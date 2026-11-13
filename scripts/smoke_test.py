"""Run authenticated loopback-only acceptance scenarios against the real Odoo Web RPC."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import json
from pathlib import Path
import secrets
from urllib.parse import urlsplit
from uuid import uuid4

import requests

ROOT = Path(__file__).resolve().parents[1]


class RpcError(RuntimeError):
    def __init__(self, error):
        self.name = error.get('data', {}).get('name', '')
        super().__init__(error.get('data', {}).get('message') or error.get('message', 'RPC error'))


class Client:
    def __init__(self, url, db, credentials):
        self.url = url.rstrip('/')
        self.session = requests.Session()
        result = self.request('/web/session/authenticate', {'db': db, **credentials})
        self.uid = result['uid']
        self.company_id = result['user_companies']['current_company']

    def request(self, path, params):
        response = self.session.post(self.url + path, json={'jsonrpc': '2.0', 'method': 'call', 'params': params, 'id': 1}, timeout=60, allow_redirects=False)
        response.raise_for_status()
        payload = response.json()
        if 'error' in payload:
            raise RpcError(payload['error'])
        return payload['result']

    def call(self, model, method, *args, **kwargs):
        return self.request(f'/web/dataset/call_kw/{model}.{method}', {'model': model, 'method': method, 'args': list(args), 'kwargs': kwargs})

    def read(self, model, ids, fields):
        return self.call(model, 'read', ids, fields=fields)

    def search(self, model, domain, fields, limit=20):
        return self.call(model, 'search_read', domain, fields=fields, limit=limit)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credentials', default=str(ROOT / '.runtime/demo_credentials.json'))
    parser.add_argument('--output', default=str(ROOT / '.runtime/smoke_result.json'))
    args = parser.parse_args()
    saved = json.loads(Path(args.credentials).read_text(encoding='utf-8'))
    url = saved['url']
    parsed = urlsplit(url)
    if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'} or saved['database'] != 'mops_demo':
        raise SystemExit('此脚本只允许在本机 mops_demo 模拟库运行。')
    clients = {role: Client(url, saved['database'], saved[role]) for role in ('staff', 'procurement', 'manager', 'admin')}
    staff, buyer, manager, admin = [clients[role] for role in ('staff', 'procurement', 'manager', 'admin')]
    manager2 = Client(url, saved['database'], saved['manager'])
    staff2 = Client(url, saved['database'], saved['staff'])
    results = []
    marker = f'自动验收（模拟）{uuid4().hex[:10]}'
    deadline = (date.today() + timedelta(days=7)).isoformat()

    def passed(name):
        results.append({'scenario': name, 'status': 'PASS'})
        print(f'PASS {name}', flush=True)

    def denied(client, model, method, *values, **kwargs):
        try:
            client.call(model, method, *values, **kwargs)
        except RpcError as exc:
            assert exc.name == 'odoo.exceptions.AccessError', (model, method, exc.name, str(exc))
        else:
            raise AssertionError(f'Unauthorized {model}.{method} succeeded')

    vendors = buyer.search('res.partner', [('name', 'in', ['模拟供应商·精密光学', '模拟供应商·聚焦技术', '模拟供应商·科研相机'])], ['name', 'supplier_rank'])
    assert len(vendors) == 3
    orders = buyer.search('purchase.order', [('origin', '=', 'MOPS 本地模拟演示')], ['name', 'state', 'partner_id', 'mops_contract_id'])
    assert len(orders) == 3 and sum(o['state'] == 'draft' for o in orders) >= 2
    passed('AC-02: three vendors and at least two RFQs')
    product = buyer.search('product.product', [('default_code', '=', 'DEMO-OBJ-60')], ['name', 'uom_id'])[0]
    contract_id = buyer.call('mops.contract', 'create', {
        'title': marker, 'partner_id': vendors[0]['id'], 'responsible_user_id': buyer.uid,
        'date_start': date.today().isoformat(), 'date_end': deadline, 'document_text': '本地模拟保密合同正文',
    })
    denied(staff, 'mops.contract', 'read', [contract_id], fields=['document_text'])
    denied(staff, 'product.product', 'read', [product['id']], fields=['standard_price'])
    denied(staff, 'purchase.order', 'read', [orders[0]['id']], fields=['amount_total'])
    denied(staff, 'purchase.order', 'button_approve', [orders[0]['id']])
    passed('AC-03/04: contract and cost access denied to staff')
    order_id = buyer.call('purchase.order', 'create', {
        'partner_id': vendors[0]['id'], 'mops_contract_id': contract_id, 'origin': marker,
        'order_line': [[0, 0, {'product_id': product['id'], 'name': product['name'], 'product_qty': 1, 'uom_id': product['uom_id'][0], 'price_unit': 20000, 'date_planned': date.today().isoformat() + ' 12:00:00', 'tax_ids': [[5, 0, 0]]}]],
    })
    buyer.call('purchase.order', 'button_confirm', [order_id])
    assert buyer.read('purchase.order', [order_id], ['state'])[0]['state'] == 'to approve'
    denied(buyer, 'purchase.order', 'button_approve', [order_id])
    denied(buyer, 'purchase.order', 'write', [order_id], {'state': 'purchase'})
    manager.call('purchase.order', 'button_approve', [order_id])
    approved = manager.read('purchase.order', [order_id], ['state', 'locked', 'mops_approved_by_id', 'mops_contract_id'])[0]
    assert approved['state'] == 'purchase' and approved['locked'] and approved['mops_approved_by_id'][0] == manager.uid and approved['mops_contract_id'][0] == contract_id
    passed('AC-05: native threshold approval and locking')
    project = manager.search('project.project', [('name', '=', '显微镜团队·会议任务（模拟）')], ['name'])[0]

    def meeting(name, raw='手工会议记录'):
        return manager.call('mops.meeting', 'create', {
            'name': marker + name, 'host_user_id': manager.uid, 'project_id': project['id'],
            'attendee_ids': [[6, 0, [staff.uid, buyer.uid, manager.uid]]], 'raw_content': raw,
        })

    meeting_id = meeting('手工任务')
    lines = []
    for name, owner in [('确认交期', buyer.uid), ('整理规格', staff.uid)]:
        lines.append(manager.call('mops.meeting.action', 'create', {'meeting_id': meeting_id, 'name': marker + name, 'assignee_user_id': owner, 'deadline': deadline, 'selected': True}))
    denied(staff, 'mops.meeting', 'action_confirm_tasks', [meeting_id])
    denied(staff, 'mops.meeting', 'write', [meeting_id], {'name': '越权修改'})
    with ThreadPoolExecutor(max_workers=2) as pool:
        concurrent = list(pool.map(lambda c: c.call('mops.meeting', 'action_confirm_tasks', [meeting_id]), [manager, manager2]))
    assert len(concurrent) == 2 and all(result['res_model'] == 'project.task' for result in concurrent)
    generated = manager.read('mops.meeting.action', lines, ['generated_task_id', 'assignee_user_id'])
    tasks = [line['generated_task_id'][0] for line in generated]
    assert len(set(tasks)) == 2
    manager.call('mops.meeting', 'action_confirm_tasks', [meeting_id])
    assert tasks == [line['generated_task_id'][0] for line in manager.read('mops.meeting.action', lines, ['generated_task_id'])]
    mine = next(line['generated_task_id'][0] for line in generated if line['assignee_user_id'][0] == staff.uid)
    other = next(task for task in tasks if task != mine)
    assert staff.read('project.task', [mine], ['user_ids'])[0]['user_ids'] == [staff.uid]
    denied(staff, 'project.task', 'read', [other], fields=['name'])
    own_task_action = staff.call('mops.meeting', 'action_open_tasks', [meeting_id])
    assert [task['id'] for task in staff.search('project.task', own_task_action['domain'], ['name'])] == [mine]
    staff.call('project.task', 'write', [mine], {'state': '1_done'})
    assert manager.read('project.task', [mine], ['state'])[0]['state'] == '1_done'
    passed('AC-06: manual tasks, actual assignees and concurrent idempotency')

    notice_id = manager.call('mops.notice', 'create', {'name': marker + '定向公告', 'body': '<p>本地模拟定向通知</p>', 'audience': 'selected', 'recipient_ids': [[6, 0, [staff.uid]]]})
    denied(staff, 'mops.notice', 'read', [notice_id], fields=['body'])
    denied(staff, 'mops.notice', 'action_publish', [notice_id])
    denied(manager, 'mops.notice', 'write', [notice_id], {'state': 'published'})
    manager.call('mops.notice', 'action_publish', [notice_id])
    assert '本地模拟定向通知' in staff.read('mops.notice', [notice_id], ['body'])[0]['body']
    denied(buyer, 'mops.notice', 'read', [notice_id], fields=['body'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda c: c.call('mops.notice', 'action_mark_read', [notice_id]), [staff, staff2]))
    receipts = manager.search('mops.notice.read', [('notice_id', '=', notice_id)], ['user_id', 'read_at'])
    assert len(receipts) == 1 and receipts[0]['user_id'][0] == staff.uid
    passed('AC-07: targeted publication and concurrent unique receipt')

    raw = f'待办：确认物镜交期 | 负责人：采购小陈 | 截止：{deadline}\n待办：整理验收规格 | 负责人：研发小林 | 截止：{deadline}'
    ai_meeting_id = meeting('规则草稿', raw)
    manager.call('mops.meeting', 'action_extract_drafts', [ai_meeting_id])
    drafts = manager.search('mops.meeting.action', [('meeting_id', '=', ai_meeting_id)], ['name', 'assignee_user_id', 'deadline', 'selected', 'generated_task_id', 'source_excerpt'])
    assert len(drafts) == 2 and all(not d['assignee_user_id'] and not d['deadline'] and not d['selected'] and not d['generated_task_id'] and d['source_excerpt'] in raw for d in drafts)
    manager.call('mops.meeting', 'action_extract_drafts', [ai_meeting_id])
    assert [d['id'] for d in drafts] == [d['id'] for d in manager.search('mops.meeting.action', [('meeting_id', '=', ai_meeting_id)], ['name'])]
    for draft, owner in zip(drafts, [buyer.uid, staff.uid]):
        manager.call('mops.meeting.action', 'write', [draft['id']], {'assignee_user_id': owner, 'deadline': deadline, 'selected': True})
    manager.call('mops.meeting', 'action_confirm_tasks', [ai_meeting_id])
    assert all(d['generated_task_id'] for d in manager.read('mops.meeting.action', [d['id'] for d in drafts], ['generated_task_id']))
    passed('AC-08: suggestions only, explicit human confirmation')

    recovery = meeting('人工恢复', '只记录讨论，无显式行动项。')
    manager.call('mops.meeting', 'action_extract_drafts', [recovery])
    assert manager.read('mops.meeting', [recovery], ['ai_error'])[0]['ai_error']
    line = manager.call('mops.meeting.action', 'create', {'meeting_id': recovery, 'name': marker + '人工补录', 'assignee_user_id': staff.uid, 'deadline': deadline, 'selected': True})
    manager.call('mops.meeting', 'action_confirm_tasks', [recovery])
    assert manager.read('mops.meeting.action', [line], ['generated_task_id'])[0]['generated_task_id']
    passed('AC-09: extraction failure permits manual continuation')

    # Admin is a real configured account; read a public directory and own role.
    assert admin.read('res.users', [admin.uid], ['login'])[0]['login'] == saved['admin']['login']
    assert len(staff.search('hr.employee.public', [('user_id', 'in', [c.uid for c in clients.values()])], ['name'])) == 4
    passed('Four authenticated roles and employee directory')

    # A real administrator opens an account, then a manager hands work to it.
    staff_group = admin.search('ir.model.data', [('module', '=', 'microscope_office_mvp'), ('name', '=', 'group_staff')], ['res_id'])[0]['res_id']
    new_credentials = {'login': f'workflow_{uuid4().hex[:10]}@mops.local', 'password': secrets.token_urlsafe(24)}
    new_user_id = admin.call('res.users', 'create', {
        'name': marker + '新同事', **new_credentials,
        'email': 'workflow@example.invalid', 'notification_type': 'inbox',
        'company_id': admin.company_id, 'company_ids': [[6, 0, [admin.company_id]]],
        'group_ids': [[6, 0, [staff_group]]], 'create_employee': True,
    }, context={'no_reset_password': True})
    new_staff = Client(url, saved['database'], new_credentials)
    assert new_staff.search('hr.employee.public', [('user_id', '=', new_user_id)], ['name'])[0]['name'] == marker + '新同事'
    denied(new_staff, 'mops.contract', 'read', [contract_id], fields=['document_text'])
    handoff_notice = manager.call('mops.notice', 'create', {
        'name': marker + '新同事安排', 'body': '<p>请完成已分派的规格核对任务。</p>',
        'audience': 'selected', 'recipient_ids': [[6, 0, [new_user_id]]],
    })
    manager.call('mops.notice', 'action_publish', [handoff_notice])
    assert not new_staff.read('mops.notice', [handoff_notice], ['is_read'])[0]['is_read']
    new_staff.call('mops.notice', 'action_mark_read', [handoff_notice])
    acknowledged = new_staff.read('mops.notice', [handoff_notice], ['is_read', 'my_read_at'])[0]
    assert acknowledged['is_read'] and acknowledged['my_read_at']
    assert manager.read('mops.notice', [handoff_notice], ['read_count'])[0]['read_count'] == 1
    denied(new_staff, 'mops.notice', 'action_open_reads', [handoff_notice])
    handoff = manager.call('mops.meeting', 'create', {
        'name': marker + '工作交接', 'host_user_id': manager.uid,
        'attendee_ids': [[6, 0, [new_user_id]]], 'raw_content': '核对物镜接口规格。',
    })
    handoff_line = manager.call('mops.meeting.action', 'create', {
        'meeting_id': handoff, 'name': marker + '新同事规格核对',
        'assignee_user_id': new_user_id, 'deadline': deadline, 'selected': True,
    })
    manager.call('mops.meeting', 'action_confirm_tasks', [handoff])
    handoff_task = manager.read('mops.meeting.action', [handoff_line], ['generated_task_id'])[0]['generated_task_id'][0]
    assert new_staff.read('project.task', [handoff_task], ['user_ids', 'mops_meeting_id'])[0]['mops_meeting_id'][0] == handoff
    new_staff.call('project.task', 'write', [handoff_task], {'state': '1_done'})
    assert manager.read('project.task', [handoff_task], ['state'])[0]['state'] == '1_done'
    buyer_group = admin.search('ir.model.data', [('module', '=', 'microscope_office_mvp'), ('name', '=', 'group_procurement')], ['res_id'])[0]['res_id']
    admin.call('res.users', 'write', [new_user_id], {'group_ids': [[6, 0, [buyer_group]]]})
    assert new_staff.read('mops.contract', [contract_id], ['document_text'])[0]['document_text'] == '本地模拟保密合同正文'
    admin.call('res.users', 'write', [new_user_id], {'group_ids': [[6, 0, [staff_group]]]})
    denied(new_staff, 'mops.contract', 'read', [contract_id], fields=['document_text'])
    passed('Admin onboarding -> manager assignment/publication -> staff acknowledgement/completion -> manager follow-up')
    report = {'status': 'PASS', 'marker': marker, 'checks': results, 'task_ids': tasks, 'notice_id': notice_id, 'contract_id': contract_id}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': report['status'], 'checks': len(results), 'output': str(output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
