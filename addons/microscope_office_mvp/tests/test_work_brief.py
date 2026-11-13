import json
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import MopsCase


@tagged('post_install', '-at_install')
class TestWorkBrief(MopsCase):
    def _meeting(self, **values):
        return self.env['mops.meeting'].with_user(self.manager).create({
            'name': '模拟物镜采购协调',
            'host_user_id': self.manager.id,
            'attendee_ids': [Command.set([self.staff.id, self.buyer.id])],
            'project_id': self.project.id,
            **values,
        })

    def _pack(self):
        return self.env['mops.quote.pack'].with_user(self.buyer).create({
            'name': '商业准备包专属标识',
            'product_id': self.product.id,
            'line_ids': [Command.create({
                'vendor_name': '模拟供应商·精密光学',
                'raw_text': '60×物镜报价（模拟材料）\n运费未注明。',
            })],
        })

    def _cards(self, user=None):
        return self.env['mops.work.brief'].with_user(user or self.manager).get_cards()

    def test_live_quote_gap_and_retained_meeting_decision(self):
        pack = self._pack()
        meeting = self._meeting(quote_pack_id=pack.id)
        task = self.env['project.task'].with_user(self.manager).create({
            'name': '核对60×物镜接口与数值孔径',
            'project_id': self.project.id,
            'company_id': self.env.company.id,
            'user_ids': [Command.set([self.staff.id])],
        })
        self.env['mops.meeting.action'].with_user(self.manager).create([
            {
                'meeting_id': meeting.id,
                'name': task.name,
                'action_kind': 'update',
                'target_task_id': task.id,
                'update_note': '核对NA 1.35与目标NA 1.40的差异。',
                'selected': True,
            },
            {
                'meeting_id': meeting.id,
                'name': '先不下单，待运费与规格确认',
                'action_kind': 'record',
                'selected': True,
            },
        ])
        meeting.action_apply_continuation()
        quote_key = 'mops.quote.pack:%s' % pack.id
        before = {card['id']: card for card in self._cards()}
        self.assertIn('运费', before[quote_key]['reason'])
        self.assertIn(task.name, before[quote_key]['reason'])
        self.assertIn('先不下单', before[quote_key]['reason'])
        self.assertEqual(before[quote_key]['action']['res_id'], pack.id)

        # This is the real editable quote field, not a cached brief snapshot.
        pack.line_ids.write({'freight_text': '120元，含税'})
        after = {card['id']: card for card in self._cards()}
        self.assertNotIn('缺少运费', after[quote_key]['reason'])
        self.assertIn('先不下单', after[quote_key]['reason'])
        self.assertEqual(task.id, meeting.action_ids.filtered(
            lambda line: line.action_kind == 'update').target_task_id.id)
        self.assertIn('project.task:%s' % task.id, after)
        self.assertNotIn('已到货', json.dumps(list(after.values()), ensure_ascii=False))
        self.assertNotIn('已付款', json.dumps(list(after.values()), ensure_ascii=False))

    def test_employee_cards_never_read_quote_commercial_data(self):
        pack = self._pack()
        meeting = self._meeting(quote_pack_id=pack.id)
        self.env['mops.meeting.action'].with_user(self.manager).create({
            'meeting_id': meeting.id,
            'name': '先不下单，待确认',
            'action_kind': 'record',
        })
        cards = self._cards(self.staff)
        self.assertTrue(any(card['id'] == 'mops.meeting:%s' % meeting.id for card in cards))
        self.assertFalse(any(card['action']['res_model'] == 'mops.quote.pack' for card in cards))
        self.assertNotIn(pack.name, json.dumps(cards, ensure_ascii=False))
        self.assertNotIn('模拟供应商·精密光学', json.dumps(cards, ensure_ascii=False))
        self.assertFalse(any(card['id'] == 'mops.meeting:%s' % meeting.id
                             for card in self._cards(self.outsider)))
        with self.assertRaises(AccessError):
            self._cards(self.env.ref('base.public_user'))

    def test_notice_preparation_disappears_after_real_conversion(self):
        proposal = self.env['mops.notice.proposal'].with_user(self.manager).create({
            'meeting_id': self._meeting().id,
            'name': '模拟协调会调整通知',
            'body': '协调会调整为2026-11-18 10:00，地点二楼会议室。',
            'recipient_ids': [Command.set([self.staff.id])],
        })
        key = 'mops.notice.proposal:%s' % proposal.id
        before = {card['id']: card for card in self._cards()}
        self.assertIn(key, before)
        self.assertEqual(before[key]['action']['res_model'], 'mops.notice.proposal')
        proposal.action_create_notice()
        self.assertEqual(proposal.notice_id.state, 'draft')
        self.assertNotIn(key, {card['id'] for card in self._cards()})

    def test_small_actionable_cards_follow_task_completion(self):
        task = self.env['project.task'].with_user(self.manager).create({
            'name': '模拟逾期待跟进任务',
            'project_id': self.project.id,
            'company_id': self.env.company.id,
            'user_ids': [Command.set([self.staff.id])],
            'date_deadline': fields.Datetime.now() - timedelta(days=1),
        })
        cards = self._cards(self.staff)
        card = next(card for card in cards if card['id'] == 'project.task:%s' % task.id)
        self.assertEqual(card['priority'], 'warning')
        self.assertIn('截止时间已过', card['reason'])
        self.assertLessEqual(len(cards), 6)
        expected_keys = {'id', 'title', 'status', 'reason', 'next_label', 'action', 'source', 'priority'}
        for card in cards:
            self.assertEqual(set(card), expected_keys)
            self.assertIsInstance(card['source'], str)
            self.assertEqual(card['action']['type'], 'ir.actions.act_window')
            self.env[card['action']['res_model']].with_user(self.staff).browse(
                card['action']['res_id']).check_access('read')
        task.write({'state': '1_done'})
        self.assertNotIn('project.task:%s' % task.id, {card['id'] for card in self._cards(self.staff)})
