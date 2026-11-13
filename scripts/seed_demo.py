"""Idempotently create exclusively simulated Odoo20 demo data."""
import argparse
from datetime import timedelta
import json
import os
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]


def seed(env, credential_path):
    from odoo import Command, fields
    from odoo.tools.binary import BinaryBytes

    module = 'microscope_office_mvp_demo'

    def record(key, model, vals):
        existing = env.ref(f'{module}.{key}', raise_if_not_found=False)
        if existing and existing.exists():
            return existing
        result = env[model].with_context(
            no_reset_password=True, mail_create_nosubscribe=True,
            mail_auto_subscribe_no_notify=True, mail_notify_force_send=False,
        ).create(vals)
        env['ir.model.data'].create({'module': module, 'name': key, 'model': model, 'res_id': result.id, 'noupdate': True})
        return result

    credential_path.parent.mkdir(parents=True, exist_ok=True)
    credentials = json.loads(credential_path.read_text(encoding='utf-8')) if credential_path.exists() else {}
    users = {}
    for role, label, group in [
        ('staff', '研发小林（模拟）', 'group_staff'),
        ('procurement', '采购小陈（模拟）', 'group_procurement'),
        ('manager', '负责人小周（模拟）', 'group_manager'),
        ('admin', '系统管理员（模拟）', 'group_admin'),
    ]:
        login = f'{role}@mops.local'
        if role not in credentials:
            credentials[role] = {'login': login, 'password': secrets.token_urlsafe(18)}
        users[role] = record(role, 'res.users', {
            'name': label, 'login': login, 'email': f'{role}@example.invalid',
            'password': credentials[role]['password'],
            'group_ids': [Command.set([env.ref(f'microscope_office_mvp.{group}').id])],
            'company_id': env.company.id, 'company_ids': [Command.set(env.company.ids)],
            'notification_type': 'inbox', 'lang': 'en_US', 'tz': 'Asia/Shanghai',
        })
        if not users[role].action_id:
            users[role].action_id = users[role]._office_home_action().id
        record(f'employee_{role}', 'hr.employee', {'name': label, 'user_id': users[role].id, 'company_id': env.company.id})
    credentials['url'] = os.environ.get('MOPS_BASE_URL', 'http://127.0.0.1:8069')
    credentials['database'] = env.cr.dbname
    # Secure the native initialization account before exposing the local web UI.
    admin = env.ref('base.user_admin')
    if 'bootstrap_admin' not in credentials:
        credentials['bootstrap_admin'] = {'login': admin.login, 'password': secrets.token_urlsafe(32)}
    admin.write({'password': credentials['bootstrap_admin']['password']})
    credential_path.write_text(json.dumps(credentials, ensure_ascii=False, indent=2), encoding='utf-8')
    credential_path.chmod(0o600)

    env['ir.config_parameter'].set_str('mail.catchall.domain', 'example.invalid')
    env['ir.config_parameter'].set_str('web.base.url', credentials['url'])
    env['ir.mail_server'].search([]).write({'active': False})
    env['ir.cron'].search([]).write({'active': False})
    # Verify native threshold approval in the demonstration, without a custom approval engine.
    env.company.write({'po_double_validation': 'two_step', 'po_double_validation_amount': 10000, 'po_lock': 'lock'})
    vendors = [record(f'vendor_{i}', 'res.partner', {'name': name, 'supplier_rank': 1, 'is_company': True, 'email': f'vendor{i}@example.invalid'}) for i, name in enumerate([
        '模拟供应商·精密光学', '模拟供应商·聚焦技术', '模拟供应商·科研相机',
    ], 1)]
    products = [record(f'product_{i}', 'product.product', {'name': name, 'default_code': code, 'type': 'consu', 'purchase_ok': True, 'sale_ok': False, 'standard_price': cost}) for i, (name, code, cost) in enumerate([
        ('模拟 60× 高数值孔径物镜', 'DEMO-OBJ-60', 12000),
        ('模拟压电聚焦台', 'DEMO-PZT-100', 6000),
        ('模拟科研相机', 'DEMO-CAM-01', 18000),
    ], 1)]
    for i, (vendor, product) in enumerate(zip(vendors, products), 1):
        record(f'supplierinfo_{i}', 'product.supplierinfo', {'partner_id': vendor.id, 'product_tmpl_id': product.product_tmpl_id.id, 'price': product.standard_price, 'min_qty': 1, 'company_id': env.company.id})
    today = fields.Date.today()
    contract = record('contract', 'mops.contract', {
        'title': '模拟物镜采购框架合同', 'partner_id': vendors[0].id,
        'responsible_user_id': users['procurement'].id, 'date_start': today,
        'date_end': today + timedelta(days=365),
        'document_text': '仅供本地演示：虚构物镜采购及质保条款，无商业效力。',
        'document_filename': '模拟合同.txt',
        'document_file': BinaryBytes('模拟合同附件；不含真实合同或客户数据。'.encode(), filename='模拟合同.txt'),
    })
    orders = []
    for i, (vendor, product) in enumerate(zip(vendors, products), 1):
        order = record(f'order_{i}', 'purchase.order', {
            'partner_id': vendor.id, 'user_id': users['procurement'].id,
            'mops_contract_id': contract.id if i == 1 else False,
            'origin': 'MOPS 本地模拟演示',
            'order_line': [Command.create({
                'product_id': product.id, 'name': product.name, 'product_qty': 1,
                'uom_id': product.uom_id.id, 'price_unit': product.standard_price,
                'date_planned': fields.Datetime.now(), 'tax_ids': [Command.clear()],
                'manufacturer_pn': product.default_code, 'technical_spec': '模拟规格，待业务人员核对',
            })],
        })
        orders.append(order)
    if orders[2].state == 'draft':
        orders[2].with_user(users['procurement']).button_confirm()

    project = record('project', 'project.project', {'name': '显微镜团队·会议任务（模拟）', 'company_id': env.company.id, 'user_id': users['manager'].id, 'privacy_visibility': 'employees'})
    # Feed adapter examples using explicit bounded syntax; names remain suggestions.
    meeting = record('meeting', 'mops.meeting', {
        'name': '模拟会议：物镜规格与交期协调', 'meeting_date': fields.Datetime.now(),
        'host_user_id': users['manager'].id,
        'attendee_ids': [Command.set([users['staff'].id, users['procurement'].id, users['manager'].id])],
        'project_id': project.id,
        'raw_content': f'会议决定先核对规格与交期。\n待办：向供应商确认物镜交期 | 负责人：采购小陈 | 截止：{today + timedelta(days=7)}\n待办：整理物镜接口与验收规格 | 负责人：研发小林 | 截止：{today + timedelta(days=5)}',
    })
    if not meeting.action_ids:
        env['mops.meeting.action'].create([
            {'meeting_id': meeting.id, 'name': '向供应商确认物镜交期', 'assignee_user_id': users['procurement'].id, 'deadline': today + timedelta(days=7)},
            {'meeting_id': meeting.id, 'name': '整理物镜接口与验收规格', 'assignee_user_id': users['staff'].id, 'deadline': today + timedelta(days=5)},
        ])
    ai_meeting = record('ai_meeting', 'mops.meeting', {
        'name': '模拟会议：待办草稿提取演示', 'meeting_date': fields.Datetime.now(),
        'host_user_id': users['manager'].id, 'attendee_ids': [Command.set([user.id for user in users.values()])],
        'project_id': project.id, 'raw_content': meeting.raw_content,
    })
    notice = record('notice', 'mops.notice', {
        'name': '模拟公告：本周物镜规格核对安排',
        'body': '<p>研发与采购同事请在会议任务中核对物镜规格和交期。本公告仅用于本地演示。</p>',
        'audience': 'all',
    })
    if notice.state == 'draft':
        notice.with_user(users['manager']).action_publish()
    draft_notice = record('draft_notice', 'mops.notice', {'name': '模拟公告：供应商评审会', 'body': '<p>负责人确认接收范围后再发布。</p>', 'audience': 'selected', 'recipient_ids': [Command.set([users['staff'].id])]})
    if draft_notice.name == '模拟公告草稿：供应商评审会':
        draft_notice.write({'name': '模拟公告：供应商评审会'})
    from seed_acceleration import seed_acceleration
    accelerated = seed_acceleration(env, record, users, vendors, products, project)
    return {'users': 4, 'vendors': len(vendors), 'rfqs': sum(o.state == 'draft' for o in orders), 'pending_orders': sum(o.state == 'to approve' for o in orders), 'contract_id': contract.id, 'meeting_id': meeting.id, 'ai_meeting_id': ai_meeting.id, 'notice_id': notice.id, **accelerated}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--source', default=str(ROOT / '.runtime' / 'odoo'))
    parser.add_argument('--credentials', default=str(ROOT / '.runtime' / 'demo_credentials.json'))
    args = parser.parse_args()
    sys.path.insert(0, args.source)
    import odoo.init
    from odoo import api, SUPERUSER_ID
    from odoo.orm.registry import Registry
    from odoo.tools import config
    config.parse_config(['-c', args.config, '--no-http'], setup_logging=True)
    databases = config['db_name']
    if len(databases) != 1:
        raise SystemExit('种子数据只允许初始化一个本地模拟数据库。')
    with Registry(databases[0]).cursor() as cr:
        env = api.Environment(cr, SUPERUSER_ID, {})
        result = seed(env, Path(args.credentials))
        cr.commit()
    print(json.dumps(result, ensure_ascii=False))
    print(f'登录凭据：{args.credentials}（仅本地，已忽略 Git）')


if __name__ == '__main__':
    main()
