import os
from unittest.mock import patch

from datetime import timedelta

from odoo import Command, fields
from odoo.tests import TransactionCase


class MopsCase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        model_mode = patch.dict(os.environ, {'MOPS_AI_MODE': 'local'})
        model_mode.start()
        cls.addClassCleanup(model_mode.stop)
        cls.other_company = cls.env['res.company'].create({'name': 'MOPS 权限隔离测试公司'})
        users = cls.env['res.users'].with_context(no_reset_password=True, mail_create_nosubscribe=True)

        def user(login, group):
            return users.create({
                'name': login, 'login': login, 'email': f'{login}@example.invalid',
                'company_id': cls.env.company.id,
                'company_ids': [Command.set([cls.env.company.id])],
                'group_ids': [Command.set([cls.env.ref(f'microscope_office_mvp.{group}').id])],
                'notification_type': 'inbox',
            })

        cls.staff = user('mops_test_staff', 'group_staff')
        cls.outsider = user('mops_test_outsider', 'group_staff')
        cls.buyer = user('mops_test_buyer', 'group_procurement')
        cls.manager = user('mops_test_manager', 'group_manager')
        cls.admin_user = user('mops_test_admin', 'group_admin')
        cls.project = cls.env['project.project'].create({
            'name': 'MOPS 测试会议任务', 'company_id': cls.env.company.id,
            'user_id': cls.manager.id, 'privacy_visibility': 'employees',
        })
        cls.vendor = cls.env['res.partner'].create({'name': '模拟供应商（权限测试）', 'supplier_rank': 1})
        cls.product = cls.env['product.product'].create({'name': '模拟物镜（测试）', 'type': 'consu', 'purchase_ok': True, 'standard_price': 100})
        cls.deadline = fields.Date.today() + timedelta(days=7)

    def contract_values(self, **extra):
        return dict({
            'title': '模拟合同', 'partner_id': self.vendor.id,
            'responsible_user_id': self.buyer.id,
            'date_start': fields.Date.today(), 'date_end': self.deadline,
            'document_text': '模拟合同保密正文',
        }, **extra)

    def create_order(self, actor=None, **extra):
        return self.env['purchase.order'].with_user(actor or self.buyer).create(dict({
            'partner_id': self.vendor.id,
            'order_line': [Command.create({
                'product_id': self.product.id, 'name': self.product.name,
                'product_qty': 2, 'uom_id': self.product.uom_id.id,
                'price_unit': 200, 'date_planned': fields.Datetime.now(),
                'tax_ids': [Command.clear()],
            })],
        }, **extra))
