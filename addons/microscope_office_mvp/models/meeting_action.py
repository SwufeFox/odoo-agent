from datetime import datetime, time

import pytz
from markupsafe import Markup

from odoo import api, Command, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError


class MeetingAction(models.Model):
    _name = 'mops.meeting.action'
    _description = '会议行动项'
    _order = 'id'
    _check_company_auto = True

    name = fields.Char('行动事项', required=True)
    meeting_id = fields.Many2one('mops.meeting', string='会议', required=True,
                                 ondelete='cascade', index=True, check_company=True)
    suggested_assignee_name = fields.Char('建议负责人（仅供核对）')
    suggested_deadline = fields.Char('建议期限（原文，仅供核对）')
    source_excerpt = fields.Text('原文引用')
    ambiguous = fields.Boolean('建议待澄清', default=True)
    assignee_user_id = fields.Many2one('res.users', string='正式负责人', check_company=True)
    deadline = fields.Date('正式截止日期')
    selected = fields.Boolean('确认生成任务', default=False)
    generated_task_id = fields.Many2one('project.task', string='已生成任务', readonly=True,
                                       copy=False, ondelete='restrict', check_company=True, index=True)
    ai_fingerprint = fields.Char('提取去重标识', readonly=True, copy=False, index=True)
    company_id = fields.Many2one('res.company', string='公司', required=True,
                                 default=lambda self: self.env.company, index=True)

    _one_task_per_action = models.Constraint(
        'UNIQUE(generated_task_id)', '同一个任务不能关联多个会议行动项。')
    _one_extraction_per_meeting = models.Constraint(
        'UNIQUE(meeting_id, ai_fingerprint)', '该会议的同一提取草稿已经存在。')

    @api.constrains('name', 'meeting_id', 'company_id', 'assignee_user_id')
    def _check_assignee(self):
        for line in self:
            if not line.name or not line.name.strip():
                raise ValidationError(_('请填写行动事项。'))
            if line.company_id != line.meeting_id.company_id:
                raise ValidationError(_('行动项必须与会议属于同一公司。'))
            user = line.assignee_user_id
            if user and (
                not user.active or user.share
                or line.company_id not in user.company_ids
                or user not in (line.meeting_id.host_user_id | line.meeting_id.attendee_ids)
            ):
                raise ValidationError(_('正式负责人必须是该公司有效的内部用户，并且是主持人或参会人员。'))

    def _check_confirmation(self):
        self.ensure_one()
        self.check_access('write')
        self.meeting_id._check_editor()
        self._check_assignee()
        if not self.assignee_user_id or not self.deadline:
            raise UserError(_('行动项“%(name)s”需要人工填写正式负责人和截止日期；建议内容不会自动采用。', name=self.name))

    @api.model_create_multi
    def create(self, vals_list):
        self.check_access('create')
        protected = {'generated_task_id', 'ai_fingerprint', 'create_uid', 'create_date', 'write_uid', 'write_date'}
        values = []
        meetings = self.env['mops.meeting']
        for original in vals_list:
            vals = dict(original)
            if protected & vals.keys() or any(self.env.context.get('default_' + key) for key in protected):
                raise AccessError(_('任务关联、提取标识与审计信息不能手工指定。'))
            meeting_id = vals.get('meeting_id') or self.env.context.get('default_meeting_id')
            if not meeting_id:
                raise ValidationError(_('请先选择所属会议。'))
            meeting = self.env['mops.meeting'].browse(meeting_id)
            meeting._check_editor()
            meetings |= meeting
            vals.update(meeting_id=meeting.id, generated_task_id=False, ai_fingerprint=False)
            vals.setdefault('company_id', meeting.company_id.id)
            if vals['company_id'] != meeting.company_id.id:
                raise ValidationError(_('行动项必须与会议属于同一公司。'))
            values.append(vals)
        meetings._lock_for_workflow()
        if any(meeting.state == 'confirmed' for meeting in meetings):
            raise UserError(_('会议已确认，不能再添加行动项。'))
        return super().create(values)

    @api.model
    def _create_extracted_drafts(self, meeting, values):
        """Private insertion after schema validation; never accept formal assignments."""
        meeting.ensure_one()
        self.check_access('create')
        meeting._check_editor()
        if meeting.state == 'confirmed':
            raise UserError(_('会议已确认，不能再添加行动项。'))
        for vals in values:
            if (vals['meeting_id'] != meeting.id or vals['company_id'] != meeting.company_id.id
                    or vals.get('assignee_user_id') or vals.get('deadline') or vals.get('generated_task_id')):
                raise ValidationError(_('提取草稿只能包含建议，不能包含正式任务信息。'))
        return super(MeetingAction, self).create(values) if values else self.browse()

    def write(self, vals):
        self.check_access('write')
        meetings = self.meeting_id
        meetings._check_editor()
        if {'generated_task_id', 'ai_fingerprint', 'create_uid', 'create_date', 'write_uid', 'write_date'} & vals.keys():
            raise AccessError(_('任务关联、提取标识与审计信息只能由会议确认流程填写。'))
        meetings._lock_for_workflow()
        self.invalidate_recordset()
        if any(line.generated_task_id or line.meeting_id.state == 'confirmed' for line in self):
            raise UserError(_('已确认会议中的行动项不能修改。'))
        if 'meeting_id' in vals and any(vals['meeting_id'] != line.meeting_id.id for line in self):
            raise AccessError(_('行动项不能转移到其他会议。'))
        if 'company_id' in vals and any(vals['company_id'] != line.company_id.id for line in self):
            raise AccessError(_('行动项不能更换公司。'))
        return super().write(vals)

    def unlink(self):
        self.check_access('unlink')
        meetings = self.meeting_id
        meetings._check_editor()
        meetings._lock_for_workflow()
        self.invalidate_recordset()
        if any(line.generated_task_id or line.meeting_id.state == 'confirmed' for line in self):
            raise UserError(_('已确认会议中的行动项不能删除。'))
        return super().unlink()

    def _create_confirmed_task(self):
        self.ensure_one()
        self._check_confirmation()
        meeting = self.meeting_id
        if self.generated_task_id:
            return self.generated_task_id
        if meeting.state == 'confirmed':
            raise UserError(_('会议已确认，不能再创建任务。'))
        # Only the user's real native Project permissions can create the task.
        # Fixed local context prevents assignment mail from leaving this demo.
        task_model = self.env['project.task'].with_context(
            mail_create_nosubscribe=True, mail_auto_subscribe_no_notify=True,
            mail_create_nolog=True, mail_notify_force_send=False,
        )
        task = task_model._create_from_meeting_action(self)
        super(MeetingAction, self).write({'generated_task_id': task.id})
        task.invalidate_recordset(['mops_action_id', 'mops_meeting_id'])
        return task


