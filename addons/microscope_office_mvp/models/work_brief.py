from odoo import api, fields, models, _
from odoo.exceptions import AccessError


class WorkBrief(models.AbstractModel):
    _name = 'mops.work.brief'
    _description = '当前可见工作的处理简报'

    @api.model
    def _visible(self, model_name, domain=None, limit=12, order='write_date desc, id desc'):
        model = self.env[model_name]
        if not model.has_access('read'):
            return model.browse()
        companies = self.env.companies.ids
        if model_name == 'project.task':
            companies = [False, *companies]
        return model.search([
            ('company_id', 'in', companies),
            *(domain or []),
        ], limit=limit, order=order)

    @api.model
    def _card(self, record, title, reason, next_label, source, *,
              status='已记录', priority='normal'):
        return {
            'id': '%s:%s' % (record._name, record.id),
            'title': title,
            'status': status,
            'reason': reason,
            'next_label': next_label,
            'action': {
                'type': 'ir.actions.act_window',
                'name': title,
                'res_model': record._name,
                'res_id': record.id,
                'view_mode': 'form',
                'views': [(False, 'form')],
                'target': 'current',
            },
            'source': source,
            'priority': priority,
        }

    @api.model
    def _task_card(self, task, meeting_names=()):
        overdue = bool(task.date_deadline and task.date_deadline < fields.Datetime.now())
        facts = [_('当前任务尚未完成。')]
        if task.date_deadline:
            deadline = fields.Datetime.context_timestamp(self, task.date_deadline)
            facts.append(_('记录截止时间：%s。', deadline.strftime('%Y-%m-%d %H:%M')))
        if overdue:
            facts.append(_('截止时间已过，建议更新进展或调整计划。'))
        else:
            facts.append(_('建议查看任务进展；未完成状态不等于已记录阻塞。'))
        return self._card(
            task, task.name, ''.join(facts), _('更新任务进展'),
            ' / '.join([_('任务：%s', task.name), *meeting_names]),
            status='已记录' if overdue else '建议查看',
            priority='warning' if overdue else 'normal',
        )

    @api.model
    def get_cards(self):
        """Return up to six live cards; reading the brief never mutates work."""
        if not self.env.user.has_group('microscope_office_mvp.group_staff'):
            raise AccessError(_('仅办公用户可以查看工作简报。'))

        meetings = self._visible('mops.meeting')
        lines = self._visible(
            'mops.meeting.action', [('meeting_id', 'in', meetings.ids)], limit=None,
        )
        lines_by_meeting = {meeting.id: lines.browse() for meeting in meetings}
        task_meetings = {}
        hold_meetings = set()
        for line in lines:
            lines_by_meeting[line.meeting_id.id] |= line
            if line.action_kind == 'record' and any(
                phrase in line.name for phrase in ('先不下单', '暂不下单', '暂停下单')
            ):
                hold_meetings.add(line.meeting_id.id)
            task = line.generated_task_id or (
                line.target_task_id if line.applied else self.env['project.task']
            )
            if task:
                task_meetings.setdefault(task.id, set()).add(line.meeting_id.id)

        linked_tasks = self._visible('project.task', [
            ('id', 'in', list(task_meetings)), ('is_closed', '=', False),
        ], order='date_deadline asc, id desc')
        cards = []
        represented_holds = set()
        # No quote search, restricted field read, or commercial source is used
        # for employees whose current ACL does not permit quote access.
        packs = self._visible('mops.quote.pack')
        quote_meetings = self._visible('mops.meeting', [
            ('id', 'in', meetings.ids), ('quote_pack_id', 'in', packs.ids),
        ]) if packs else meetings.browse()
        for pack in packs:
            related = quote_meetings.filtered(lambda meeting: meeting.quote_pack_id == pack)
            held = set(related.ids) & hold_meetings
            if not pack.attention_summary and not held:
                continue
            facts = [_('当前报价记录：%s', pack.attention_summary)] if pack.attention_summary else [
                _('当前报价记录未列出缺项；这不代表已获下单许可。'),
            ]
            if held:
                facts.append(_('会议已记录先不下单；决定仍保留在会议行动项中。'))
                represented_holds.update(held)
            related_ids = set(related.ids)
            tasks = linked_tasks.filtered(
                lambda task: bool(task_meetings[task.id] & related_ids)
            )
            if tasks:
                facts.append(_('关联未完成任务：%s。', '、'.join(tasks.mapped('name'))))
            facts.append(_('建议先核对当前缺项与会议决定，再处理采购草稿。'))
            cards.append(self._card(
                pack, pack.name, '\n'.join(facts), _('查看比较与补问草稿'),
                ' / '.join([_('报价准备：%s', pack.name), *related.mapped('name')]),
                priority='warning',
            ))
            if len(cards) == 2:
                break

        for meeting in meetings:
            pending = lines_by_meeting[meeting.id].filtered(
                lambda line: line.action_kind in ('create', 'update') and not line.applied
            )
            held = meeting.id in hold_meetings and meeting.id not in represented_holds
            if not pending and not held:
                continue
            facts = []
            if held:
                facts.append(_('会议已记录先不下单；这是会议决定，不是下单任务。'))
            if pending:
                facts.append(_('仍有%s条新增或更新建议未采纳，尚不代表任务已落实。', len(pending)))
            facts.append(_('建议核对行动清单并继续处理。'))
            cards.append(self._card(
                meeting, meeting.name, ''.join(facts), _('核对会议行动清单'),
                _('会议：%s', meeting.name), priority='warning' if held else 'normal',
            ))
            break

        task_domain = [('is_closed', '=', False)]
        if not self.env.user.has_group('microscope_office_mvp.group_manager'):
            task_domain.append(('user_ids', 'in', [self.env.uid]))
        other_tasks = self._visible(
            'project.task', task_domain, limit=2, order='date_deadline asc, id desc',
        )
        for task in (linked_tasks | other_tasks)[:2]:
            sources = [meeting.name for meeting in meetings
                       if meeting.id in task_meetings.get(task.id, ())]
            cards.append(self._task_card(task, sources))

        proposals = self._visible('mops.notice.proposal', [
            ('state', 'in', ['draft', 'submitted']),
        ], limit=1)
        for proposal in proposals:
            facts = [_('已保存通知准备稿，尚未生成正式公告。')]
            if proposal.attention_summary:
                facts.append(_('准备稿记录的待补信息：%s', proposal.attention_summary))
            facts.append(_('建议核对正文和接收范围；准备稿不等于已发布。'))
            cards.append(self._card(
                proposal, proposal.name, '\n'.join(facts), _('继续核对通知准备稿'),
                _('通知准备：%s', proposal.name),
            ))
        return sorted(cards, key=lambda card: card['priority'] != 'warning')
