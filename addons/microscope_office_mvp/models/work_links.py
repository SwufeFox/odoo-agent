from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


class Meeting(models.Model):
    _inherit = 'mops.meeting'

    quote_pack_id = fields.Many2one(
        'mops.quote.pack', string='报价准备包', check_company=True,
        ondelete='restrict', copy=False, index=True,
        groups='microscope_office_mvp.group_procurement',
    )

    @api.model
    def _check_quote_pack_value(self, pack_id, company_id):
        if not self.env.user.has_group('microscope_office_mvp.group_procurement'):
            raise AccessError(_('只有采购人员可以关联报价准备包。'))
        if not pack_id:
            return
        pack = self.env['mops.quote.pack'].browse(pack_id)
        pack.check_access('read')
        if not pack.exists():
            raise ValidationError(_('报价准备包不存在，请重新选择。'))
        if pack.company_id.id != company_id:
            raise ValidationError(_('会议和报价准备包必须属于同一公司。'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            pack_id = vals.get('quote_pack_id', self.env.context.get('default_quote_pack_id'))
            if pack_id:
                self._check_quote_pack_value(
                    pack_id,
                    vals.get('company_id') or self.env.context.get('default_company_id') or self.env.company.id,
                )
        return super().create(vals_list)

    def write(self, vals):
        if 'quote_pack_id' in vals:
            self.check_access('write')
            for meeting in self:
                meeting._check_quote_pack_value(
                    vals['quote_pack_id'], vals.get('company_id', meeting.company_id.id),
                )
        return super().write(vals)

    def action_open_quote_pack(self):
        self.ensure_one()
        self.check_access('read')
        if not self.env.user.has_group('microscope_office_mvp.group_procurement'):
            raise AccessError(_('只有采购人员可以查看报价准备包。'))
        pack = self.quote_pack_id
        self._check_quote_pack_value(pack.id, self.company_id.id)
        if not pack:
            raise ValidationError(_('请先关联报价准备包。'))
        return {
            'type': 'ir.actions.act_window', 'name': _('报价准备包'),
            'res_model': 'mops.quote.pack', 'res_id': pack.id,
            'view_mode': 'form', 'views': [(False, 'form')],
        }


class QuotePack(models.Model):
    _inherit = 'mops.quote.pack'
    _check_company_auto = True

    meeting_ids = fields.One2many(
        'mops.meeting', 'quote_pack_id', string='相关会议',
        check_company=True, readonly=True,
        groups='microscope_office_mvp.group_procurement',
    )
