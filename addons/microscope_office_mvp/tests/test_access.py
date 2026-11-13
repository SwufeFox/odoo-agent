from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import MopsCase


@tagged('post_install', '-at_install')
class TestAccess(MopsCase):
    def test_staff_commercial_crud_is_denied(self):
        contract = self.env['mops.contract'].with_user(self.buyer).create(self.contract_values())
        order = self.create_order()
        for record, fields_to_read in [(contract, ['document_text']), (order, ['amount_total']), (order.order_line, ['price_unit'])]:
            with self.subTest(model=record._name):
                denied = record.with_user(self.staff)
                with self.assertRaises(AccessError):
                    denied.read(fields_to_read)
                with self.assertRaises(AccessError):
                    denied.write({'name': '越权修改'})
                with self.assertRaises(AccessError):
                    denied.unlink()
        with self.assertRaises(AccessError):
            self.env['mops.contract'].with_user(self.staff).create(self.contract_values())
        with self.assertRaises(AccessError):
            self.create_order(actor=self.staff)
        with self.assertRaises(AccessError):
            self.env['purchase.order.line'].with_user(self.staff).create({'order_id': order.id, 'name': '越权'})
        with self.assertRaises(AccessError):
            order.with_user(self.staff).button_confirm()
        with self.assertRaises(AccessError):
            order.with_user(self.staff).button_approve()

    def test_product_cost_and_vendor_prices_are_protected(self):
        supplier = self.env['product.supplierinfo'].with_user(self.buyer).create({
            'partner_id': self.vendor.id, 'product_tmpl_id': self.product.product_tmpl_id.id, 'price': 90,
        })
        for product in (self.product, self.product.product_tmpl_id):
            with self.assertRaises(AccessError):
                product.with_user(self.staff).read(['standard_price'])
            self.assertEqual(product.with_user(self.buyer).standard_price, 100)
        with self.assertRaises(AccessError):
            supplier.with_user(self.staff).read(['price'])
        with self.assertRaises(AccessError):
            self.env['product.supplierinfo'].with_user(self.staff).search([])
        self.assertEqual(supplier.with_user(self.buyer).price, 90)

    def test_native_threshold_and_direct_approval_guards(self):
        self.env.company.write({'po_double_validation': 'two_step', 'po_double_validation_amount': 1, 'po_lock': 'lock'})
        order = self.create_order()
        order.button_confirm()
        self.assertEqual(order.state, 'to approve')
        with self.assertRaises(AccessError):
            order.button_approve()
        with self.assertRaises(AccessError):
            order.write({'state': 'purchase'})
        with self.assertRaises(UserError):
            order.order_line.write({'price_unit': 1})
        with self.assertRaises(UserError):
            order.order_line.unlink()
        with self.assertRaises(UserError):
            order.write({'partner_id': self.env['res.partner'].create({'name': '另一模拟供应商'}).id})
        order.with_user(self.manager).button_approve()
        self.assertEqual(order.state, 'purchase')
        self.assertTrue(order.locked)
        self.assertEqual(order.mops_approved_by_id, self.manager)
        with self.assertRaises(AccessError):
            order.button_unlock()
        with self.assertRaises(AccessError):
            order.with_user(self.manager).button_approve()
        with self.assertRaises(AccessError):
            order.write({'mops_approved_by_id': self.buyer.id})

    def test_below_threshold_uses_native_confirmation(self):
        self.env.company.write({'po_double_validation': 'two_step', 'po_double_validation_amount': 1000, 'po_lock': 'lock'})
        order = self.create_order()
        order.button_confirm()
        self.assertEqual(order.state, 'purchase')
        self.assertEqual(order.mops_approved_by_id, self.buyer)
        self.assertTrue(order.locked)

    def test_approval_metadata_cannot_be_created(self):
        with self.assertRaises(AccessError):
            self.create_order(state='purchase')
        with self.assertRaises(AccessError):
            self.create_order(mops_approved_by_id=self.manager.id)

    def test_context_cannot_create_confirmed_records(self):
        order = self.env['purchase.order'].with_user(self.buyer).with_context(
            default_state='purchase', default_mops_approved_by_id=self.manager.id,
            default_date_approve=fields.Datetime.now(), default_locked=True,
        ).create({'partner_id': self.vendor.id})
        self.assertEqual(order.state, 'draft')
        self.assertFalse(order.mops_approved_by_id)
        self.assertFalse(order.date_approve)
        self.assertFalse(order.locked)
        contract = self.env['mops.contract'].with_user(self.buyer).with_context(
            default_state='active',
        ).create(self.contract_values())
        self.assertEqual(contract.state, 'draft')

    def test_account_creation_preserves_employee_identity(self):
        users = self.env['res.users'].with_user(self.admin_user).with_context(
            no_reset_password=True, default_create_employee=True,
        ).create([
            {
                'name': '开户员工甲', 'login': 'mops_onboard_a', 'email': 'onboard_a@example.invalid',
                'group_ids': [Command.set([self.env.ref('microscope_office_mvp.group_staff').id])],
                'create_employee': True,
            },
            {
                'name': '开户员工乙', 'login': 'mops_onboard_b', 'email': 'onboard_b@example.invalid',
                'group_ids': [Command.set([self.env.ref('microscope_office_mvp.group_staff').id])],
            },
        ])
        for user, name, email in zip(users, ('开户员工甲', '开户员工乙'), ('onboard_a@example.invalid', 'onboard_b@example.invalid')):
            with self.subTest(login=user.login):
                self.assertEqual(user.name, name)
                self.assertEqual(user.email, email)
                employee = self.env['hr.employee'].search([('user_id', '=', user.id)])
                self.assertEqual(len(employee), 1)
                self.assertEqual(employee.name, name)
                self.assertEqual(employee.resource_id.name, name)
                self.assertEqual(employee.work_contact_id, user.partner_id)
                self.assertEqual(employee.company_id, user.company_id)
                directory = self.env['hr.employee.public'].with_user(user).search([('user_id', '=', user.id)])
                self.assertEqual(directory.name, name)
