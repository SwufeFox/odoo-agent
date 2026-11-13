from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .common import MopsCase


@tagged('post_install', '-at_install')
class TestContract(MopsCase):
    def test_contract_links_multiple_orders_and_validates_vendor(self):
        contract = self.env['mops.contract'].with_user(self.buyer).create(self.contract_values())
        first = self.create_order(mops_contract_id=contract.id)
        second = self.create_order(mops_contract_id=contract.id)
        self.assertEqual(contract.purchase_order_ids, first | second)
        other_vendor = self.env['res.partner'].create({'name': '另一模拟合同方'})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            first.write({'partner_id': other_vendor.id})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            contract.write({'partner_id': other_vendor.id})

    def test_contract_state_and_dates_are_validated(self):
        model = self.env['mops.contract'].with_user(self.buyer)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            model.create(self.contract_values(date_start=self.deadline, date_end=fields.Date.today()))
        with self.assertRaises(AccessError):
            model.create(self.contract_values(state='active'))
        contract = model.create(self.contract_values())
        with self.assertRaises(AccessError):
            contract.write({'state': 'active'})
        with self.assertRaises(AccessError):
            contract.action_activate()
        contract.with_user(self.manager).action_activate()
        self.assertEqual(contract.state, 'active')
        with self.assertRaises(UserError):
            contract.write({'document_text': '改写生效条款'})
        with self.assertRaises(UserError):
            contract.with_user(self.manager).unlink()
        with self.assertRaises(UserError):
            contract.with_user(self.manager).action_expire()
        contract.with_user(self.manager).action_cancel()
        self.assertEqual(contract.state, 'cancelled')

    def test_contract_attachment_follows_record_access(self):
        contract = self.env['mops.contract'].with_user(self.buyer).create(self.contract_values())
        attachment = self.env['ir.attachment'].with_user(self.buyer).create({
            'name': '模拟合同.txt', 'raw': b'LOCAL DEMO CONTRACT',
            'res_model': 'mops.contract', 'res_id': contract.id,
        })
        self.assertEqual(attachment.with_user(self.buyer).raw.content, b'LOCAL DEMO CONTRACT')
        with self.assertRaises(AccessError):
            attachment.with_user(self.staff).read(['raw'])

    def test_cross_company_contract_read_is_denied(self):
        user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': '其他公司责任人', 'login': 'mops_other_company_contract',
            'company_id': self.other_company.id, 'company_ids': [Command.set(self.other_company.ids)],
            'group_ids': [Command.set([self.env.ref('microscope_office_mvp.group_procurement').id])],
        })
        contract = self.env['mops.contract'].create(self.contract_values(company_id=self.other_company.id, responsible_user_id=user.id))
        with self.assertRaises(AccessError):
            contract.with_user(self.manager).read(['title'])

    def test_new_rfq_keeps_contract_party_and_company(self):
        contract = self.env['mops.contract'].with_user(self.buyer).create(self.contract_values())
        action = contract.action_new_rfq()
        order = self.env['purchase.order'].with_user(self.buyer).with_context(**action['context']).create({})
        self.assertEqual(order.partner_id, contract.partner_id)
        self.assertEqual(order.company_id, contract.company_id)
        self.assertEqual(order.mops_contract_id, contract)
        self.assertEqual(order.state, 'draft')
        linked = contract.action_open_purchase_orders()
        self.assertEqual(self.env['purchase.order'].with_user(self.buyer).search(linked['domain']), order)
        opened = order.action_open_mops_contract()
        self.assertEqual(self.env['mops.contract'].with_user(self.buyer).browse(opened['res_id']), contract)
        for operation in (
            contract.with_user(self.staff).action_new_rfq,
            contract.with_user(self.staff).action_open_purchase_orders,
            order.with_user(self.staff).action_open_mops_contract,
        ):
            with self.assertRaises(AccessError):
                operation()
