from odoo import api, models, _
from odoo.exceptions import AccessError

from ..services.model_client import ModelRequestError, provider_label


class Workbench(models.AbstractModel):
    _name = 'mops.workbench'
    _description = '办公工作加速入口'

    @api.model
    def get_overview(self):
        if not self.env.user.has_group('microscope_office_mvp.group_staff'):
            raise AccessError(_('只有办公用户可以使用工作加速。'))
        entries = [
            ('quote', '报价准备', '对照报价、补齐缺项，采用到询价草稿。',
             'action_quote_packs', 'mops.quote.pack', 'group_procurement'),
            ('meeting', '会议接续', '把会议行动接到已有任务，保留会议决定。',
             'action_meetings', 'mops.meeting', 'group_staff'),
            ('notice', '通知准备', '检查会议通知准备稿，交由负责人生成公告。',
             'action_notice_proposals', 'mops.notice.proposal', 'group_staff'),
            ('tasks', '我的任务', '继续处理已分配的工作。',
             'action_office_tasks', 'project.task', 'group_staff'),
        ]
        shortcuts = []
        for key, title, description, action_id, model, group in entries:
            if not self.env.user.has_group('microscope_office_mvp.' + group):
                continue
            if not self.env[model].has_access('read'):
                continue
            shortcuts.append({
                'key': key,
                'title': _(title),
                'description': _(description),
                'action': self.env['ir.actions.actions']._for_xml_id(
                    'microscope_office_mvp.' + action_id),
            })
        try:
            adapter_label = provider_label()
        except ModelRequestError as exc:
            adapter_label = _('模型配置需要检查：%s') % str(exc)
        return {
            'adapter_label': adapter_label,
            'shortcuts': shortcuts,
            'cards': self.env['mops.work.brief'].get_cards(),
            'empty_message': _('当前没有需要优先处理的事项，可以从上方入口继续工作。'),
        }
