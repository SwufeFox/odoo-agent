from html import escape

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError

from ..services.notice_adapter import manual_notice, notice_details, prepare_notice
from ..services.schema import DraftValidationError


_METADATA = frozenset({
    'owner_id', 'state', 'notice_id', 'source_excerpt', 'time_source',
    'location_source', 'recipient_source', 'create_uid', 'create_date',
    'write_uid', 'write_date', 'audience_source', 'preparation_method',
    'preparation_provider', 'preparation_error', 'source_fingerprint',
    'prepared_body', 'prepared_details',
})


class NoticeProposal(models.Model):
    _name = 'mops.notice.proposal'
    _description = '公告准备稿'
    _order = 'create_date desc, id desc'
    _check_company_auto = True

    name = fields.Char('通知标题', required=True)
    body = fields.Text('通知正文', help='直接修改正文；下方识别到的时间、地点随正文更新。')
    company_id = fields.Many2one(
        'res.company', string='公司', required=True, readonly=True, index=True,
        default=lambda self: self.env.company,
    )
    meeting_id = fields.Many2one(
        'mops.meeting', string='来源会议', required=True, readonly=True,
        check_company=True, ondelete='restrict', index=True,
    )
    owner_id = fields.Many2one(
        'res.users', string='准备人', required=True, readonly=True,
        default=lambda self: self.env.user, check_company=True, index=True,
    )
    recipient_ids = fields.Many2many(
        'res.users', 'mops_notice_proposal_recipient_rel', 'proposal_id', 'user_id',
        string='接收人员候选', check_company=True,
        domain=lambda self: self.env['mops.notice']._office_recipient_domain(),
    )
    notice_id = fields.Many2one(
        'mops.notice', string='正式公告', readonly=True, copy=False,
        check_company=True, ondelete='restrict',
        groups='microscope_office_mvp.group_manager',
    )
    state = fields.Selection([
        ('draft', '准备中'), ('submitted', '已提交负责人'), ('converted', '已转公告'),
    ], string='准备状态', default='draft', required=True, readonly=True, copy=False, index=True)
    time_text = fields.Char('正文中的时间', compute='_compute_details')
    location = fields.Char('正文中的地点', compute='_compute_details')
    audience_text = fields.Char('正文指定对象', compute='_compute_details')
    attention_summary = fields.Text('待核对信息', compute='_compute_attention')
    source_excerpt = fields.Text('通知原文摘录', readonly=True, copy=False)
    time_source = fields.Char('时间来源', readonly=True, copy=False)
    location_source = fields.Char('地点来源', readonly=True, copy=False)
    recipient_source = fields.Text('接收范围来源', readonly=True, copy=False)
    audience_source = fields.Text('通知对象原文依据', readonly=True, copy=False)
    preparation_method = fields.Selection([
        ('unrecorded', '既有准备稿（方式未记录）'),
        ('local', '本地规则整理（未调用模型）'),
        ('model', '模型已整理，待人工核对'),
        ('failed', '模型整理失败，可人工继续'),
    ], string='首次准备方式', default='unrecorded', readonly=True, copy=False)
    preparation_provider = fields.Char('首次整理方式与模型', readonly=True, copy=False)
    preparation_error = fields.Text('整理失败原因', readonly=True, copy=False)
    source_fingerprint = fields.Char('原文与整理方式指纹', readonly=True, copy=False)
    prepared_body = fields.Text('首次正文快照', readonly=True, copy=False)
    prepared_details = fields.Json('首次原文事实', readonly=True, copy=False)

    _meeting_owner_unique = models.Constraint(
        'UNIQUE(meeting_id, owner_id)', '您已为这场会议准备通知，请打开已有准备稿。',
    )

    @api.depends('body', 'prepared_body', 'prepared_details')
    def _compute_details(self):
        for proposal in self:
            details = notice_details(proposal.body)
            if proposal.prepared_details and (proposal.body or '') == (proposal.prepared_body or ''):
                details.update(proposal.prepared_details)
            for key, value in details.items():
                proposal[key] = value

    @api.depends('body', 'time_text', 'location', 'recipient_ids')
    def _compute_attention(self):
        for proposal in self:
            missing = []
            if not (proposal.body or '').strip():
                missing.append(_('正文：尚无确认的通知内容，请参照来源原文手工填写。'))
            if not proposal.time_text:
                missing.append(_('时间：正文未提供明确日期，请核对是否需要补充。'))
            if not proposal.location:
                missing.append(_('地点：正文未注明，请核对是否需要补充。'))
            if not proposal.recipient_ids:
                missing.append(_('接收范围：尚未选择人员；负责人可先生成指定员工草稿，再补齐范围。'))
            proposal.attention_summary = '\n'.join(missing)

    def _check_editor(self, operation='write'):
        proposals = self.sudo(False)
        proposals.check_access(operation)
        manager = proposals.env.user.has_group('microscope_office_mvp.group_manager')
        for proposal in proposals:
            if proposal.company_id not in proposals.env.companies:
                raise AccessError(_('请切换到准备稿所属的授权公司。'))
            proposal.meeting_id.check_access('read')
            if not manager and proposal.owner_id != proposals.env.user:
                raise AccessError(_('员工只能操作本人准备的通知。'))
        return proposals

    @api.constrains('name', 'meeting_id', 'company_id', 'recipient_ids', 'owner_id')
    def _check_values(self):
        for proposal in self:
            if not (proposal.name or '').strip():
                raise ValidationError(_('请填写通知标题。'))
            proposal.meeting_id.check_access('read')
            if proposal.meeting_id.company_id != proposal.company_id:
                raise ValidationError(_('准备稿与来源会议必须属于同一公司。'))
            for user in proposal.recipient_ids:
                if (not user.active or user.share
                        or not user.has_group('microscope_office_mvp.group_staff')
                        or proposal.company_id not in user.company_ids):
                    raise ValidationError(_('接收人员必须是该公司的有效办公用户。'))

    @api.model_create_multi
    def create(self, vals_list):
        protected_defaults = {'default_' + name for name in _METADATA}
        proposals = self.sudo(False).with_context({
            key: value for key, value in self.env.context.items() if key not in protected_defaults
        })
        prepared = []
        for incoming in vals_list:
            if _METADATA & incoming.keys():
                raise AccessError(_('准备人、处理状态、正式公告与原文来源由系统记录。'))
            meeting = proposals.env['mops.meeting'].browse(incoming.get('meeting_id')).exists()
            if len(meeting) != 1:
                raise ValidationError(_('请选择一场可读取的来源会议。'))
            meeting.check_access('read')
            if meeting.company_id not in proposals.env.companies:
                raise AccessError(_('请切换到会议所属的授权公司。'))
            if incoming.get('company_id', meeting.company_id.id) != meeting.company_id.id:
                raise ValidationError(_('准备稿必须使用来源会议的公司。'))
            try:
                extracted = prepare_notice(meeting.raw_content, meeting.name)
            except DraftValidationError as error:
                extracted = manual_notice(meeting.raw_content, meeting.name, str(error))
            for field in ('time_text', 'location', 'audience_text'):
                extracted.pop(field)
            candidates = proposals.env['res.users'].search([
                ('id', 'in', meeting.attendee_ids.ids),
                ('company_ids', 'in', meeting.company_id.ids),
                *proposals.env['mops.notice']._office_recipient_domain(),
            ])
            values = {
                **extracted,
                'recipient_ids': [fields.Command.set(candidates.ids)],
                **incoming,
                'meeting_id': meeting.id,
                'company_id': meeting.company_id.id,
                'owner_id': proposals.env.uid,
                'state': 'draft',
                'recipient_source': _('来源会议的参会人员候选：%s。请核对实际通知范围。') % (
                    '、'.join(candidates.mapped('name')) or _('未提供有效办公参会人')
                ),
            }
            prepared.append(values)
        return super(NoticeProposal, proposals).create(prepared)

    def write(self, vals):
        proposals = self._check_editor()
        if _METADATA & vals.keys() or {'meeting_id', 'company_id'} & vals.keys():
            raise AccessError(_('来源、准备人和处理状态不能直接修改，请使用操作按钮。'))
        proposals.lock_for_update()
        proposals.invalidate_recordset(['state'])
        if any(proposal.state == 'converted' for proposal in proposals):
            raise UserError(_('已转公告的准备稿保留原样；请由负责人在正式公告草稿中继续修改。'))
        return super(NoticeProposal, proposals).write(vals)

    def unlink(self):
        proposals = self._check_editor('unlink')
        proposals.lock_for_update()
        proposals.invalidate_recordset(['state'])
        if any(proposal.state != 'draft' for proposal in proposals):
            raise UserError(_('已提交或已转公告的准备稿需要保留。'))
        return super(NoticeProposal, proposals).unlink()

    def action_submit(self):
        proposals = self._check_editor()
        proposals.lock_for_update()
        proposals.invalidate_recordset(['state'])
        for proposal in proposals:
            if proposal.state == 'draft':
                super(NoticeProposal, proposal).write({'state': 'submitted'})
        return True

    def action_create_notice(self):
        self.ensure_one()
        proposals = self.sudo(False)
        proposals.env['mops.notice']._require_manager()
        proposals = proposals._check_editor()
        proposals.lock_for_update()
        proposals.invalidate_recordset(['state', 'notice_id', 'name', 'body', 'recipient_ids'])
        if not proposals.notice_id:
            if not (proposals.body or '').strip():
                raise UserError(_('正文尚未填写，请先补充需要通知的内容。'))
            notice = proposals.env['mops.notice'].create({
                'name': proposals.name,
                'body': '<p>' + escape(proposals.body).replace('\n', '<br/>') + '</p>',
                'audience': 'selected',
                'recipient_ids': [fields.Command.set(proposals.recipient_ids.ids)],
                'company_id': proposals.company_id.id,
            })
            super(NoticeProposal, proposals).write({'notice_id': notice.id, 'state': 'converted'})
        proposals.notice_id.check_access('read')
        return {
            'type': 'ir.actions.act_window', 'name': _('正式公告'),
            'res_model': 'mops.notice', 'res_id': proposals.notice_id.id,
            'view_mode': 'form', 'views': [(False, 'form')], 'target': 'current',
        }


class MeetingNoticePreparation(models.Model):
    _inherit = 'mops.meeting'

    def action_prepare_notice(self):
        self.ensure_one()
        meeting = self.sudo(False)
        meeting.check_access('read')
        if meeting.company_id not in meeting.env.companies:
            raise AccessError(_('请切换到会议所属的授权公司。'))
        meeting.lock_for_update()
        model = meeting.env['mops.notice.proposal']
        proposal = model.search([
            ('meeting_id', '=', meeting.id), ('owner_id', '=', meeting.env.uid),
        ], limit=1)
        if not proposal:
            proposal = model.create({'meeting_id': meeting.id})
        return {
            'type': 'ir.actions.act_window', 'name': _('公告准备稿'),
            'res_model': 'mops.notice.proposal', 'res_id': proposal.id,
            'view_mode': 'form', 'views': [(False, 'form')], 'target': 'current',
        }
