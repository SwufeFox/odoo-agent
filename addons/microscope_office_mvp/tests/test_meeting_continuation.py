import json
from pathlib import Path
from copy import deepcopy
from unittest.mock import Mock, patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import new_test_user

from ..services import model_client
from ..services.meeting_continuation import parse_continuation, validate_continuation
from ..services.schema import DraftValidationError
from .common import MopsCase


@tagged('post_install', '-at_install')
class TestMeetingContinuation(MopsCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.scene = json.loads((Path(__file__).resolve().parents[3] / 'scripts' / 'demo_acceleration.json').read_text(encoding='utf-8'))
        cls.buyer.name = '采购小陈（模拟）'
        cls.staff.name = '研发小林（模拟）'

    def meeting(self, raw=None, actor=None, **extra):
        return self.env['mops.meeting'].with_user(actor or self.manager).create(dict({
            'name': self.scene['meeting_title'], 'project_id': self.project.id,
            'attendee_ids': [Command.set((self.buyer | self.staff).ids)],
            'raw_content': raw if raw is not None else self.scene['meeting_raw'],
        }, **extra))

    def task(self, name=None, **extra):
        return self.env['project.task'].with_user(self.manager).with_context(
            mail_auto_subscribe_no_notify=True, tracking_disable=True,
        ).create(dict({
            'name': name or self.scene['existing_task'],
            'project_id': self.project.id,
            'company_id': self.env.company.id,
            'user_ids': [Command.set(self.buyer.ids)],
            'description': '<p>已收到报价，待补齐条件。</p>',
        }, **extra))

    def test_shared_scene_updates_existing_creates_research_and_retains_decision(self):
        existing = self.task()
        meeting = self.meeting()
        meeting.action_prepare_continuation()
        update = meeting.action_ids.filtered(lambda line: line.action_kind == 'update')
        create = meeting.action_ids.filtered(lambda line: line.action_kind == 'create')
        records = meeting.action_ids.filtered(lambda line: line.action_kind == 'record')
        self.assertEqual(len(update), 1)
        self.assertEqual(len(create), 1)
        self.assertEqual(len(records), 2)
        self.assertEqual(update.target_task_id, existing)
        self.assertEqual(update.assignee_user_id, self.buyer)
        self.assertEqual(create.assignee_user_id, self.staff)
        self.assertEqual(str(create.deadline), '2026-11-18')
        self.assertTrue(all(meeting.action_ids.mapped('selected')))
        self.assertFalse(any(meeting.action_ids.mapped('attention_summary')))
        self.assertTrue(any('先不下单' in line.name for line in records))
        self.assertFalse(any('通知' in line.name for line in meeting.action_ids))

        meeting.action_apply_continuation()
        self.assertEqual(meeting.task_count, 2)
        self.assertEqual(len(meeting.action_ids.generated_task_id), 1)
        self.assertEqual(create.generated_task_id.user_ids, self.staff)
        self.assertIn('补问精密光学含税运费。', str(existing.description))
        self.assertIn('已收到报价', str(existing.description))
        self.assertEqual(fields.Datetime.context_timestamp(existing, existing.date_deadline).date(), update.deadline)
        self.assertEqual(existing.mops_meeting_id, meeting)
        self.assertIn(update, existing.mops_continuation_action_ids)
        self.assertFalse(records.generated_task_id)
        self.assertFalse(records.target_task_id)
        self.assertTrue(all(meeting.action_ids.mapped('applied')))
        ids = meeting.action_ids.ids
        description = existing.description
        meeting.action_prepare_continuation()
        meeting.action_apply_continuation()
        self.assertEqual(meeting.action_ids.ids, ids)
        self.assertEqual(existing.description, description)
        self.assertEqual(meeting.task_count, 2)
        existing.write({'description': '<p>任务负责人已人工更新进展。</p>'})
        meeting.action_apply_continuation()
        self.assertIn('人工更新进展', str(existing.description))
        with self.assertRaises(UserError):
            update.write({'update_note': '覆盖已采纳结果'})
        with self.assertRaises(UserError):
            meeting.unlink()
        meeting.action_confirm_tasks()
        self.assertEqual(meeting.state, 'confirmed')

    def test_reextract_preserves_edits_even_after_source_date_and_progress_change(self):
        existing = self.task()
        meeting = self.meeting()
        meeting.action_prepare_continuation()
        update = meeting.action_ids.filtered(lambda line: line.action_kind == 'update')
        update.write({'name': '人工核对后的跟进标题', 'update_note': '只补问包装与运费',
                      'deadline': '2026-11-21', 'selected': False})
        ids = meeting.action_ids.ids
        meeting.write({'raw_content': meeting.raw_content.replace('2026-11-17', '2026-11-22').replace(
            '补问精密光学含税运费。', '新的材料进展。')})
        meeting.action_prepare_continuation()
        self.assertEqual(meeting.action_ids.ids, ids)
        self.assertEqual(update.target_task_id, existing)
        self.assertEqual(update.name, '人工核对后的跟进标题')
        self.assertEqual(update.update_note, '只补问包装与运费')
        self.assertEqual(str(update.deadline), '2026-11-21')
        self.assertFalse(update.selected)

    def test_ambiguous_rows_stay_local_and_no_date_does_not_block(self):
        self.buyer.name = '同名同事（采购）'
        self.staff.name = '同名同事（研发）'
        self.task(name='重复任务')
        self.task(name='重复任务')
        meeting = self.meeting(raw=(
            '行动项：明确但没有期限 | 负责人：' + self.manager.name + '\n'
            '行动项：同名负责人事项 | 负责人：同名同事 | 截止：下周五\n'
            '更新任务：重复任务\n'
            '仅记录：先不下单。'
        ))
        meeting.action_prepare_continuation()
        clear = meeting.action_ids.filtered(lambda line: line.name == '明确但没有期限')
        unclear = meeting.action_ids.filtered(lambda line: line.name == '同名负责人事项')
        duplicate = meeting.action_ids.filtered(lambda line: line.name == '重复任务')
        self.assertTrue(clear.selected)
        self.assertFalse(clear.deadline)
        self.assertFalse(clear.attention_summary)
        self.assertFalse(unclear.selected)
        self.assertFalse(unclear.assignee_user_id)
        self.assertTrue(unclear.attention_summary)
        self.assertFalse(duplicate.target_task_id)
        self.assertFalse(duplicate.selected)
        meeting.action_apply_continuation()
        self.assertTrue(clear.generated_task_id)
        self.assertFalse(clear.generated_task_id.date_deadline)
        self.assertFalse(unclear.applied)
        self.assertFalse(duplicate.applied)
        unclear.write({'selected': True, 'assignee_user_id': self.buyer.id})
        meeting.action_apply_continuation()
        self.assertTrue(unclear.generated_task_id)
        self.assertFalse(unclear.generated_task_id.date_deadline)

    def test_unreadable_tasks_are_not_matched_and_company_guard_remains(self):
        hidden = self.task()
        meeting = self.meeting(actor=self.staff, raw='更新任务：' + hidden.name)
        meeting.action_prepare_continuation()
        line = meeting.action_ids
        self.assertFalse(line.target_task_id)
        self.assertFalse(line.selected)
        with self.assertRaises(AccessError), self.cr.savepoint():
            line.write({'target_task_id': hidden.id})
        with self.assertRaises(AccessError), self.cr.savepoint():
            self.env['mops.meeting.action'].with_user(self.staff).with_context(
                default_target_task_id=hidden.id, default_action_kind='update',
            ).create({'meeting_id': meeting.id, 'name': '越权接续'})
        other_project = self.env['project.project'].create({
            'name': '其他公司项目', 'company_id': self.other_company.id,
        })
        other_task = self.env['project.task'].create({
            'name': '其他公司任务', 'project_id': other_project.id,
            'company_id': self.other_company.id,
        })
        with self.assertRaises((AccessError, ValidationError)), self.cr.savepoint():
            line.with_user(self.manager).write({'target_task_id': other_task.id})
        for method in ('action_prepare_continuation', 'action_apply_continuation'):
            with self.assertRaises(AccessError):
                getattr(meeting.with_user(self.buyer), method)()

    def test_continuation_preserves_generated_task_protection_and_multiple_sources(self):
        first = self.meeting(raw='行动项：复用原会议任务 | 负责人：采购小陈')
        first.action_prepare_continuation()
        first.action_apply_continuation()
        task = first.action_ids.generated_task_id
        second = self.meeting(raw='更新任务：复用原会议任务 | 负责人：采购小陈 | 进展：已补问运费。')
        second.action_prepare_continuation()
        self.assertEqual(second.action_ids.target_task_id, task)
        second.action_apply_continuation()
        self.assertEqual(task.mops_meeting_id, first)
        self.assertEqual(task.mops_continuation_action_ids, first.action_ids | second.action_ids)
        self.assertEqual(second.task_count, 1)
        with self.assertRaises(UserError):
            task.write({'user_ids': [Command.set(self.staff.ids)]})
        project_user = new_test_user(self.env, login='mops_continuation_project_only', groups='project.group_project_user')
        private_view = task.with_user(project_user)
        self.assertFalse(private_view.mops_meeting_id)
        with self.assertRaises(AccessError):
            private_view.read(['mops_continuation_action_ids'])
        self.assertNotIn(second.raw_content, str(private_view.description))

    def test_records_need_no_project_and_bad_input_keeps_manual_path(self):
        meeting = self.meeting(raw='行动项：先不下单，等规格确认。', project_id=False)
        meeting.action_prepare_continuation()
        self.assertEqual(meeting.action_ids.action_kind, 'record')
        meeting.action_apply_continuation()
        self.assertTrue(meeting.action_ids.applied)
        self.assertFalse(meeting.action_ids.generated_task_id)
        meeting.write({'raw_content': '无法识别的普通讨论'})
        meeting.action_prepare_continuation()
        self.assertTrue(meeting.ai_error)
        self.assertEqual(len(meeting.action_ids), 1)
        self.assertTrue(meeting.action_ids.applied)

    def test_parser_rejects_invalid_dates_without_inventing_values(self):
        items = parse_continuation('行动项：核对规格 | 截止：2026-02-30\n通知：明天开会。')
        self.assertEqual(len(items), 1)
        self.assertFalse(items[0]['deadline'])
        self.assertEqual(items[0]['suggested_deadline'], '2026-02-30')
        with self.assertRaises(DraftValidationError):
            parse_continuation('')

    def test_model_schema_rejects_identifiers_unknown_targets_and_invented_evidence(self):
        raw = '采购小陈下周五补问运费。'
        payload = {
            'summary': '', 'notices': [],
            'actions': [{
                'name': '补问运费', 'action_kind': 'update', 'source_excerpt': raw,
                'suggested_assignee_name': '采购小陈', 'suggested_deadline': '下周五',
                'update_note': '', 'target_ref': 'candidate-1', 'ambiguous': False,
            }],
        }
        candidates = [{'ref': 'candidate-1', 'name': '供应商交期跟进'}]
        row = validate_continuation(payload, raw, candidates)['actions'][0]
        self.assertFalse(row['deadline'])
        self.assertEqual(row['suggested_deadline'], '下周五')
        for change in ({'target_task_id': 12}, {'target_ref': 'candidate-999'},
                       {'source_excerpt': '没有出现在原文的句子'},
                       {'suggested_assignee_name': '研发小林'}, {'suggested_deadline': '2026-11-20'},
                       {'ambiguous': 'false'}, {'update_note': '已经完成采购'}):
            with self.subTest(change=change), self.assertRaises(DraftValidationError):
                invalid = deepcopy(payload)
                invalid['actions'][0].update(change)
                validate_continuation(invalid, raw, candidates)
        payload['notices'] = [{'title': '通知', 'audience': '全部员工', 'timing': '', 'source_excerpt': raw}]
        with self.assertRaises(DraftValidationError):
            validate_continuation(payload, raw, candidates)

    def test_model_negative_decision_cannot_become_purchase_task(self):
        raw = '决定不下单，等规格确认。'
        payload = {'summary': '', 'notices': [], 'actions': [{
            'name': '下单采购', 'action_kind': 'update', 'source_excerpt': raw,
            'suggested_assignee_name': '', 'suggested_deadline': '', 'update_note': '',
            'target_ref': 'candidate-1', 'ambiguous': False,
        }]}
        row = validate_continuation(payload, raw, [{'ref': 'candidate-1', 'name': '采购'}])['actions'][0]
        self.assertEqual(row['action_kind'], 'record')
        self.assertFalse(row['target_ref'])

    def test_natural_model_continuation_reuses_source_and_preserves_row_provenance(self):
        existing = self.task(name='供应商交期跟进')
        raw = '采购小陈继续补问运费。研发小林下周五核对规格。先不下单。通知：下周五向参会人员同步规格。'
        meeting = self.meeting(raw=raw)

        def answer(instruction, data):
            candidates = data['task_candidates']
            self.assertTrue(all(set(candidate) == {'ref', 'name'} for candidate in candidates))
            target = next(candidate['ref'] for candidate in candidates if candidate['name'] == existing.name)
            return {'summary': '接续运费与规格核对，采购暂缓。', 'actions': [
                {'name': '补问运费', 'action_kind': 'update', 'source_excerpt': '采购小陈继续补问运费。',
                 'suggested_assignee_name': '采购小陈', 'suggested_deadline': '',
                 'update_note': '继续补问运费', 'target_ref': target, 'ambiguous': False},
                {'name': '核对规格', 'action_kind': 'create', 'source_excerpt': '研发小林下周五核对规格。',
                 'suggested_assignee_name': '研发小林', 'suggested_deadline': '下周五',
                 'update_note': '', 'target_ref': '', 'ambiguous': False},
                {'name': '先不下单', 'action_kind': 'record', 'source_excerpt': '先不下单。',
                 'suggested_assignee_name': '', 'suggested_deadline': '',
                 'update_note': '', 'target_ref': '', 'ambiguous': False},
            ], 'notices': [{'title': '同步规格', 'audience': '参会人员', 'timing': '下周五',
                            'source_excerpt': '通知：下周五向参会人员同步规格。'}]}

        with patch.multiple(model_client, model_enabled=Mock(return_value=True),
                            provider_label=Mock(return_value='模型A'), provider_key=Mock(return_value='model-a'),
                            request_json=Mock(side_effect=answer)):
            meeting.action_prepare_continuation()
            update = meeting.action_ids.filtered(lambda row: row.action_kind == 'update')
            create = meeting.action_ids.filtered(lambda row: row.action_kind == 'create')
            self.assertEqual(update.target_task_id, existing)
            self.assertEqual(update.assignee_user_id, self.buyer)
            self.assertFalse(create.selected)
            self.assertTrue(create.attention_summary)
            self.assertFalse(create.deadline)
            self.assertIn('参会人员', meeting.notice_candidates)
            self.assertEqual(len(meeting.action_ids), 3)
            create.write({'name': '人工核对规格', 'selected': False})
            meeting.action_apply_continuation()
            ids = meeting.action_ids.ids
            description = existing.description
            meeting.action_prepare_continuation()
            self.assertEqual(model_client.request_json.call_count, 1)
            self.assertEqual(meeting.action_ids.ids, ids)
            self.assertEqual(create.name, '人工核对规格')
            model_client.provider_label.return_value = '模型B'
            model_client.provider_key.return_value = 'model-b'
            meeting.action_prepare_continuation()
            self.assertEqual(model_client.request_json.call_count, 2)
            self.assertEqual(meeting.action_ids.ids, ids)
            self.assertEqual(set(meeting.action_ids.mapped('extraction_provider')), {'模型A'})
            self.assertEqual(meeting.extraction_provider, '模型B')
            model_client.provider_key.return_value = 'model-a'
            model_client.provider_label.return_value = '模型A'
            meeting.action_prepare_continuation()
            self.assertEqual(model_client.request_json.call_count, 2)
            meeting.action_apply_continuation()
            self.assertEqual(existing.description, description)

    def test_model_failure_retains_local_rows_and_manual_continuation(self):
        meeting = self.meeting(raw='行动项：核对规格 | 负责人：研发小林')
        meeting.action_prepare_continuation()
        original = meeting.action_ids
        original.write({'name': '人工保留的规格核对', 'selected': False})
        provider = meeting.extraction_provider
        summary = meeting.ai_summary
        with patch.multiple(model_client, model_enabled=Mock(return_value=True),
                            provider_label=Mock(return_value='模型A'), provider_key=Mock(return_value='model-a'),
                            request_json=Mock(side_effect=model_client.ModelRequestError('模型请求失败（HTTP 403）'))):
            meeting.action_prepare_continuation()
        self.assertIn('403', meeting.ai_error)
        self.assertEqual(meeting.action_ids, original)
        self.assertEqual(original.name, '人工保留的规格核对')
        self.assertEqual(meeting.extraction_provider, provider)
        self.assertEqual(meeting.ai_summary, summary)
        manual = self.env['mops.meeting.action'].with_user(self.manager).create({
            'meeting_id': meeting.id, 'name': '人工记录决定', 'action_kind': 'record', 'selected': True,
        })
        meeting.action_apply_continuation()
        self.assertTrue(manual.applied)
        self.assertFalse(original.applied)
