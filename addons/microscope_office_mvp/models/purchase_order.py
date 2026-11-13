from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    mops_contract_id = fields.Many2one('mops.contract', '采购合同', check_company=True, tracking=True, ondelete='restrict', copy=False, groups='microscope_office_mvp.group_procurement')
    mops_approved_by_id = fields.Many2one('res.users', '确认/审批人', readonly=True, copy=False, groups='purchase.group_purchase_user')
    mops_contract_partner_id = fields.Many2one(
        related='partner_id.commercial_partner_id', groups='microscope_office_mvp.group_procurement')

    def action_open_mops_contract(self):
        self.ensure_one()
        self.check_access('read')
        contract = self.mops_contract_id
        if not contract:
            raise UserError('请先选择并保存采购合同。')
        contract.check_access('read')
        return {
            'type': 'ir.actions.act_window', 'name': '采购合同',
            'res_model': 'mops.contract', 'view_mode': 'form',
            'views': [(False, 'form')], 'res_id': contract.id,
        }

    @api.model_create_multi
    def create(self, vals_list):
        self.check_access('create')
        if any(vals.get('state', 'draft') != 'draft' or vals.get('mops_approved_by_id') or vals.get('date_approve') or vals.get('locked') for vals in vals_list):
            raise AccessError('采购单必须从未确认的询价草稿开始。')
        prepared = [dict(vals, state='draft', mops_approved_by_id=False, date_approve=False, locked=False) for vals in vals_list]
        return super().create(prepared)

    def _check_purchase_actor(self):
        self.check_access('write')
        if not self.env.user.has_group('purchase.group_purchase_user'):
            raise AccessError('当前账号没有采购操作权限。')

    def write(self, vals):
        self.check_access('write')
        if 'mops_approved_by_id' in vals:
            raise AccessError('确认/审批人由系统记录，不能直接修改。')
        commercial = {'partner_id', 'company_id', 'currency_id', 'order_line', 'fiscal_position_id'}
        if commercial.intersection(vals) and self.filtered(lambda o: o.state in ('to approve', 'purchase')):
            raise UserError('已提交审批或确认的采购单不能修改供应商、金额或明细；请按原生流程取消后重新询价。')
        if vals.get('locked') is False and not self.env.user.has_group('purchase.group_purchase_manager'):
            raise AccessError('仅采购负责人可以解锁订单。')
        if 'date_approve' in vals and vals.get('state') != 'purchase':
            raise AccessError('确认时间只能随合法的确认/审批操作写入。')
        if 'state' in vals:
            self._check_purchase_actor()
            target = vals['state']
            for order in self:
                if target == 'purchase':
                    if order.state not in ('draft', 'sent', 'to approve') or not order._approval_allowed():
                        raise AccessError('订单当前状态或审批权限不允许确认。')
                    if order.state == 'to approve' and not self.env.user.has_group('purchase.group_purchase_manager'):
                        raise AccessError('待审批订单必须由采购负责人批准。')
                    error = order._confirmation_error_message()
                    if error:
                        raise UserError(error)
                elif target == 'to approve':
                    if order.state not in ('draft', 'sent'):
                        raise UserError('只有询价草稿可以提交审批。')
                elif target == 'sent':
                    if order.state != 'draft':
                        raise UserError('只有询价草稿可以标记已发送。')
                elif target == 'draft':
                    if order.state != 'cancel':
                        raise UserError('只有已取消采购单可以重置为草稿。')
                elif target == 'cancel':
                    if order.locked or any(invoice.state not in ('draft', 'cancel') for invoice in order.invoice_ids):
                        raise UserError('请先由负责人解锁，并取消或处理已过账账单。')
                else:
                    raise UserError('不支持该采购状态。')
            if target == 'purchase':
                vals = dict(vals, mops_approved_by_id=self.env.uid, date_approve=fields.Datetime.now())
        return super().write(vals)

    def button_confirm(self):
        self._check_purchase_actor()
        if self.filtered(lambda o: o.state not in ('draft', 'sent')):
            raise UserError('只有询价草稿可以确认。')
        return super().button_confirm()

    def button_approve(self, force=False):
        self._check_purchase_actor()
        for order in self:
            if order.state not in ('draft', 'sent', 'to approve') or not order._approval_allowed():
                raise AccessError('当前订单不能由此账号审批。')
            if order.state == 'to approve' and not self.env.user.has_group('purchase.group_purchase_manager'):
                raise AccessError('仅采购负责人可以批准待审批订单。')
        return super().button_approve(force=force)

    def button_unlock(self):
        self._check_purchase_actor()
        if not self.env.user.has_group('purchase.group_purchase_manager'):
            raise AccessError('仅采购负责人可以解锁订单。')
        return super().button_unlock()

    @api.constrains('mops_contract_id', 'partner_id', 'company_id')
    def _check_mops_contract(self):
        for order in self.filtered('mops_contract_id'):
            contract = order.mops_contract_id
            contract.check_access('read')
            if order.partner_id.commercial_partner_id != contract.partner_id.commercial_partner_id or order.company_id != contract.company_id:
                raise ValidationError('采购单与合同必须属于同一公司、同一合同方。')


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    manufacturer_pn = fields.Char('原厂型号')
    technical_spec = fields.Char('技术规格')

    @api.model_create_multi
    def create(self, vals_list):
        self.check_access('create')
        orders = self.env['purchase.order'].browse([vals['order_id'] for vals in vals_list if vals.get('order_id')])
        orders.check_access('write')
        if orders.filtered(lambda o: o.state not in ('draft', 'sent')):
            raise UserError('只能为询价草稿增加明细。')
        return super().create(vals_list)

    def write(self, vals):
        self.check_access('write')
        commercial = {'order_id', 'product_id', 'name', 'product_qty', 'product_uom_qty', 'uom_id', 'price_unit', 'technical_price_unit', 'discount', 'tax_ids', 'manufacturer_pn', 'technical_spec'}
        if commercial.intersection(vals):
            orders = self.order_id
            if vals.get('order_id'):
                orders |= self.env['purchase.order'].browse(vals['order_id'])
            orders.check_access('write')
            if orders.filtered(lambda o: o.state not in ('draft', 'sent')):
                raise UserError('已提交或确认的订单不能修改采购明细。')
        return super().write(vals)

    def unlink(self):
        self.check_access('unlink')
        if self.order_id.filtered(lambda o: o.state not in ('draft', 'sent', 'cancel')):
            raise UserError('已提交或确认的订单不能删除采购明细。')
        return super().unlink()


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    standard_price = fields.Float(groups='purchase.group_purchase_user')


class ProductProduct(models.Model):
    _inherit = 'product.product'

    standard_price = fields.Float(groups='purchase.group_purchase_user')


class SupplierInfo(models.Model):
    _inherit = 'product.supplierinfo'

    price = fields.Float(groups='purchase.group_purchase_user')
    price_discounted = fields.Float(groups='purchase.group_purchase_user')
