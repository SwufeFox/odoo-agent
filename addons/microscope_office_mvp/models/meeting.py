from hashlib import sha256
from time import monotonic

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError

from ..services import DraftValidationError, RuleBasedDemoAdapter, validate_draft_payload
from ..services.schema import ADAPTER_TIMEOUT_SECONDS, validate_input
from ..services.meeting_continuation import extract_continuation, unannotated_name
from ..services import model_client


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
        ('draft', '草稿'), ('review', '接续清单'), ('confirmed', '已确认锁定'),
    ], string='状态', required=True, default='draft', readonly=True, copy=False)
    ai_summary = fields.Text('整理结果摘要', readonly=True, copy=False)
    ai_error = fields.Text('草稿提取提示', readonly=True, copy=False)
    processing_mode = fields.Char('当前配置方式', compute='_compute_processing_mode')
    extraction_provider = fields.Char('最近成功整理方式', readonly=True, copy=False)
    extraction_history = fields.Json('成功来源记录', readonly=True, copy=False)
    notice_candidates = fields.Text('通知候选（未发送）', readonly=True, copy=False)

    def _compute_processing_mode(self):
        try:
            label = model_client.provider_label()
        except DraftValidationError:
            label = _('模型配置无效，请修正配置或手工继续。')
        for meeting in self:
            meeting.processing_mode = label

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

    @api.depends('action_ids.generated_task_id', 'action_ids.target_task_id', 'action_ids.applied')
    @api.depends_context('uid', 'allowed_company_ids')
    def _compute_task_count(self):
        for meeting in self:
            meeting.task_count = len(meeting._continuation_tasks())

    def _continuation_tasks(self):
        ids = (self.action_ids.generated_task_id | self.action_ids.filtered('applied').target_task_id).ids
        return self.env['project.task'].search([('id', 'in', ids)])

    def action_open_tasks(self):
        self.check_access('read')
        tasks = self._continuation_tasks()
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
        protected = {'ai_summary', 'ai_error', 'extraction_provider', 'extraction_history',
                     'notice_candidates', 'create_uid', 'create_date', 'write_uid', 'write_date'}
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
        if {'state', 'ai_summary', 'ai_error', 'extraction_provider', 'extraction_history',
                'notice_candidates', 'create_uid', 'create_date', 'write_uid', 'write_date'} & vals.keys():
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
        if any(meeting.state == 'confirmed' or meeting.action_ids.generated_task_id or any(meeting.action_ids.mapped('applied')) for meeting in self):
            raise UserError(_('已生成任务的会议不能删除。'))
        return super().unlink()

    def action_extract_drafts(self):
        try:
            use_model = model_client.model_enabled()
        except DraftValidationError:
            use_model = True
        if use_model:
            return self.action_prepare_continuation()
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
                            'extraction_provider': _('本地规则整理（未调用模型）'),
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
                        'extraction_provider': _('本地规则整理（未调用模型）'),
                    })
                    continue
                super(Meeting, meeting).write({'ai_error': error})
        return True

    def action_prepare_continuation(self):
        self._check_editor()
        with self.env.cr.savepoint():
            self._lock_for_workflow()
            for meeting in self.sorted('id'):
                if meeting.state == 'confirmed':
                    raise UserError(_('会议已通过手工流程确认，不能重新整理。'))
                try:
                    raw_content = validate_input(meeting.raw_content)
                    provider = model_client.provider_label()
                    use_model = model_client.model_enabled()
                    source_key = sha256(('meeting-v1\0' + model_client.provider_key()
                                         + '\0' + raw_content).encode()).hexdigest()
                    history = dict(meeting.extraction_history or {})
                    if source_key in history:
                        cached = history[source_key]
                        super(Meeting, meeting).write({
                            'ai_error': False, 'state': 'review',
                            'ai_summary': _('已复用同一原文与方式的整理记录；本次未重新调用，人工修改及已采纳事项保持原样。')
                                          + '\n' + cached['summary'],
                            'extraction_provider': cached['provider'],
                            'notice_candidates': cached['notices'],
                        })
                        continue
                    task_model = self.env['project.task']
                    candidates = task_model.browse()
                    if use_model and meeting.project_id and task_model.has_access('read'):
                        # Only the selected project provides context; no other project or company is sent.
                        candidates = task_model.search([
                            ('project_id', '=', meeting.project_id.id),
                            ('company_id', '=', meeting.company_id.id),
                        ], limit=100, order='id')
                    candidate_map = {f'candidate-{index}': task for index, task in enumerate(candidates, 1)}
                    draft = extract_continuation(raw_content, [
                        {'ref': ref, 'name': task.name} for ref, task in candidate_map.items()
                    ])
                except DraftValidationError as exc:
                    super(Meeting, meeting).write({'ai_error': str(exc) + _(' 已有事项不变，可手工添加或修改后采纳。')})
                    continue
                known = set(meeting.action_ids.mapped('ai_fingerprint'))
                names = {line.name.strip().casefold() for line in meeting.action_ids}
                participants = (meeting.host_user_id | meeting.attendee_ids).filtered(
                    lambda user: user.active and not user.share and meeting.company_id in user.company_ids)
                values = []
                for item in draft['actions']:
                    if item['ai_fingerprint'] in known or item['name'].casefold() in names:
                        continue
                    vals = dict(item, meeting_id=meeting.id, company_id=meeting.company_id.id,
                                generated_task_id=False, applied=False, target_task_id=False,
                                extraction_provider=provider, extraction_source=source_key)
                    target_ref = vals.pop('target_ref', '')
                    suggested_name = item['suggested_assignee_name']
                    assignees = participants.filtered(
                        lambda user: (user.name or '').strip() == suggested_name) if suggested_name else participants.browse()
                    if suggested_name and not assignees:
                        assignees = participants.filtered(
                            lambda user: unannotated_name(user.name) == suggested_name.casefold())
                    vals['assignee_user_id'] = assignees.id if len(assignees) == 1 else False
                    tasks = task_model.browse()
                    if use_model:
                        target = candidate_map.get(target_ref)
                        if target:
                            tasks = candidates.filtered(lambda task: task.name == target.name)
                    elif item['action_kind'] != 'record' and task_model.has_access('read'):
                        tasks = task_model.search([
                            ('name', '=', item['name']),
                            ('company_id', '=', meeting.company_id.id),
                        ], limit=2)
                    if len(tasks) == 1:
                        vals.update(action_kind='update', target_task_id=tasks.id)
                    elif tasks:
                        vals['action_kind'] = 'update'
                    issue = item.get('ambiguous', False) or (vals['action_kind'] == 'update' and not vals.get('target_task_id'))
                    issue = issue or (vals['action_kind'] != 'record' and (
                        (item['suggested_assignee_name'] and len(assignees) != 1)
                        or (vals['action_kind'] == 'create' and not vals['assignee_user_id'])
                        or (item['suggested_deadline'] and not item['deadline'])))
                    vals.update(ambiguous=bool(issue), selected=not issue)
                    values.append(vals)
                    known.add(item['ai_fingerprint'])
                    names.add(item['name'].casefold())
                self.env['mops.meeting.action']._create_continuation_drafts(meeting, values)
                notices = '\n\n'.join(
                    '%s\n对象：%s\n时间：%s\n原文：%s' % (
                        notice['title'], notice['audience'] or '待核对', notice['timing'] or '待核对', notice['source_excerpt'])
                    for notice in draft['notices'])
                summary = (_('模型已成功处理该来源，已整理候选事项。') if use_model
                           else _('该来源使用本地规则整理，未调用模型。'))
                summary += _(' 新增 %(count)s 行；同名或已有来源行保留原结果。请核对后采纳。', count=len(values))
                if draft['summary']:
                    summary += '\n' + draft['summary']
                history[source_key] = {'provider': provider, 'summary': summary, 'notices': notices}
                super(Meeting, meeting).write({
                    'state': 'review', 'ai_error': False, 'ai_summary': summary,
                    'extraction_provider': provider, 'extraction_history': history,
                    'notice_candidates': notices,
                })
        return True

    def action_apply_continuation(self):
        self._check_editor()
        with self.env.cr.savepoint():
            self._lock_for_workflow()
            for meeting in self.sorted('id'):
                if meeting.state == 'confirmed':
                    continue
                meeting._check_participants()
                lines = meeting.action_ids.filtered(
                    lambda line: line.selected and not line.applied and not line.generated_task_id)
                for line in lines:
                    line._check_confirmation()
                for line in lines:
                    line._apply_continuation()
        return self.action_open_tasks()


    def action_confirm_tasks(self):
        self._check_editor()
        with self.env.cr.savepoint():
            self._lock_for_workflow()
            pending = []
            for meeting in self.sorted('id'):
                if meeting.state == 'confirmed':
                    continue
                meeting._check_participants()
                lines = meeting.action_ids.filtered(lambda line: line.selected and not line.generated_task_id and not line.applied)
                if not lines and not any(meeting.action_ids.mapped('applied')):
                    raise UserError(_('请至少勾选一个行动项，并核对正式负责人。截止日期可留空。'))
                for line in lines:
                    line._check_confirmation()
                pending.append((meeting, lines))
            for meeting, lines in pending:
                for line in lines:
                    line._apply_continuation()
                super(Meeting, meeting).write({'state': 'confirmed'})
        return self.action_open_tasks()
