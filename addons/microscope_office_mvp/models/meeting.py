from hashlib import sha256
from time import monotonic

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError

from ..services import DraftValidationError, RuleBasedDemoAdapter, validate_draft_payload
from ..services.schema import ADAPTER_TIMEOUT_SECONDS, validate_input


class Meeting(models.Model):
    _name = 'mops.meeting'
    _description = '会议纪要'
    _order = 'meeting_date desc, id desc'
    _check_company_auto = True

    name = fields.Char('会议主题', required=True)
    meeting_date = fields.Datetime('会议时间', required=True, default=fields.Datetime.now)
    host_user_id = fields.Many2one('res.users', string='主持人', required=True,
                                   default=lambda self: self.env.user, check_company=True)
    attendee_ids = fields.Many2many('res.users', string='参会人员', check_company=True)
    raw_content = fields.Text('会议原文', copy=False)
    project_id = fields.Many2one('project.project', string='任务项目', check_company=True,
                                 default=lambda self: self._default_office_project())
    action_ids = fields.One2many('mops.meeting.action', 'meeting_id', string='行动项')
    state = fields.Selection([
        ('draft', '草稿'), ('review', '待人工确认'), ('confirmed', '已生成任务'),
    ], string='状态', required=True, default='draft', readonly=True, copy=False)
    ai_summary = fields.Text('本地草稿摘要', readonly=True, copy=False)
    ai_error = fields.Text('草稿提取提示', readonly=True, copy=False)
    company_id = fields.Many2one('res.company', string='公司', required=True,
                                 default=lambda self: self.env.company, index=True)
    can_edit = fields.Boolean('可编辑会议', compute='_compute_can_edit', compute_sudo=False)
    task_count = fields.Integer('关联任务', compute='_compute_task_count', compute_sudo=False)

    @api.model
    def _default_office_project(self):
        project = self.env.ref('microscope_office_mvp.project_meeting_actions', raise_if_not_found=False)
        if project and project.active and project.company_id == self.env.company and project.has_access('read'):
            return project
        return False

    @api.depends('action_ids.generated_task_id')
    @api.depends_context('uid', 'allowed_company_ids')
    def _compute_task_count(self):
        visible = set(self.env['project.task'].search([
            ('id', 'in', self.action_ids.generated_task_id.ids),
        ]).ids)
        for meeting in self:
            meeting.task_count = sum(task.id in visible for task in meeting.action_ids.generated_task_id)

    def action_open_tasks(self):
        self.check_access('read')
        tasks = self.env['project.task'].search([
            ('id', 'in', self.action_ids.generated_task_id.ids),
        ])
        return {
            'type': 'ir.actions.act_window', 'name': _('会议任务'),
            'res_model': 'project.task', 'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': [('id', 'in', tasks.ids)], 'context': {'create': False},
        }

    @api.depends('host_user_id')
    @api.depends_context('uid')
    def _compute_can_edit(self):
        manager = self.env.user.has_group('microscope_office_mvp.group_manager')
        for meeting in self:
            meeting.can_edit = manager or meeting.host_user_id == self.env.user

    def _check_editor(self, operation='write'):
        self.check_access(operation)
        manager = self.env.user.has_group('microscope_office_mvp.group_manager')
        for meeting in self:
            if meeting.company_id not in self.env.companies:
                raise AccessError(_('请切换到会议所属的授权公司后操作。'))
            if not manager and self.env.user not in (meeting.host_user_id | meeting.create_uid):
                raise AccessError(_('仅会议主持人、创建人或办公经理可以修改和确认行动项。'))

    def _lock_for_workflow(self):
        """Serialize drafts, structural edits and confirmation on the parent row.

        The parameterized row lock also makes a competing repeatable-read
        transaction retry if confirmation committed after its snapshot. Together
        with the action/task unique constraints this prevents duplicate tasks.
        """
        if not self:
            return
        self.flush_recordset()
        self.env.cr.execute(
            'SELECT id FROM mops_meeting WHERE id IN %s ORDER BY id FOR UPDATE',
            (tuple(self.ids),),
        )
        self.invalidate_recordset()
        self.action_ids.invalidate_recordset()
        self._check_editor()
        # Child-only edits must also change the parent row version. Otherwise a
        # competing repeatable-read confirmation could retain a stale snapshot
        # of actions despite waiting for the row lock.
        super(Meeting, self).write({'write_date': fields.Datetime.now()})

    @api.constrains('name', 'host_user_id', 'attendee_ids', 'company_id', 'project_id')
    def _check_participants(self):
        for meeting in self:
            if not meeting.name or not meeting.name.strip():
                raise ValidationError(_('请填写会议主题。'))
            for user in meeting.host_user_id | meeting.attendee_ids:
                if not user.active or user.share or meeting.company_id not in user.company_ids:
                    raise ValidationError(_('主持人与参会人员必须是该公司的有效内部用户。'))
            if meeting.project_id:
                meeting.project_id.check_access('read')
                if meeting.project_id.company_id and meeting.project_id.company_id != meeting.company_id:
                    raise ValidationError(_('任务项目必须与会议属于同一公司，或为共享项目。'))
            meeting.action_ids._check_assignee()

    @api.model_create_multi
    def create(self, vals_list):
        protected = {'ai_summary', 'ai_error', 'create_uid', 'create_date', 'write_uid', 'write_date'}
        values = []
        for original in vals_list:
            vals = dict(original)
            if protected & vals.keys() or vals.get('state', 'draft') != 'draft':
                raise AccessError(_('会议状态、提取结果与审计信息只能由系统流程填写。'))
            if any(self.env.context.get('default_' + key) for key in protected):
                raise AccessError(_('请移除会议提取结果或审计信息的默认值后重试。'))
            if self.env.context.get('default_state', 'draft') != 'draft':
                raise AccessError(_('请从草稿状态新建会议。'))
            vals.update(state='draft', ai_summary=False, ai_error=False)
            company = self.env['res.company'].browse(
                vals.get('company_id') or self.env.context.get('default_company_id') or self.env.company.id)
            if company not in self.env.companies:
                raise AccessError(_('不能在未授权公司中创建会议。'))
            host_id = vals.get('host_user_id') or self.env.context.get('default_host_user_id') or self.env.uid
            if not self.env.user.has_group('microscope_office_mvp.group_manager') and host_id != self.env.uid:
                raise AccessError(_('普通员工只能为自己主持的会议创建纪要。'))
            values.append(vals)
        records = super().create(values)
        records._check_editor()
        return records

    def write(self, vals):
        self._check_editor()
        if {'state', 'ai_summary', 'ai_error', 'create_uid', 'create_date', 'write_uid', 'write_date'} & vals.keys():
            raise AccessError(_('会议状态、提取结果与审计信息不能直接修改，请使用会议操作按钮。'))
        self._lock_for_workflow()
        if any(meeting.state == 'confirmed' for meeting in self):
            raise UserError(_('会议已确认，不能再修改纪要或行动项。'))
        if 'company_id' in vals and any(vals['company_id'] != meeting.company_id.id for meeting in self):
            raise AccessError(_('已有会议不能更换公司，请在目标公司新建会议。'))
        if 'host_user_id' in vals and not self.env.user.has_group('microscope_office_mvp.group_manager'):
            if any(vals['host_user_id'] != meeting.host_user_id.id for meeting in self):
                raise AccessError(_('请由办公经理调整会议主持人。'))
        return super().write(vals)

    def unlink(self):
        self._check_editor('unlink')
        self._lock_for_workflow()
        if any(meeting.state == 'confirmed' or meeting.action_ids.generated_task_id for meeting in self):
            raise UserError(_('已生成任务的会议不能删除。'))
        return super().unlink()

    def action_extract_drafts(self):
        self._check_editor()
        with self.env.cr.savepoint():
            self._lock_for_workflow()
            for meeting in self.sorted('id'):
                if meeting.state == 'confirmed':
                    raise UserError(_('会议已确认，不能重新提取行动项。'))
                try:
                    raw_content = validate_input(meeting.raw_content)
                    deadline = monotonic() + ADAPTER_TIMEOUT_SECONDS
                    payload = RuleBasedDemoAdapter().extract(raw_content, deadline=deadline)
                    if monotonic() >= deadline:
                        raise TimeoutError()
                    draft = validate_draft_payload(payload, raw_content)
                    if monotonic() >= deadline:
                        raise TimeoutError()
                except DraftValidationError as exc:
                    error = str(exc)
                except TimeoutError:
                    error = _('本地草稿提取超时，请重试；已有草稿不变，仍可手动添加和确认行动项。')
                except Exception:
                    error = _('本地草稿助手无法处理本次输入；已有草稿不变，请手动添加或稍后重试。')
                else:
                    known_fingerprints = set(meeting.action_ids.mapped('ai_fingerprint'))
                    known_names = {line.name.strip().casefold() for line in meeting.action_ids}
                    values = []
                    for item in draft.actions:
                        fingerprint = sha256('\0'.join((item.name.casefold(), item.source_excerpt)).encode()).hexdigest()
                        if fingerprint in known_fingerprints or item.name.casefold() in known_names:
                            continue
                        values.append({
                            'name': item.name,
                            'meeting_id': meeting.id,
                            'company_id': meeting.company_id.id,
                            'suggested_assignee_name': item.suggested_assignee_name,
                            'suggested_deadline': item.suggested_deadline,
                            'source_excerpt': item.source_excerpt,
                            'ambiguous': item.ambiguous,
                            'ai_fingerprint': fingerprint,
                            'assignee_user_id': False,
                            'deadline': False,
                            'selected': False,
                            'generated_task_id': False,
                        })
                        known_fingerprints.add(fingerprint)
                        known_names.add(item.name.casefold())
                    meeting.env['mops.meeting.action']._create_extracted_drafts(meeting, values)
                    super(Meeting, meeting).write({
                        'ai_summary': draft.summary, 'ai_error': False, 'state': 'review',
                    })
                    continue
                super(Meeting, meeting).write({'ai_error': error})
        return True

    def action_confirm_tasks(self):
        self._check_editor()
        with self.env.cr.savepoint():
            self._lock_for_workflow()
            pending = []
            for meeting in self.sorted('id'):
                if meeting.state == 'confirmed':
                    continue
                if not meeting.project_id:
                    raise UserError(_('请先选择任务项目，再确认行动项。'))
                meeting._check_participants()
                lines = meeting.action_ids.filtered(lambda line: line.selected and not line.generated_task_id)
                if not lines:
                    raise UserError(_('请至少勾选一个行动项，并手工填写正式负责人及截止日期。'))
                for line in lines:
                    line._check_confirmation()
                pending.append((meeting, lines))
            for meeting, lines in pending:
                for line in lines:
                    line._create_confirmed_task()
                super(Meeting, meeting).write({'state': 'confirmed'})
        return self.action_open_tasks()
