"""Exercise the continuous quotation, meeting and notice demo through real local sessions."""
import argparse
from decimal import Decimal
import json
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from smoke_test import Client, RpcError

ROOT = Path(__file__).resolve().parents[1]
QUIET = {'mail_create_nosubscribe': True, 'mail_auto_subscribe_no_notify': True, 'mail_notify_force_send': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credentials', default=str(ROOT / '.runtime/demo_credentials.json'))
    parser.add_argument('--output', default=str(ROOT / '.runtime/acceleration_smoke.json'))
    args = parser.parse_args()
    saved = json.loads(Path(args.credentials).read_text(encoding='utf-8'))
    source = json.loads((ROOT / 'scripts/demo_acceleration.json').read_text(encoding='utf-8'))
    parsed = urlsplit(saved['url'])
    if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'} or saved['database'] != 'mops_demo':
        raise SystemExit('只允许在本机 mops_demo 模拟库运行。')
    staff, buyer, manager, admin = [Client(saved['url'], saved['database'], saved[role]) for role in ('staff', 'procurement', 'manager', 'admin')]
    adapter = buyer.call('mops.workbench', 'get_overview')['adapter_label']
    marker = '工作加速验收（模拟）' + uuid4().hex[:8]
    checks = []

    def passed(name):
        checks.append(name)
        print('PASS ' + name, flush=True)

    def denied(client, model, method, *values, **kwargs):
        try:
            client.call(model, method, *values, **kwargs)
        except RpcError as error:
            assert error.name == 'odoo.exceptions.AccessError', error
        else:
            raise AssertionError(f'Unauthorized {model}.{method} succeeded')

    product = buyer.search('product.product', [('default_code', '=', source['product_code'])], ['name', 'uom_id'])[0]
    currency = buyer.search('res.currency', [('name', '=', source['currency'])], ['name'])[0]
    project = manager.search('project.project', [('name', '=', '显微镜团队·会议任务（模拟）')], ['name'])[0]
    vendors = {row['name']: row['id'] for row in buyer.search('res.partner', [('name', 'in', [item['name'] for item in source['vendors']])], ['name'])}
    mail_count = admin.call('mail.mail', 'search_count', [])
    pack = buyer.call('mops.quote.pack', 'create', {
        'name': marker + source['name'], 'company_id': buyer.company_id,
        'product_id': product['id'], 'target_spec': source['target_spec'],
        'line_ids': [[0, 0, {
            'vendor_name': item['name'], 'raw_text': item['raw_text'],
            'partner_id': vendors[item['name']], 'product_id': product['id'],
            'uom_id': product['uom_id'][0], 'currency_id': currency['id'],
        }] for item in source['vendors']],
    })
    buyer.call('mops.quote.pack', 'action_prepare', [pack])
    quote_fields = ['vendor_name', 'freight_text', 'valid_until', 'quantity_text', 'unit_price_text', 'tax_basis', 'attention_summary', 'followup_draft', 'purchase_order_line_id', 'purchase_order_id', 'delivery_text']
    lines = {row['vendor_name']: row for row in buyer.search('mops.quote.line', [('pack_id', '=', pack)], quote_fields)}
    quote_a, quote_b = [lines[item['name']] for item in source['vendors']]
    assert quote_a['unit_price_text'] and quote_b['unit_price_text'], buyer.read('mops.quote.pack', [pack], ['last_result'])[0]
    assert not quote_a['freight_text'] and '运费' in quote_a['followup_draft']
    assert not quote_b['valid_until'] and '有效期' in quote_b['followup_draft']
    assert quote_a['tax_basis'] == 'included' and quote_b['tax_basis'] == 'excluded'
    assert Decimal(quote_a['unit_price_text']) == 12000 and Decimal(quote_b['quantity_text']) == 2
    assert '规格差异' in quote_b['attention_summary']
    buyer.call('mops.quote.line', 'write', [quote_a['id']], {'selected': True, 'specification_confirmed': True})
    buyer.call('mops.quote.pack', 'action_apply_selected', [pack])
    applied = buyer.read('mops.quote.line', [quote_a['id']], ['purchase_order_id', 'purchase_order_line_id', 'apply_note'])[0]
    assert applied['purchase_order_id'] and applied['purchase_order_line_id'], applied
    order_id, order_line_id = applied['purchase_order_id'][0], applied['purchase_order_line_id'][0]
    order = buyer.read('purchase.order', [order_id], ['state', 'amount_total', 'currency_id', 'order_line'])[0]
    assert order['state'] == 'draft' and abs(order['amount_total'] - 24000) < 0.01, order
    assert order['currency_id'][0] == currency['id']
    buyer.call('mops.quote.pack', 'action_apply_selected', [pack])
    assert buyer.read('purchase.order', [order_id], ['order_line'])[0]['order_line'] == order['order_line']
    passed('报价缺项与规格差异 -> 分供应商补问 -> 真实RFQ草稿，重复采用不重复建单')

    existing_title = marker + source['existing_task']
    task_id = manager.call('project.task', 'create', {
        'name': existing_title, 'project_id': project['id'], 'company_id': manager.company_id,
        'user_ids': [[6, 0, [buyer.uid]]], 'description': '<p>原有人工备注应保留。</p>',
    }, context=QUIET)
    meeting_raw = source['meeting_raw'].replace(source['existing_task'], existing_title).replace(
        '核对60×物镜接口与数值孔径', marker + '核对60×物镜接口与数值孔径',
    )
    meeting = manager.call('mops.meeting', 'create', {
        'name': marker + source['meeting_title'], 'company_id': manager.company_id,
        'host_user_id': manager.uid, 'attendee_ids': [[6, 0, [manager.uid, buyer.uid, staff.uid]]],
        'project_id': project['id'], 'quote_pack_id': pack, 'raw_content': meeting_raw,
    })
    manager.call('mops.meeting', 'action_prepare_continuation', [meeting])
    action_fields = ['name', 'action_kind', 'target_task_id', 'assignee_user_id', 'generated_task_id', 'applied']
    actions = manager.search('mops.meeting.action', [('meeting_id', '=', meeting)], action_fields)
    updates = [row for row in actions if row['action_kind'] == 'update']
    creates = [row for row in actions if row['action_kind'] == 'create']
    records = [row for row in actions if row['action_kind'] == 'record']
    assert len(updates) == 1 and updates[0]['target_task_id'][0] == task_id, actions
    assert len(creates) == 1 and creates[0]['assignee_user_id'][0] == staff.uid, actions
    assert records and not any('下单' in row['name'] for row in creates)
    manager.call('mops.meeting.action', 'write', [row['id'] for row in actions], {'selected': True})
    manager.call('mops.meeting', 'action_apply_continuation', [meeting])
    confirmed = manager.search('mops.meeting.action', [('meeting_id', '=', meeting)], action_fields)
    new_task_id = next(row['generated_task_id'][0] for row in confirmed if row['action_kind'] == 'create')
    description = manager.read('project.task', [task_id], ['description'])[0]['description']
    assert '原有人工备注应保留' in description and '补问精密光学含税运费' in description
    manager.call('mops.meeting', 'action_apply_continuation', [meeting])
    manager.call('mops.meeting', 'action_prepare_continuation', [meeting])
    assert manager.search('mops.meeting.action', [('meeting_id', '=', meeting)], action_fields) == confirmed
    assert manager.read('project.task', [task_id], ['description'])[0]['description'] == description
    assert staff.read('project.task', [new_task_id], ['user_ids'])[0]['user_ids'] == [staff.uid]
    passed('纪要接续已有采购任务、新建研发任务、保留不下单决定；重复采纳保留原工作')

    proposal_action = staff.call('mops.meeting', 'action_prepare_notice', [meeting])
    proposal_id = proposal_action['res_id']
    proposal = staff.read('mops.notice.proposal', [proposal_id], ['body', 'time_text', 'location', 'recipient_ids', 'state'])[0]
    assert '2026-11-18' in proposal['time_text'] and '二楼会议室' in proposal['location'], proposal
    assert set(proposal['recipient_ids']) == {staff.uid, buyer.uid, manager.uid}
    edited_body = proposal['body'] + '\n人工补充：请携带规格核对记录。'
    staff.call('mops.notice.proposal', 'write', [proposal_id], {'body': edited_body})
    assert staff.call('mops.meeting', 'action_prepare_notice', [meeting])['res_id'] == proposal_id
    assert staff.read('mops.notice.proposal', [proposal_id], ['body'])[0]['body'] == edited_body
    denied(staff, 'mops.notice.proposal', 'action_create_notice', [proposal_id])
    staff.call('mops.notice.proposal', 'action_submit', [proposal_id])
    notice_id = manager.call('mops.notice.proposal', 'action_create_notice', [proposal_id])['res_id']
    notice = manager.read('mops.notice', [notice_id], ['body', 'state', 'audience', 'recipient_ids'])[0]
    assert notice['state'] == 'draft' and notice['audience'] == 'selected'
    manager.call('mops.notice', 'write', [notice_id], {'body': notice['body'] + '<p>负责人已核对。</p>'})
    assert manager.call('mops.notice.proposal', 'action_create_notice', [proposal_id])['res_id'] == notice_id
    assert '负责人已核对' in manager.read('mops.notice', [notice_id], ['body'])[0]['body']
    passed('员工通知准备与提交 -> 负责人真实公告草稿；重复转稿不覆盖修改，不自动发布')

    cards = manager.call('mops.work.brief', 'get_cards')
    quote_card = next(card for card in cards if card['action'].get('res_model') == 'mops.quote.pack' and card['action'].get('res_id') == pack)
    assert '先不下单' in quote_card['reason'] and '运费' in quote_card['reason'], quote_card
    buyer.call('mops.quote.line', 'write', [quote_a['id']], {'delivery_text': '人工核对后为订单确认后12天'})
    buyer.call('purchase.order.line', 'write', [order_line_id], {'name': '采购人工补充：规格须再次确认。'})
    buyer.call('mops.quote.line', 'write', [quote_a['id']], {'raw_text': source['vendors'][0]['raw_text'] + source['vendors'][0]['supplement']})
    buyer.call('mops.quote.pack', 'action_prepare', [pack])
    updated_quote = buyer.read('mops.quote.line', [quote_a['id']], quote_fields)[0]
    assert Decimal(updated_quote['freight_text']) == 120, updated_quote
    assert updated_quote['delivery_text'] == '人工核对后为订单确认后12天'
    assert '运费' not in updated_quote['followup_draft'], updated_quote['followup_draft']
    buyer.call('mops.quote.pack', 'action_apply_selected', [pack])
    assert buyer.read('purchase.order.line', [order_line_id], ['name'])[0]['name'] == '采购人工补充：规格须再次确认。'
    assert buyer.read('mops.quote.line', [quote_a['id']], ['purchase_order_line_id'])[0]['purchase_order_line_id'][0] == order_line_id
    updated_cards = manager.call('mops.work.brief', 'get_cards')
    updated_card = next(card for card in updated_cards if card['action'].get('res_model') == 'mops.quote.pack' and card['action'].get('res_id') == pack)
    assert '先不下单' in updated_card['reason'] and '缺少运费' not in updated_card['reason'], updated_card
    passed('事项简报关联真实决定；补运费仅更新相关缺项，人工修正与RFQ/任务关联保留')

    denied(staff, 'mops.quote.pack', 'read', [pack], fields=['name', 'attention_summary'])
    denied(staff, 'mops.quote.pack', 'action_prepare', [pack])
    denied(staff, 'mops.quote.pack', 'action_apply_selected', [pack])
    overview = staff.call('mops.workbench', 'get_overview')
    assert not any(item['key'] == 'quote' for item in overview['shortcuts'])
    assert not any(card['action'].get('res_model') in {'mops.quote.pack', 'purchase.order', 'mops.contract'} for card in overview['cards'])
    passed('员工工作台和直接RPC均不扩大商业资料/采购权限')

    buyer.call('mops.quote.line', 'write', [quote_a['id']], {'raw_text': ''})
    try:
        buyer.call('mops.quote.pack', 'action_prepare', [pack])
    except RpcError as error:
        assert error.name in {'odoo.exceptions.UserError', 'odoo.exceptions.ValidationError'}, error
    preserved = buyer.read('mops.quote.line', [quote_a['id']], ['freight_text', 'delivery_text', 'purchase_order_line_id'])[0]
    assert Decimal(preserved['freight_text']) == 120 and preserved['purchase_order_line_id'][0] == order_line_id
    buyer.call('mops.quote.line', 'write', [quote_a['id']], {'raw_text': source['vendors'][0]['raw_text'] + source['vendors'][0]['supplement']})
    manager.call('mops.meeting', 'write', [meeting], {'raw_content': ''})
    manager.call('mops.meeting', 'action_prepare_continuation', [meeting])
    assert manager.read('mops.meeting', [meeting], ['ai_error'])[0]['ai_error']
    manual_line = manager.call('mops.meeting.action', 'create', {
        'meeting_id': meeting, 'name': marker + '人工继续核对', 'action_kind': 'create',
        'assignee_user_id': buyer.uid, 'selected': True,
    })
    manager.call('mops.meeting', 'action_apply_continuation', [meeting])
    assert manager.read('mops.meeting.action', [manual_line], ['generated_task_id'])[0]['generated_task_id']
    manager.call('mops.meeting', 'write', [meeting], {'raw_content': meeting_raw})
    assert admin.call('mail.mail', 'search_count', []) == mail_count
    passed('示例整理失败保留产物、允许无截止日期的手工继续，且不产生外发邮件')

    result = {'status': 'PASS', 'adapter': adapter, 'marker': marker, 'checks': checks,
              'quote_pack_id': pack, 'rfq_id': order_id, 'meeting_id': meeting,
              'existing_task_id': task_id, 'new_task_id': new_task_id,
              'notice_proposal_id': proposal_id, 'notice_draft_id': notice_id}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': result['status'], 'checks': len(checks), 'output': str(output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
