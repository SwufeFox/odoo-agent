from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class Contract(models.Model):
    _name = 'mops.contract'
    _description = '采购合同台账'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _check_company_auto = True
    _order = 'id desc'

    name = fields.Char('合同编号', required=True, default='新建', copy=False, tracking=True)
    title = fields.Char('合同名称', required=True, tracking=True)
    company_id = fields.Many2one('res.company', '公司', required=True, default=lambda s: s.env.company)
    partner_id = fields.Many2one('res.partner', '合同方', required=True, check_company=True, tracking=True)
    responsible_user_id = fields.Many2one('res.users', '责任人', required=True, default=lambda s: s.env.user, check_company=True, tracking=True)
    date_start = fields.Date('开始日期', required=True, default=fields.Date.context_today, tracking=True)
    date_end = fields.Date('结束日期', required=True, tracking=True)
    state = fields.Selection([('draft', '草稿'), ('active', '生效'), ('expired', '到期'), ('cancelled', '取消')], default='draft', required=True, copy=False, tracking=True)
    document_text = fields.Text('合同正文', groups='microscope_office_mvp.group_procurement')
    document_file = fields.Binary('合同附件', attachment=True, groups='microscope_office_mvp.group_procurement')
    document_filename = fields.Char('附件名称', groups='microscope_office_mvp.group_procurement')
    purchase_order_ids = fields.One2many('purchase.order', 'mops_contract_id', '关联采购单')
    purchase_order_count = fields.Integer('采购单', compute='_compute_purchase_order_count', compute_sudo=False)

    @api.depends('purchase_order_ids')
    @api.depends_context('uid', 'allowed_company_ids')
    def _compute_purchase_order_count(self):
        for contract in self:
            contract.purchase_order_count = len(contract.purchase_order_ids)

    def _purchase_defaults(self):
        return {
            'default_partner_id': self.partner_id.id,
            'default_company_id': self.company_id.id,
            'default_mops_contract_id': self.id,
        }

    def action_open_purchase_orders(self):
        self.ensure_one()
        self.check_access('read')
        self.env['purchase.order'].check_access('read')
        return {
            'type': 'ir.actions.act_window', 'name': '合同采购单',
            'res_model': 'purchase.order', 'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': [('mops_contract_id', '=', self.id)],
            'context': self._purchase_defaults(),
        }

    def action_new_rfq(self):
        self.ensure_one()
        self.check_access('read')
        self.env['purchase.order'].check_access('create')
        return {
            'type': 'ir.actions.act_window', 'name': '新建询价',
            'res_model': 'purchase.order', 'view_mode': 'form',
            'views': [(False, 'form')], 'context': self._purchase_defaults(),
        }

    _unique_name_company = models.Constraint('UNIQUE(name, company_id)', '同一公司的合同编号不能重复。')

    @api.model_create_multi
    def create(self, vals_list):
        self.check_access('create')
        prepared = []
        for vals in vals_list:
            vals = dict(vals)
            if vals.get('state', 'draft') != 'draft':
                raise AccessError('请先保存合同草稿，再由负责人执行状态操作。')
            vals['state'] = 'draft'
            if vals.get('name', '新建') == '新建':
                vals['name'] = self.env['ir.sequence'].next_by_code('mops.contract')
            prepared.append(vals)
        return super().create(prepared)

    def write(self, vals):
        self.check_access('write')
        if 'state' in vals:
            raise AccessError('请使用合同的生效、到期或取消按钮修改状态。')
        if self.filtered(lambda c: c.state != 'draft'):
            raise UserError('只有草稿合同可以修改；请保留已生效合同的历史记录。')
        return super().write(vals)

    def unlink(self):
        self.check_access('unlink')
        if self.filtered(lambda c: c.state != 'draft' or c.purchase_order_ids):
            raise UserError('仅可删除没有关联采购单的草稿合同。')
        return super().unlink()

    @api.constrains('date_start', 'date_end', 'responsible_user_id', 'company_id', 'partner_id', 'purchase_order_ids')
    def _check_business_values(self):
        for contract in self:
            if contract.date_end < contract.date_start:
                raise ValidationError('合同结束日期不能早于开始日期。')
            user = contract.responsible_user_id
            if not user.active or user.share or contract.company_id not in user.company_ids:
                raise ValidationError('合同责任人必须是本公司有效的内部用户。')
            if any(order.partner_id.commercial_partner_id != contract.partner_id.commercial_partner_id or order.company_id != contract.company_id for order in contract.purchase_order_ids):
                raise ValidationError('关联合同的采购单必须属于同一公司、同一合同方。')

    def _transition(self, target, expected):
        self.check_access('write')
        if not self.env.user.has_group('microscope_office_mvp.group_manager'):
            raise AccessError('仅负责人可以修改合同状态。')
        if self.filtered(lambda c: c.state not in expected):
            raise UserError('当前合同状态不允许执行此操作。')
        return super().write({'state': target})

    def action_activate(self):
        return self._transition('active', {'draft'})

    def action_expire(self):
        if self.filtered(lambda c: c.date_end >= fields.Date.context_today(c)):
            raise UserError('合同尚未到期。')
        return self._transition('expired', {'active'})

    def action_cancel(self):
        return self._transition('cancelled', {'draft', 'active'})
