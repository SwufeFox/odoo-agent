"""Shared, explicitly simulated materials for the work acceleration demonstration."""
import json
from pathlib import Path


def seed_acceleration(env, record, users, vendors, products, project):
    from odoo import Command

    material = json.loads(Path(__file__).with_name('demo_acceleration.json').read_text(encoding='utf-8'))
    currency = env.ref('base.CNY')
    if not currency.active:
        currency.active = True
    country = env.company.account_fiscal_country_id or env.company.country_id or env.ref('base.cn')
    tax_group = record('acceleration_tax_group', 'account.tax.group', {
        'name': '模拟报价税费', 'company_id': env.company.id, 'country_id': country.id,
    })
    for mode, label in [('tax_included', '含税'), ('tax_excluded', '未税')]:
        record(f'acceleration_tax_{mode}', 'account.tax', {
            'name': f'模拟采购13%（{label}）', 'type_tax_use': 'purchase',
            'amount_type': 'percent', 'amount': 13,
            'price_include_override': mode, 'company_id': env.company.id,
            'country_id': country.id, 'tax_group_id': tax_group.id,
        })
    pack = record('acceleration_quote_pack', 'mops.quote.pack', {
        'name': material['name'], 'company_id': env.company.id,
        'product_id': products[0].id, 'target_spec': material['target_spec'],
    })
    for source, vendor in zip(material['vendors'], vendors):
        record(f'acceleration_quote_{source["label"]}', 'mops.quote.line', {
            'pack_id': pack.id, 'vendor_name': source['name'], 'raw_text': source['raw_text'],
            'partner_id': vendor.id, 'product_id': products[0].id,
            'uom_id': products[0].uom_id.id, 'currency_id': currency.id,
        })
    task = record('acceleration_existing_task', 'project.task', {
        'name': material['existing_task'], 'project_id': project.id,
        'company_id': env.company.id, 'user_ids': [Command.set(users['procurement'].ids)],
        'description': '<p>模拟已有工作：已取得两份物镜报价，等待确认运费与规格。原有手工备注应保留。</p>',
    })
    meeting = record('acceleration_meeting', 'mops.meeting', {
        'name': material['meeting_title'], 'meeting_date': material['date'] + ' 09:00:00',
        'host_user_id': users['manager'].id, 'company_id': env.company.id,
        'attendee_ids': [Command.set([users[role].id for role in ('staff', 'procurement', 'manager')])],
        'project_id': project.id, 'raw_content': material['meeting_raw'],
        'quote_pack_id': pack.id,
    })
    return {
        'acceleration_quote_pack_id': pack.id, 'acceleration_meeting_id': meeting.id,
        'acceleration_task_id': task.id,
    }
