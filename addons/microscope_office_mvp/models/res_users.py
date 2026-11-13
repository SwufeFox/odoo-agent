from odoo import api, models


_OFFICE_HOMES = (
    ('group_admin', 'action_office_users'),
    ('group_manager', 'action_office_approvals'),
    ('group_procurement', 'action_office_purchase'),
    ('group_staff', 'action_office_tasks'),
)


class ResUsers(models.Model):
    _inherit = 'res.users'

    def _office_home_action(self):
        self.ensure_one()
        for group_name, action_name in _OFFICE_HOMES:
            group = self.env.ref(f'microscope_office_mvp.{group_name}', raise_if_not_found=False)
            if group and self.has_group(f'microscope_office_mvp.{group_name}'):
                return self.env.ref(f'microscope_office_mvp.{action_name}', raise_if_not_found=False)
        return False

    @api.model_create_multi
    def create(self, vals_list):
        create_employees = [
            vals.get('create_employee', self.env.context.get('default_create_employee', False))
            and not vals.get('create_employee_id', self.env.context.get('default_create_employee_id'))
            for vals in vals_list
        ]
        # This Odoo20 baseline caches empty delegated name/email during user creation.
        # Let the partner finish first, then use the native employee action with fresh values.
        users = super().create([
            dict(vals, create_employee=False) if requested else vals
            for vals, requested in zip(vals_list, create_employees)
        ])
        users.invalidate_recordset(['name', 'email'])
        for user, requested in zip(users, create_employees):
            if requested:
                user.action_create_employee()
        for user in users.filtered(lambda record: not record.action_id):
            action = user._office_home_action()
            if action:
                super(ResUsers, user).write({'action_id': action.id})
        return users

    def write(self, vals):
        # Keep an explicitly chosen personal home; update only office-owned defaults.
        office_actions = {
            action.id
            for _, name in _OFFICE_HOMES
            if (action := self.env.ref(f'microscope_office_mvp.{name}', raise_if_not_found=False))
        } if 'group_ids' in vals and 'action_id' not in vals else set()
        update_home = self.filtered(lambda user: not user.action_id or user.action_id.id in office_actions) if office_actions else self.browse()
        result = super().write(vals)
        for user in update_home:
            action = user._office_home_action()
            if action and action.id != user.action_id.id:
                super(ResUsers, user).write({'action_id': action.id})
        return result
