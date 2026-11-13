from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import SQL


_LOG_FIELDS = frozenset({'create_uid', 'create_date', 'write_uid', 'write_date'})


class Notice(models.Model):
    _name = 'mops.notice'
    _description = '内部公告'
    _order = 'published_at desc, id desc'
    _check_company_auto = True

    name = fields.Char(string='标题', required=True)
    body = fields.Html(string='公告正文', required=True, sanitize=True)
    audience = fields.Selection(
        [('all', '全体员工'), ('selected', '指定员工')],
        string='发布范围', required=True, default='all',
    )
    recipient_ids = fields.Many2many(
        'res.users', 'mops_notice_recipient_rel', 'notice_id', 'user_id',
        string='接收员工', check_company=True,
        domain=lambda self: self._office_recipient_domain(),
    )
    state = fields.Selection(
        [('draft', '草稿'), ('published', '已发布'), ('archived', '已归档')],
        string='状态', required=True, default='draft', readonly=True, copy=False,
    )
    publisher_id = fields.Many2one(
        'res.users', string='发布人', readonly=True, copy=False, check_company=True,
    )
    published_at = fields.Datetime(string='发布时间', readonly=True, copy=False)
    company_id = fields.Many2one(
        'res.company', string='公司', required=True, default=lambda self: self.env.company,
        index=True,
    )
    read_ids = fields.One2many('mops.notice.read', 'notice_id', '阅读回执', readonly=True)
    is_read = fields.Boolean('已读', compute='_compute_my_read', compute_sudo=False)
    my_read_at = fields.Datetime('阅读时间', compute='_compute_my_read', compute_sudo=False)
    read_count = fields.Integer('已读人数', compute='_compute_read_count', compute_sudo=False,
                                groups='microscope_office_mvp.group_manager')

    @api.model
    def _office_recipient_domain(self):
        group = self.env.ref('microscope_office_mvp.group_staff', raise_if_not_found=False)
        return [('active', '=', True), ('share', '=', False),
                ('all_group_ids', 'in', group.ids if group else [])]

    @api.depends('read_ids.user_id', 'read_ids.read_at')
    @api.depends_context('uid', 'allowed_company_ids')
    def _compute_my_read(self):
        receipts = self.env['mops.notice.read'].search([
            ('notice_id', 'in', [notice.id for notice in self if notice.id]),
            ('user_id', '=', self.env.uid),
        ])
        by_notice = {receipt.notice_id.id: receipt.read_at for receipt in receipts}
        for notice in self:
            notice.my_read_at = by_notice.get(notice.id, False)
            notice.is_read = bool(notice.my_read_at)

    @api.depends('read_ids')
    @api.depends_context('uid', 'allowed_company_ids')
    def _compute_read_count(self):
        counts = self.env['mops.notice.read']._read_group(
            [('notice_id', 'in', [notice.id for notice in self if notice.id])],
            ['notice_id'], ['__count'],
        )
        by_notice = {notice.id: count for notice, count in counts}
        for notice in self:
            notice.read_count = by_notice.get(notice.id, 0)

    def action_open_reads(self):
        self.ensure_one()
        self._require_manager()
        self.check_access('read')
        self._check_allowed_company()
        return {
            'type': 'ir.actions.act_window', 'name': '公告阅读回执',
            'res_model': 'mops.notice.read', 'view_mode': 'list',
            'views': [(False, 'list')], 'domain': [('notice_id', '=', self.id)],
        }

    def _require_manager(self):
        if not self.env.user.has_group('microscope_office_mvp.group_manager'):
            raise AccessError(_('只有负责人可以创建、修改、发布或归档公告。'))

    def _check_allowed_company(self):
        allowed = self.env.companies
        if any(not notice.company_id or notice.company_id not in allowed for notice in self):
            raise AccessError(_('只能操作当前已启用公司的公告。'))

    @api.model
    def _check_company_value(self, company_id):
        if not company_id or company_id not in self.env.companies.ids:
            raise AccessError(_('请选择您当前有权使用的公司。'))

    @api.constrains('recipient_ids', 'company_id')
    def _check_recipients(self):
        for notice in self:
            if any(not user.active or user.share or not user.has_group('microscope_office_mvp.group_staff') for user in notice.recipient_ids):
                raise ValidationError(_('接收人必须是有效的办公用户。请先在员工账号中分配办公角色。'))

    @api.model_create_multi
    def create(self, vals_list):
        notices = self.sudo(False)
        notices._require_manager()
        prepared = []
        for incoming in vals_list:
            vals = dict(incoming)
            if {'publisher_id', 'published_at'} & vals.keys() or _LOG_FIELDS & vals.keys():
                raise AccessError(_('发布人、发布时间及审计信息由系统记录，不能手动设置。'))
            if vals.get('state', 'draft') != 'draft':
                raise AccessError(_('新公告必须从草稿开始，请使用“发布”操作。'))
            vals.update(
                state='draft', publisher_id=False, published_at=False,
                company_id=vals.get('company_id', notices.env.company.id),
                audience=vals.get('audience', 'all'),
                recipient_ids=vals.get('recipient_ids', []),
            )
            notices._check_company_value(vals['company_id'])
            prepared.append(vals)
        return super(Notice, notices).create(prepared)

    def write(self, vals):
        notices = self.sudo(False)
        notices._require_manager()
        notices.check_access('write')
        notices._check_allowed_company()
        if {'state', 'publisher_id', 'published_at'} & vals.keys() or _LOG_FIELDS & vals.keys():
            raise AccessError(_('状态及发布信息不能直接修改，请使用公告操作按钮。'))
        if 'company_id' in vals:
            notices._check_company_value(vals['company_id'])
        if {'body', 'audience', 'recipient_ids', 'company_id'} & vals.keys():
            if any(notice.state != 'draft' for notice in notices):
                raise UserError(_('公告发布后不能修改正文、接收范围或公司，请另建公告。'))
        return super(Notice, notices).write(vals)

    def unlink(self):
        notices = self.sudo(False)
        notices._require_manager()
        notices.check_access('unlink')
        notices._check_allowed_company()
        if notices.env['mops.notice.read'].search_count([('notice_id', 'in', notices.ids)]):
            raise UserError(_('公告已有阅读回执，不能删除；请归档公告以保留记录。'))
        return super(Notice, notices).unlink()

    def action_publish(self):
        notices = self.sudo(False)
        notices._require_manager()
        notices.check_access('write')
        notices._check_allowed_company()
        notices.lock_for_update()
        notices.invalidate_recordset(['state', 'audience', 'recipient_ids'])
        for notice in notices:
            if notice.state != 'draft':
                raise UserError(_('只有草稿公告可以发布。'))
            if notice.audience == 'selected' and not notice.recipient_ids:
                raise ValidationError(_('发布指定员工公告前，请先选择接收员工。'))
        notices._check_recipients()
        return super(Notice, notices).write({
            'state': 'published',
            'publisher_id': notices.env.uid,
            'published_at': fields.Datetime.now(),
        })

    def action_archive(self):
        notices = self.sudo(False)
        notices._require_manager()
        notices.check_access('write')
        notices._check_allowed_company()
        notices.lock_for_update()
        notices.invalidate_recordset(['state'])
        if any(notice.state != 'published' for notice in notices):
            raise UserError(_('只有已发布公告可以归档。'))
        return super(Notice, notices).write({'state': 'archived'})

    def _check_visible_publication(self):
        self.check_access('read')
        self._check_allowed_company()
        for notice in self:
            if notice.state != 'published':
                raise AccessError(_('只能为当前可见的已发布公告记录阅读回执。'))
            if not self.env.user.has_group('microscope_office_mvp.group_manager'):
                if notice.audience == 'selected' and self.env.user not in notice.recipient_ids:
                    raise AccessError(_('您不在此公告的接收范围内。'))

    def action_mark_read(self):
        notices = self.sudo(False)
        notices._check_visible_publication()
        notices.env['mops.notice.read'].create([
            {'notice_id': notice.id} for notice in notices
        ])
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {
                'title': _('已确认阅读'), 'message': _('您的阅读确认已记录。'),
                'type': 'success', 'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }


class NoticeRead(models.Model):
    _name = 'mops.notice.read'
    _description = '公告阅读回执'
    _rec_name = 'id'
    _order = 'read_at desc, id desc'
    _check_company_auto = True

    notice_id = fields.Many2one(
        'mops.notice', string='公告', required=True, readonly=True,
        ondelete='restrict', check_company=True, index=True,
    )
    user_id = fields.Many2one(
        'res.users', string='阅读人', required=True, readonly=True,
        default=lambda self: self.env.user, check_company=True, index=True,
    )
    read_at = fields.Datetime(
        string='阅读时间', required=True, readonly=True, default=fields.Datetime.now,
    )
    company_id = fields.Many2one(
        'res.company', string='公司', required=True, readonly=True,
        default=lambda self: self.env.company, index=True,
    )

    _notice_user_unique = models.Constraint(
        'UNIQUE(notice_id, user_id)', '同一员工对同一公告只能有一条阅读回执。',
    )

    @api.model_create_multi
    def create(self, vals_list):
        receipts = self.sudo(False)
        receipts.check_access('create')
        prepared = []
        for incoming in vals_list:
            vals = dict(incoming)
            if 'read_at' in vals or _LOG_FIELDS & vals.keys():
                raise AccessError(_('阅读时间及审计信息由系统记录，不能手动设置。'))
            if vals.get('user_id', receipts.env.uid) != receipts.env.uid:
                raise AccessError(_('只能为您本人记录阅读回执。'))
            notice = receipts.env['mops.notice'].browse(vals.get('notice_id'))
            if not notice:
                raise ValidationError(_('请选择要阅读的公告。'))
            notice._check_visible_publication()
            if vals.get('company_id', notice.company_id.id) != notice.company_id.id:
                raise AccessError(_('回执公司必须与公告公司一致。'))
            vals.update(
                notice_id=notice.id, user_id=receipts.env.uid,
                company_id=notice.company_id.id, read_at=fields.Datetime.now(),
            )
            prepared.append(vals)

        # A tuple update also invalidates an older REPEATABLE READ snapshot.
        # PostgreSQL then raises SerializationFailure for the normal Odoo retry,
        # instead of letting two requests race against an invisible receipt.
        notices = receipts.env['mops.notice'].browse(sorted({vals['notice_id'] for vals in prepared}))
        notices.flush_recordset()
        for notice in notices:
            receipts.env.cr.execute(SQL(
                'UPDATE %s SET write_date = write_date WHERE id = %s',
                SQL.identifier(notice._table), notice.id,
            ))
        notices.invalidate_recordset()
        notices._check_visible_publication()

        result = receipts.browse()
        for vals in prepared:
            existing = receipts.search([
                ('notice_id', '=', vals['notice_id']), ('user_id', '=', receipts.env.uid),
            ], limit=1)
            receipt = existing or super(NoticeRead, receipts).create([vals])
            result += receipt
        return result

    def write(self, vals):
        raise AccessError(_('阅读回执为不可修改的记录。'))

    def unlink(self):
        raise AccessError(_('阅读回执不能删除。'))
