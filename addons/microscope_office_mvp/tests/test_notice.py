from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .common import MopsCase


@tagged('post_install', '-at_install')
class TestNotice(MopsCase):
    def _draft(self, **values):
        vals = {
            'name': '内部工作安排',
            'body': '<p>请按公告安排完成工作。</p>',
            'company_id': self.env.company.id,
        }
        vals.update(values)
        return self.env['mops.notice'].with_user(self.manager).create(vals)

    def _receipt_model(self, user=None):
        return self.env['mops.notice.read'].with_user(user or self.staff)

    def test_manager_publication_and_staff_audience(self):
        draft = self._draft(name='尚未发布')
        selected = self._draft(
            audience='selected', recipient_ids=[Command.set(self.staff.ids)],
        )
        everyone = self._draft(name='全体员工公告')
        self.assertTrue(selected.action_publish())
        self.assertTrue(everyone.action_publish())
        self.assertEqual(selected.state, 'published')
        self.assertEqual(selected.publisher_id, self.manager)
        self.assertTrue(selected.published_at)
        self.assertIn('请按公告安排', selected.with_user(self.staff).read(['body'])[0]['body'])
        self.assertTrue(everyone.with_user(self.outsider).read(['body']))
        visible = self.env['mops.notice'].with_user(self.outsider).search([
            ('id', 'in', (draft + selected + everyone).ids),
        ])
        self.assertEqual(visible.ids, everyone.ids)
        for hidden in (draft, selected):
            with self.assertRaises(AccessError):
                hidden.with_user(self.outsider).read(['name', 'body'])
        with self.assertRaises(AccessError):
            draft.with_user(self.staff).read(['body'])
        selected.action_archive()
        self.assertEqual(selected.state, 'archived')
        with self.assertRaises(AccessError):
            selected.with_user(self.staff).read(['body'])
        self.assertTrue(selected.with_user(self.manager).read(['body']))

    def test_staff_cannot_create_edit_delete_or_publish(self):
        draft = self._draft()
        published = self._draft()
        published.action_publish()
        for user in (self.staff, self.buyer):
            with self.assertRaises(AccessError):
                self.env['mops.notice'].with_user(user).create({
                    'name': '越权公告', 'body': '<p>禁止创建</p>',
                })
            for notice in (draft, published):
                with self.assertRaises(AccessError):
                    notice.with_user(user).write({'name': '越权编辑'})
                with self.assertRaises(AccessError):
                    notice.with_user(user).unlink()
                with self.assertRaises(AccessError):
                    notice.with_user(user).action_publish()
                with self.assertRaises(AccessError):
                    notice.with_user(user).action_archive()

    def test_manager_direct_metadata_and_state_forgery_rejected(self):
        draft = self._draft()
        model = self.env['mops.notice'].with_user(self.manager)
        for forged in (
            {'state': 'published'},
            {'state': 'archived'},
            {'publisher_id': self.staff.id},
            {'published_at': '2000-01-01 00:00:00'},
            {'create_uid': self.staff.id},
            {'write_date': '2000-01-01 00:00:00'},
        ):
            with self.assertRaises(AccessError):
                model.create({'name': '伪造发布信息', 'body': '<p>正文</p>', **forged})
            with self.assertRaises(AccessError):
                draft.write(forged)
        with self.assertRaises(AccessError):
            draft.write({'state': 'draft'})
        draft.write({'name': '负责人已修改草稿', 'body': '<p>更新后的草稿。</p>'})
        self.assertEqual(draft.name, '负责人已修改草稿')
        self.assertTrue(draft.unlink())

    def test_context_cannot_forge_publication_or_receipt_metadata(self):
        notice = self.env['mops.notice'].with_user(self.manager).with_context(
            default_state='published',
            default_publisher_id=self.staff.id,
            default_published_at='2000-01-01 00:00:00',
        ).create({'name': '上下文不是发布凭据', 'body': '<p>草稿。</p>'})
        self.assertEqual(notice.state, 'draft')
        self.assertFalse(notice.publisher_id)
        self.assertFalse(notice.published_at)
        with self.assertRaises(AccessError):
            notice.with_user(self.staff).with_context(
                bypass_security=True, mops_internal=True, user_id=self.manager.id,
            ).action_publish()
        notice.action_publish()
        before = fields.Datetime.now()
        receipt = self._receipt_model().with_context(
            default_user_id=self.outsider.id,
            default_read_at='2000-01-01 00:00:00',
            default_company_id=self.other_company.id,
            default_create_uid=self.manager.id,
            default_create_date='2000-01-01 00:00:00',
        ).create({'notice_id': notice.id})
        self.assertEqual(receipt.user_id, self.staff)
        self.assertEqual(receipt.company_id, notice.company_id)
        self.assertEqual(receipt.create_uid, self.staff)
        self.assertNotEqual(receipt.create_date, fields.Datetime.to_datetime('2000-01-01 00:00:00'))
        self.assertGreaterEqual(receipt.read_at, before)
        self.assertLessEqual(receipt.read_at, fields.Datetime.now())

    def test_published_body_audience_and_company_are_immutable(self):
        notice = self._draft(
            audience='selected', recipient_ids=[Command.set(self.staff.ids)],
        )
        notice.action_publish()
        publisher, published_at = notice.publisher_id, notice.published_at
        changes = (
            {'body': '<p>替换已发布内容</p>'},
            {'audience': 'all'},
            {'recipient_ids': [Command.set(self.outsider.ids)]},
            {'company_id': notice.company_id.id},
        )
        for vals in changes:
            with self.assertRaises(UserError):
                notice.write(vals)
        with self.assertRaises(UserError):
            notice.action_publish()
        notice.action_archive()
        for vals in changes:
            with self.assertRaises(UserError):
                notice.write(vals)
        with self.assertRaises(UserError):
            notice.action_publish()
        with self.assertRaises(UserError):
            notice.action_archive()
        self.assertEqual(notice.publisher_id, publisher)
        self.assertEqual(notice.published_at, published_at)

    def test_selected_notice_requires_recipients_before_publication(self):
        notice = self._draft(audience='selected')
        with self.assertRaises(ValidationError):
            notice.action_publish()
        self.assertEqual(notice.state, 'draft')
        notice.write({'recipient_ids': [Command.set(self.staff.ids)]})
        notice.action_publish()
        self.assertEqual(notice.state, 'published')
        with self.assertRaises(UserError):
            self._draft().action_archive()

    def test_receipts_only_for_actor_visible_published_notice(self):
        draft = self._draft()
        selected = self._draft(
            audience='selected', recipient_ids=[Command.set(self.staff.ids)],
        )
        selected.action_publish()
        with self.assertRaises(AccessError):
            self._receipt_model(self.manager).create({
                'notice_id': selected.id, 'user_id': self.staff.id,
            })
        model = self._receipt_model()
        for notice in (draft,):
            with self.assertRaises(AccessError):
                model.create({'notice_id': notice.id})
            with self.assertRaises(AccessError):
                notice.with_user(self.staff).action_mark_read()
        with self.assertRaises(AccessError):
            self._receipt_model(self.manager).create({'notice_id': draft.id})
        with self.assertRaises(AccessError):
            self._receipt_model(self.outsider).create({'notice_id': selected.id})
        with self.assertRaises(AccessError):
            selected.with_user(self.outsider).action_mark_read()
        for forged in (
            {'user_id': self.outsider.id},
            {'read_at': '2000-01-01 00:00:00'},
            {'company_id': self.other_company.id},
            {'create_uid': self.manager.id},
            {'create_date': '2000-01-01 00:00:00'},
            {'write_uid': self.manager.id},
        ):
            with self.assertRaises(AccessError):
                model.create({'notice_id': selected.id, **forged})
        receipt = model.create({'notice_id': selected.id, 'user_id': self.staff.id})
        self.assertEqual(receipt.user_id, self.staff)
        self.assertEqual(receipt.company_id, selected.company_id)
        self.assertEqual(receipt.notice_id, selected)
        with self.assertRaises(AccessError):
            receipt.with_user(self.outsider).read(['notice_id'])
        self.assertTrue(receipt.with_user(self.manager).read(['user_id', 'read_at']))
        selected.action_archive()
        with self.assertRaises(AccessError):
            model.create({'notice_id': selected.id})
        with self.assertRaises(AccessError):
            selected.with_user(self.staff).action_mark_read()
        with self.assertRaises(AccessError):
            receipt.with_user(self.staff).read(['notice_id'])

    def test_receipts_are_immutable_and_duplicate_reads_idempotent(self):
        notice = self._draft()
        notice.action_publish()
        model = self._receipt_model()
        self.assertTrue(notice.with_user(self.staff).action_mark_read())
        receipt = model.search([('notice_id', '=', notice.id)])
        self.assertEqual(len(receipt), 1)
        read_at = receipt.read_at
        self.assertTrue(notice.with_user(self.staff).action_mark_read())
        self.assertEqual(model.create({'notice_id': notice.id}), receipt)
        batch = model.create([{'notice_id': notice.id}, {'notice_id': notice.id}])
        self.assertEqual(batch.ids, [receipt.id, receipt.id])
        self.assertEqual(model.search_count([('notice_id', '=', notice.id)]), 1)
        self.assertEqual(receipt.read_at, read_at)
        for user in (self.staff, self.manager, self.admin_user):
            for vals in (
                {'user_id': self.outsider.id},
                {'read_at': '2000-01-01 00:00:00'},
                {'notice_id': self._draft().id},
                {'company_id': self.other_company.id},
            ):
                with self.assertRaises(AccessError):
                    receipt.with_user(user).write(vals)
            with self.assertRaises(AccessError):
                receipt.with_user(user).unlink()
        with self.assertRaises(UserError):
            notice.unlink()
        notice.action_archive()
        self.assertTrue(receipt.with_user(self.manager).read(['read_at']))

    def test_company_guards_reject_foreign_records_and_context(self):
        notice = self._draft()
        with self.assertRaises(AccessError):
            self._draft(company_id=self.other_company.id)
        with self.assertRaises(AccessError):
            notice.write({'company_id': self.other_company.id})
        with self.assertRaises(AccessError):
            self.env['mops.notice'].with_user(self.manager).with_context(
                allowed_company_ids=self.other_company.ids,
            ).create({'name': '越权公司', 'body': '<p>禁止。</p>'})
        original_companies = self.manager.company_ids
        self.manager.write({'company_ids': [Command.link(self.other_company.id)]})
        foreign = self.env['mops.notice'].with_user(self.manager).with_company(
            self.other_company,
        ).create({
            'name': '其他公司的公告', 'body': '<p>其他公司内部内容。</p>',
            'company_id': self.other_company.id,
        })
        foreign.action_publish()
        self.manager.write({'company_ids': [Command.set(original_companies.ids)]})
        for user in (self.staff, self.manager):
            hidden = foreign.with_user(user).with_context(allowed_company_ids=self.env.company.ids)
            with self.assertRaises(AccessError):
                hidden.read(['name', 'body'])
            with self.assertRaises(AccessError):
                hidden.action_mark_read()
            with self.assertRaises(AccessError):
                self._receipt_model(user).create({'notice_id': foreign.id})
        hidden = foreign.with_user(self.manager).with_context(allowed_company_ids=self.env.company.ids)
        with self.assertRaises(AccessError):
            hidden.write({'name': '跨公司修改'})
        with self.assertRaises(AccessError):
            hidden.action_archive()
        with self.assertRaises(AccessError):
            hidden.unlink()

    def test_recipients_must_be_company_compatible(self):
        self.outsider.write({
            'company_id': self.other_company.id,
            'company_ids': [Command.set(self.other_company.ids)],
        })
        with self.assertRaises(UserError):
            self._draft(audience='selected', recipient_ids=[Command.set(self.outsider.ids)])

    def test_read_status_and_unread_filter_are_personal(self):
        notice = self._draft()
        notice.action_publish()
        staff_notice = notice.with_user(self.staff)
        buyer_notice = notice.with_user(self.buyer)
        self.assertFalse(staff_notice.is_read)
        self.assertFalse(buyer_notice.is_read)
        self.assertEqual(notice.read_count, 0)
        staff_notice.action_mark_read()
        self.assertTrue(staff_notice.is_read)
        self.assertTrue(staff_notice.my_read_at)
        self.assertFalse(buyer_notice.is_read)
        self.assertEqual(notice.read_count, 1)
        unread = self.env['mops.notice'].with_user(self.staff).search([
            ('state', '=', 'published'), ('read_ids', 'not any', [('user_id', '=', self.staff.id)]),
        ])
        self.assertNotIn(notice, unread)
        buyer_unread = self.env['mops.notice'].with_user(self.buyer).search([
            ('state', '=', 'published'), ('read_ids', 'not any', [('user_id', '=', self.buyer.id)]),
        ])
        self.assertIn(notice, buyer_unread)
        with self.assertRaises(AccessError):
            staff_notice.read(['read_count'])
        with self.assertRaises(AccessError):
            staff_notice.action_open_reads()
        buyer_notice.action_mark_read()
        self.assertTrue(buyer_notice.is_read)
        self.assertEqual(notice.read_count, 2)