class ProjectTask(models.Model):
    _inherit = 'project.task'

    mops_generated = fields.Boolean('由会议确认生成', readonly=True, copy=False)
    # The immutable FK is held by the action. These non-stored links are looked
    # up with the reading user's access, so a task never exposes a hidden meeting.
    mops_action_id = fields.Many2one('mops.meeting.action', string='来源行动项',
                                     compute='_compute_mops_links', compute_sudo=False)
    mops_meeting_id = fields.Many2one('mops.meeting', string='来源会议',
                                      compute='_compute_mops_links', compute_sudo=False)

    @api.depends('mops_generated')
    @api.depends_context('uid', 'allowed_company_ids')
    def _compute_mops_links(self):
        self.check_access('read')
        action_model = self.env['mops.meeting.action']
        actions = action_model.browse()
        if action_model.has_access('read'):
            actions = action_model.search([('generated_task_id', 'in', self.ids)])
        by_task = {line.generated_task_id.id: line for line in actions}
        for task in self:
            action = by_task.get(task.id)
            visible_meeting = action and action.meeting_id.has_access('read')
            task.mops_action_id = action if visible_meeting else False
            task.mops_meeting_id = action.meeting_id if visible_meeting else False

    @api.model_create_multi
    def create(self, vals_list):
        protected = {'mops_generated', 'mops_action_id', 'mops_meeting_id'}
        if any(protected & vals.keys() for vals in vals_list) or any(
            self.env.context.get('default_' + key) for key in protected
        ):
            raise AccessError(_('会议任务标记与来源关联只能由人工确认行动项流程生成。'))
        values = [dict(vals, mops_generated=False) for vals in vals_list]
        return super().create(values)

    @api.model
    def _create_from_meeting_action(self, action):
        action.ensure_one()
        action._check_confirmation()
        if action.generated_task_id:
            return action.generated_task_id
        meeting = action.meeting_id
        meeting.project_id.check_access('read')
        # Meeting excerpts remain on the access-controlled action; native
        # project visibility can be broader than meeting visibility.
        description = Markup('<p>%s</p>') % _(
            '会议行动项；正式负责人和截止日期已由会议主持人或经理人工确认。')
        # Odoo 20 uses a Datetime deadline. A manually selected calendar day
        # means the end of that day in the confirming user's timezone.
        local_end = datetime.combine(action.deadline, time.max).replace(microsecond=0)
        timezone = pytz.timezone(self.env.user.tz or 'UTC')
        utc_end = timezone.localize(local_end).astimezone(pytz.UTC).replace(tzinfo=None)
        return super(ProjectTask, self).create({
            'name': action.name,
            'description': description,
            'project_id': meeting.project_id.id,
            'company_id': meeting.company_id.id,
            'user_ids': [Command.set(action.assignee_user_id.ids)],
            'date_deadline': utc_end,
            'mops_generated': True,
            'mops_action_id': False,
            'mops_meeting_id': False,
            'parent_id': False,
            'recurring_task': False,
            'is_template': False,
        })

    def write(self, vals):
        self.check_access('write')
        if {'mops_generated', 'mops_action_id', 'mops_meeting_id'} & vals.keys():
            raise AccessError(_('会议任务标记与来源关联不能修改。'))
        structural = {'user_ids', 'company_id', 'project_id', 'parent_id', 'recurring_task', 'is_template'}
        if structural & vals.keys() and any(self.mapped('mops_generated')):
            raise UserError(_('会议任务的负责人、项目、公司及结构已随确认锁定。'))
        return super().write(vals)

    def unlink(self):
        self.check_access('unlink')
        if any(self.mapped('mops_generated')):
            raise UserError(_('会议生成的任务须保留来源记录，请使用任务完成或归档操作。'))
        return super().unlink()
