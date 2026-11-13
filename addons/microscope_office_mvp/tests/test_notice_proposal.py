from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from ..services import notice_adapter
from ..services.model_client import ModelRequestError
from ..services.notice_adapter import prepare_notice
from ..services.schema import DraftValidationError
from .common import MopsCase


# The notice portion of scripts/demo_acceleration.json; simulated, not customer data.
NOTICE_LINE = '通知：物镜采购协调会调整为2026-11-18 10:00，地点二楼会议室，参加人为本次参会人员。'


@tagged('post_install', '-at_install')
class TestNoticeProposal(MopsCase):
    def _meeting(self, **extra):
        return self.env['mops.meeting'].with_user(self.manager).create({
            'name': '模拟接续会议：物镜报价与下次协调',
            'host_user_id': self.manager.id,
            'attendee_ids': [Command.set((self.staff | self.buyer).ids)],
            'project_id': self.project.id,
            'raw_content': '会议决定：当前先不下单。\n' + NOTICE_LINE,
            **extra,
        })

    def _prepare(self, meeting=None, user=None):
        meeting = meeting if meeting is not None else self._meeting()
        user = user or self.staff
        action = meeting.with_user(user).action_prepare_notice()
        self.assertEqual(action['res_model'], 'mops.notice.proposal')
        return self.env['mops.notice.proposal'].with_user(user).browse(action['res_id'])

    def test_readable_meeting_prepares_editable_notice_and_sources(self):
        meeting = self._meeting()
        proposal = self._prepare(meeting)
        self.assertEqual(proposal.owner_id, self.staff)
        self.assertEqual(proposal.meeting_id, meeting)
        self.assertEqual(proposal.name, '物镜采购协调会')
        self.assertEqual(proposal.time_text, '2026-11-18 10:00')
        self.assertEqual(proposal.location, '二楼会议室')
        self.assertEqual(proposal.recipient_ids, self.staff | self.buyer)
        self.assertIn('本次参会人员', proposal.audience_text)
        self.assertIn(NOTICE_LINE, proposal.source_excerpt)
        self.assertIn('2026-11-18 10:00', proposal.time_source)
        self.assertIn(self.staff.name, proposal.recipient_source)
        self.assertNotIn('先不下单', proposal.body)
        self.assertFalse(proposal.attention_summary)
        self.assertEqual(proposal.state, 'draft')
        self.assertFalse(proposal.with_user(self.manager).notice_id)

    def test_preparation_and_submission_do_not_create_or_publish_notice(self):
        before = self.env['mops.notice'].with_user(self.manager).search_count([])
        proposal = self._prepare()
        proposal.action_submit()
        proposal.action_submit()
        self.assertEqual(proposal.state, 'submitted')
        self.assertEqual(self.env['mops.notice'].with_user(self.manager).search_count([]), before)
        with self.assertRaises(AccessError):
            proposal.action_create_notice()
        with self.assertRaises(AccessError):
            proposal.with_user(self.staff).sudo().action_create_notice()

    def test_repeat_preparation_preserves_manual_changes_and_snapshot(self):
        meeting = self._meeting()
        proposal = self._prepare(meeting)
        original_source = proposal.source_excerpt
        proposal.write({
            'name': '人工确认的通知',
            'body': '调整为2026-11-19 11:00，地点三楼会议室。',
            'recipient_ids': [Command.set(self.staff.ids)],
        })
        meeting.write({'raw_content': '通知：改为2026-11-20 15:00，地点四楼会议室。'})
        reopened = self._prepare(meeting)
        self.assertEqual(reopened.id, proposal.id)
        self.assertEqual(reopened.name, '人工确认的通知')
        self.assertEqual(reopened.time_text, '2026-11-19 11:00')
        self.assertEqual(reopened.location, '三楼会议室')
        self.assertEqual(reopened.recipient_ids, self.staff)
        self.assertEqual(reopened.source_excerpt, original_source)

    def test_manager_creates_one_real_draft_then_preserves_notice_edits(self):
        proposal = self._prepare()
        proposal.action_submit()
        manager_proposal = proposal.with_user(self.manager)
        action = manager_proposal.action_create_notice()
        notice = self.env['mops.notice'].with_user(self.manager).browse(action['res_id'])
        self.assertEqual(action['res_model'], 'mops.notice')
        self.assertEqual(notice, manager_proposal.notice_id)
        self.assertEqual(notice.state, 'draft')
        self.assertEqual(notice.audience, 'selected')
        self.assertEqual(notice.recipient_ids, proposal.recipient_ids)
        self.assertFalse(notice.published_at)
        self.assertFalse(notice.publisher_id)
        self.assertFalse(notice.read_ids)
        self.assertEqual(proposal.state, 'converted')
        notice.write({'name': '负责人修改的正式稿', 'body': '<p>负责人核定内容</p>'})
        self.assertEqual(manager_proposal.action_create_notice()['res_id'], notice.id)
        self.assertEqual(notice.name, '负责人修改的正式稿')
        self.assertIn('负责人核定内容', notice.body)
        notice.action_publish()
        self.assertEqual(manager_proposal.action_create_notice()['res_id'], notice.id)
        self.assertEqual(notice.state, 'published')
        self.assertFalse(notice.read_ids)
        with self.assertRaises(UserError):
            proposal.write({'body': '不应覆盖正式公告'})

    def test_missing_items_are_local_and_do_not_broaden_audience(self):
        meeting = self._meeting(raw_content='通知：请准备采购协调材料。', attendee_ids=[Command.clear()])
        proposal = self._prepare(meeting, self.manager)
        self.assertFalse(proposal.time_text)
        self.assertFalse(proposal.location)
        self.assertFalse(proposal.recipient_ids)
        self.assertIn('时间：', proposal.attention_summary)
        self.assertIn('地点：', proposal.attention_summary)
        self.assertIn('接收范围：', proposal.attention_summary)
        proposal.write({'body': '请准备采购协调材料，地点二楼会议室。'})
        self.assertNotIn('地点：', proposal.attention_summary)
        action = proposal.action_create_notice()
        notice = self.env['mops.notice'].with_user(self.manager).browse(action['res_id'])
        self.assertEqual(notice.state, 'draft')
        self.assertEqual(notice.audience, 'selected')
        self.assertFalse(notice.recipient_ids)

    def test_no_notice_line_keeps_body_empty_until_human_fills_it(self):
        proposal = self._prepare(self._meeting(raw_content='会议决定：先不下单。'))
        self.assertFalse(proposal.body)
        self.assertFalse(proposal.time_text)
        self.assertIn('正文：', proposal.attention_summary)
        with self.assertRaises(UserError):
            proposal.with_user(self.manager).action_create_notice()
        proposal.write({'body': '请先核对物镜规格。'})
        self.assertTrue(proposal.with_user(self.manager).action_create_notice()['res_id'])

    def test_staff_cannot_prepare_unreadable_meeting_or_another_owner(self):
        meeting = self._meeting()
        proposal = self._prepare(meeting)
        with self.assertRaises(AccessError):
            meeting.with_user(self.outsider).action_prepare_notice()
        with self.assertRaises(AccessError):
            proposal.with_user(self.buyer).write({'body': '修改他人准备稿'})
        with self.assertRaises(AccessError):
            proposal.with_user(self.buyer).action_submit()
        with self.assertRaises(AccessError):
            self.env['mops.notice.proposal'].with_user(self.staff).create({
                'meeting_id': meeting.id, 'owner_id': self.manager.id,
            })
        for values in ({'owner_id': self.manager.id}, {'state': 'converted'},
                       {'notice_id': False}, {'meeting_id': meeting.id},
                       {'source_excerpt': '伪造原文'}, {'company_id': self.other_company.id}):
            with self.assertRaises(AccessError):
                proposal.write(values)

    def test_context_defaults_do_not_forge_workflow_or_sources(self):
        meeting = self._meeting()
        proposal = self.env['mops.notice.proposal'].with_user(self.staff).with_context(
            default_owner_id=self.manager.id, default_state='converted',
            default_notice_id=123456, default_source_excerpt='伪造原文',
        ).create({'meeting_id': meeting.id})
        self.assertEqual(proposal.owner_id, self.staff)
        self.assertEqual(proposal.state, 'draft')
        self.assertEqual(proposal.source_excerpt, NOTICE_LINE)
        self.assertFalse(proposal.with_user(self.manager).notice_id)

    def test_formal_notice_escapes_plain_text(self):
        proposal = self._prepare()
        proposal.write({'body': '<script>alert(1)</script>\n请核对安排。'})
        action = proposal.with_user(self.manager).action_create_notice()
        notice = self.env['mops.notice'].with_user(self.manager).browse(action['res_id'])
        self.assertNotIn('<script>', notice.body)
        self.assertIn('&lt;script&gt;', notice.body)

    def test_adapter_never_promotes_non_notice_decisions(self):
        values = prepare_notice('会议决定：先不下单。\n仅记录：讨论下周会期。', '采购例会')
        self.assertFalse(values['body'])
        self.assertFalse(values['time_text'])
        self.assertFalse(values['location'])
        self.assertFalse(values['source_excerpt'])

    def _natural_notice(self):
        source = '请转告本次参会人员，协调会改到下周三上午十点，在二楼会议室进行。'
        return source, {
            'name': '协调会', 'excerpts': [source],
            'time_text': '下周三上午十点', 'location': '二楼会议室',
            'audience_text': '本次参会人员', 'time_source': '改到下周三上午十点',
            'location_source': '在二楼会议室进行', 'audience_source': '请转告本次参会人员',
        }

    def test_model_extracts_natural_notice_without_expanding_recipients(self):
        source, result = self._natural_notice()
        meeting = self._meeting(raw_content=source)
        with patch.object(notice_adapter.model_client, 'model_enabled', return_value=True), \
                patch.object(notice_adapter.model_client, 'provider_label', return_value='测试模型'), \
                patch.object(notice_adapter.model_client, 'provider_key', return_value='openai:test'), \
                patch.object(notice_adapter.model_client, 'request_json', return_value=result):
            proposal = self._prepare(meeting)
        self.assertEqual(proposal.preparation_method, 'model')
        self.assertEqual(proposal.preparation_provider, '测试模型')
        self.assertEqual(proposal.body, source)
        self.assertEqual(proposal.time_text, '下周三上午十点')
        self.assertEqual(proposal.location, '二楼会议室')
        self.assertEqual(proposal.recipient_ids, self.staff | self.buyer)
        original_fingerprint = proposal.source_fingerprint
        proposal.write({'body': '人工确认：2026-11-19 10:00，地点三楼会议室。'})
        meeting.write({'raw_content': '更新的会议原文不能覆盖人工准备稿。'})
        with patch.object(notice_adapter.model_client, 'request_json', side_effect=AssertionError('重复请求')):
            reopened = self._prepare(meeting)
        self.assertEqual(reopened, proposal)
        self.assertEqual(reopened.source_fingerprint, original_fingerprint)
        self.assertEqual(reopened.source_excerpt, source)
        self.assertEqual(reopened.location, '三楼会议室')

    def test_model_rejects_invented_facts_unknown_fields_and_oversized_reply(self):
        source, result = self._natural_notice()
        invalid_results = [
            {**result, 'location': '三楼会议室'},
            {**result, 'time_source': '改为2026-12-01'},
            {**result, 'audience_text': '全体员工'},
            {**result, 'recipient_ids': [self.manager.id]},
            {**result, 'excerpts': [source + '交通费由公司报销。']},
            {**result, 'name': '协' * 201},
            {**result, 'time_text': 123},
        ]
        for invalid in invalid_results:
            with self.subTest(fields=list(invalid)), \
                    patch.object(notice_adapter.model_client, 'model_enabled', return_value=True), \
                    patch.object(notice_adapter.model_client, 'request_json', return_value=invalid):
                with self.assertRaises(DraftValidationError):
                    prepare_notice(source, '例会')

    def test_model_failure_retains_source_and_staff_manual_path(self):
        source, _result = self._natural_notice()
        for message in ('模型服务拒绝请求（HTTP 403）。', '模型整理超时。'):
            with self.subTest(error=message):
                meeting = self._meeting(raw_content=source)
                with patch.object(notice_adapter.model_client, 'model_enabled', return_value=True), \
                        patch.object(notice_adapter.model_client, 'provider_label', return_value='测试模型'), \
                        patch.object(notice_adapter.model_client, 'provider_key', return_value='openai:test'), \
                        patch.object(notice_adapter.model_client, 'request_json', side_effect=ModelRequestError(message)):
                    proposal = self._prepare(meeting)
                self.assertEqual(proposal.preparation_method, 'failed')
                self.assertEqual(proposal.preparation_error, message)
                self.assertEqual(proposal.preparation_provider, '尝试方式：测试模型')
                self.assertEqual(proposal.source_excerpt, source)
                self.assertFalse(proposal.body)
                self.assertFalse(proposal.time_source)
                with self.assertRaises(AccessError):
                    proposal.write({'preparation_method': 'model'})
                proposal.write({'body': source, 'recipient_ids': [Command.set(self.staff.ids)]})
                with patch.object(notice_adapter.model_client, 'request_json', side_effect=AssertionError('重复请求')):
                    self.assertEqual(self._prepare(meeting).id, proposal.id)
                proposal.action_submit()
                with self.assertRaises(AccessError):
                    proposal.action_create_notice()
                action = proposal.with_user(self.manager).action_create_notice()
                notice = self.env['mops.notice'].with_user(self.manager).browse(action['res_id'])
                self.assertEqual(notice.state, 'draft')
                self.assertEqual(notice.recipient_ids, self.staff)
                self.assertEqual(proposal.preparation_method, 'failed')

    def test_invalid_model_result_keeps_editable_preparation(self):
        source, result = self._natural_notice()
        with patch.object(notice_adapter.model_client, 'model_enabled', return_value=True), \
                patch.object(notice_adapter.model_client, 'request_json', return_value={**result, 'location': '无依据地点'}):
            proposal = self._prepare(self._meeting(raw_content=source))
        self.assertEqual(proposal.preparation_method, 'failed')
        self.assertEqual(proposal.source_excerpt, source)
        self.assertFalse(proposal.body)
        proposal.write({'body': source})
        self.assertEqual(proposal.body, source)
