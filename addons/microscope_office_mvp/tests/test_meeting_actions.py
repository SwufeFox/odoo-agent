from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import new_test_user

from .common import MopsCase


@tagged('post_install', '-at_install')
class TestMeetingActions(MopsCase):
    def meeting(self, **extra):
        return self.env['mops.meeting'].with_user(self.staff).create(dict({
            'name': '会议行动项测试',
            'project_id': self.project.id,
            'attendee_ids': [Command.set(self.buyer.ids)],
            'raw_content': '讨论后手工确认后续工作。',
        }, **extra))

    def action(self, meeting, **extra):
        return self.env['mops.meeting.action'].with_user(self.staff).create(dict({
            'name': '整理显微镜需求', 'meeting_id': meeting.id,
        }, **extra))

    def test_manual_confirmation_requires_human_assignment_and_deadline(self):
        meeting = self.meeting()
        line = self.action(meeting, selected=True,
                           suggested_assignee_name=self.buyer.name,
                           suggested_deadline=str(self.deadline))
        self.assertFalse(line.generated_task_id)
        with self.assertRaises(UserError):
            meeting.action_confirm_tasks()
        self.assertFalse(line.assignee_user_id)
        self.assertFalse(line.deadline)
        self.assertFalse(line.generated_task_id)
        line.write({'assignee_user_id': self.buyer.id})
        with self.assertRaises(UserError):
            meeting.action_confirm_tasks()
        self.assertFalse(line.generated_task_id)
        line.write({'deadline': self.deadline})
        meeting.action_confirm_tasks()
        task = line.generated_task_id
        self.assertTrue(task)
        self.assertEqual(meeting.state, 'confirmed')
        self.assertEqual(task.user_ids, self.buyer)
        self.assertEqual(task.project_id, self.project)
        self.assertEqual(task.company_id, meeting.company_id)
        self.assertEqual(fields.Datetime.context_timestamp(task, task.date_deadline).date(), self.deadline)
        self.assertTrue(task.mops_generated)
        self.assertEqual(task.mops_action_id, line)
        self.assertEqual(task.mops_meeting_id, meeting)
        meeting.action_confirm_tasks()
        self.assertEqual(line.generated_task_id, task)
        self.assertEqual(self.env['mops.meeting.action'].search_count([('generated_task_id', '=', task.id)]), 1)

    def test_only_selected_lines_generate_tasks(self):
        meeting = self.meeting()
        chosen = self.action(meeting, selected=True, assignee_user_id=self.buyer.id, deadline=self.deadline)
        unchosen = self.action(meeting, name='暂缓事项')
        meeting.action_confirm_tasks()
        self.assertTrue(chosen.generated_task_id)
        self.assertFalse(unchosen.generated_task_id)
        with self.assertRaises(UserError):
            unchosen.write({'selected': True})

    def test_attendees_read_but_cannot_edit_extract_or_confirm(self):
        meeting = self.meeting()
        line = self.action(meeting)
        attendee_meeting = meeting.with_user(self.buyer)
        attendee_line = line.with_user(self.buyer)
        self.assertEqual(attendee_meeting.name, meeting.name)
        self.assertEqual(attendee_line.name, line.name)
        self.assertTrue(meeting.can_edit)
        self.assertFalse(attendee_meeting.can_edit)
        self.assertTrue(meeting.with_user(self.manager).can_edit)
        for operation in (
            lambda: attendee_meeting.write({'raw_content': '篡改'}),
            attendee_meeting.action_extract_drafts,
            attendee_meeting.action_confirm_tasks,
            attendee_meeting.unlink,
            lambda: attendee_line.write({'name': '篡改'}),
            attendee_line.unlink,
            lambda: self.env['mops.meeting.action'].with_user(self.buyer).create({
                'name': '伪造行动项', 'meeting_id': meeting.id,
            }),
        ):
            with self.assertRaises(AccessError):
                operation()
        with self.assertRaises(AccessError):
            meeting.with_user(self.outsider).read(['raw_content'])
        with self.assertRaises(AccessError):
            line.with_user(self.outsider).write({'selected': True})
        meeting.with_user(self.manager).write({'name': '经理核对'})
        self.assertEqual(meeting.name, '经理核对')

    def test_direct_metadata_and_context_defaults_are_guarded(self):
        meeting = self.meeting()
        line = self.action(meeting)
        task = self.env['project.task'].with_user(self.staff).create({
            'name': '普通任务', 'project_id': self.project.id,
        })
        for vals in ({'state': 'confirmed'}, {'ai_summary': '伪造'}, {'ai_error': False},
                     {'create_uid': self.manager.id}):
            with self.assertRaises(AccessError):
                meeting.with_context(mops_internal=True, skip_security=True).write(vals)
        for vals in ({'generated_task_id': task.id}, {'ai_fingerprint': '伪造'}, {'write_uid': self.manager.id}):
            with self.assertRaises(AccessError):
                line.with_context(mops_internal=True).write(vals)
            with self.assertRaises(AccessError):
                self.action(meeting, **vals)
        with self.assertRaises(AccessError):
            self.meeting(state='confirmed')
        with self.assertRaises(AccessError):
            self.env['mops.meeting'].with_user(self.staff).with_context(default_state='confirmed').create({'name': '伪造状态'})
        with self.assertRaises(AccessError):
            self.env['mops.meeting.action'].with_user(self.staff).with_context(
                default_generated_task_id=task.id).create({'name': '伪造关联', 'meeting_id': meeting.id})
        for vals in ({'mops_generated': True}, {'mops_action_id': line.id}, {'mops_meeting_id': meeting.id}):
            with self.assertRaises(AccessError):
                task.write(vals)
            with self.assertRaises(AccessError):
                self.env['project.task'].with_user(self.staff).create(dict({'name': '伪造来源'}, **vals))
        with self.assertRaises(AccessError):
            self.env['project.task'].with_user(self.staff).with_context(
                default_mops_generated=True).create({'name': '伪造默认来源'})

    def test_assignee_company_and_parent_integrity(self):
        meeting = self.meeting()
        line = self.action(meeting)
        other = self.meeting(name='另一会议')
        with self.assertRaises(AccessError):
            line.write({'meeting_id': other.id})
        with self.assertRaises(AccessError):
            meeting.write({'company_id': self.other_company.id})
        with self.assertRaises(AccessError):
            self.meeting(company_id=self.other_company.id)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            line.write({'assignee_user_id': self.outsider.id})
        self.assertFalse(line.assignee_user_id)
        with self.assertRaises(ValidationError):
            self.action(meeting, company_id=self.other_company.id)
        other_project = self.env['project.project'].create({
            'name': '其他公司项目', 'company_id': self.other_company.id,
            'privacy_visibility': 'employees',
        })
        with self.assertRaises((AccessError, ValidationError)), self.cr.savepoint():
            meeting.write({'project_id': other_project.id})
        self.buyer.active = False
        with self.assertRaises(ValidationError), self.cr.savepoint():
            line.write({'assignee_user_id': self.buyer.id})

    def test_confirmation_is_atomic_when_a_native_task_creation_fails(self):
        meeting = self.meeting()
        first = self.action(meeting, selected=True, assignee_user_id=self.buyer.id, deadline=self.deadline)
        second = self.action(meeting, name='核对报价', selected=True,
                             assignee_user_id=self.buyer.id, deadline=self.deadline)
        task_class = type(self.env['project.task'])
        original = task_class._create_from_meeting_action
        created = []

        def fail_second(task_model, action):
            if created:
                raise UserError('模拟原生任务创建失败')
            task = original(task_model, action)
            created.append(task.id)
            return task

        with patch.object(task_class, '_create_from_meeting_action', new=fail_second):
            with self.assertRaises(UserError):
                meeting.action_confirm_tasks()
        self.assertEqual(len(created), 1)
        self.assertFalse(self.env['project.task'].browse(created).exists())
        self.assertFalse((first | second).generated_task_id)
        self.assertEqual(meeting.state, 'draft')
        meeting.action_confirm_tasks()
        self.assertEqual(len((first | second).generated_task_id), 2)

    def test_confirmed_snapshot_and_native_task_provenance_are_immutable(self):
        meeting = self.meeting()
        line = self.action(meeting, selected=True, assignee_user_id=self.buyer.id, deadline=self.deadline)
        meeting.action_confirm_tasks()
        task = line.generated_task_id
        for operation in (
            lambda: meeting.write({'raw_content': '改写已确认原文'}),
            lambda: line.write({'deadline': self.deadline}),
            line.unlink, meeting.unlink, meeting.action_extract_drafts,
            lambda: self.action(meeting, name='追加行动项'),
            lambda: task.write({'user_ids': [Command.set(self.outsider.ids)]}),
            lambda: task.write({'project_id': False}),
            lambda: task.write({'company_id': self.other_company.id}),
            task.unlink,
        ):
            with self.assertRaises(UserError):
                operation()
        task.with_user(self.buyer).write({'state': '1_done'})
        self.assertEqual(task.state, '1_done')
        copied = task.copy({'name': '独立任务副本'})
        self.assertFalse(copied.mops_generated)
        self.assertFalse(copied.mops_action_id)
        self.assertFalse(copied.mops_meeting_id)
        self.assertEqual(line.generated_task_id, task)

    def test_native_project_user_cannot_see_hidden_meeting_links(self):
        meeting = self.meeting()
        line = self.action(meeting, selected=True, assignee_user_id=self.buyer.id, deadline=self.deadline)
        meeting.action_confirm_tasks()
        project_user = new_test_user(self.env, login='mops_project_only', groups='project.group_project_user')
        task = line.generated_task_id.with_user(project_user)
        task.check_access('read')
        self.assertFalse(task.mops_action_id)
        self.assertFalse(task.mops_meeting_id)
        self.assertNotIn(meeting.raw_content, str(task.description))
        with self.assertRaises(UserError):
            task.write({'user_ids': [Command.set(project_user.ids)]})

    def test_participants_follow_only_their_accessible_tasks(self):
        meeting = self.env['mops.meeting'].with_user(self.manager).create({
            'name': '跨角色任务跟进', 'host_user_id': self.manager.id,
            'project_id': self.project.id, 'attendee_ids': [Command.set((self.staff | self.buyer).ids)],
        })
        lines = self.env['mops.meeting.action'].with_user(self.manager).create([
            {'meeting_id': meeting.id, 'name': '研发核对', 'assignee_user_id': self.staff.id, 'deadline': self.deadline, 'selected': True},
            {'meeting_id': meeting.id, 'name': '采购核对', 'assignee_user_id': self.buyer.id, 'deadline': self.deadline, 'selected': True},
        ])
        meeting.action_confirm_tasks()
        self.assertEqual(meeting.task_count, 2)
        self.assertEqual(meeting.with_user(self.staff).task_count, 1)
        self.assertEqual(meeting.with_user(self.buyer).task_count, 1)
        action = meeting.with_user(self.staff).action_open_tasks()
        visible = self.env['project.task'].with_user(self.staff).search(action['domain'])
        self.assertEqual(visible, lines[0].generated_task_id)
        self.assertEqual(visible.mops_meeting_id, meeting)
        with self.assertRaises(AccessError):
            meeting.with_user(self.outsider).action_open_tasks()
