import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import MopsCase
from ..models import quote_pack
from ..services import model_client, quote_adapter
from ..services.quote_adapter import decimal_value, extract_quote, validate_quote_response
from ..services.schema import DraftValidationError


@tagged('post_install', '-at_install', 'mops')
class TestQuotePack(MopsCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.scenario = json.loads((Path(__file__).resolve().parents[3] / 'scripts' / 'demo_acceleration.json').read_text(encoding='utf-8'))
        cls.currency = cls.env.ref('base.CNY')
        cls.currency.active = True
        cls.quote_vendors = cls.env['res.partner'].create([
            {'name': vendor['name'], 'supplier_rank': 1} for vendor in cls.scenario['vendors']
        ])
        cls.included_tax = cls.env['account.tax'].create({
            'name': '模拟报价测试13%含税', 'amount': 13, 'amount_type': 'percent',
            'type_tax_use': 'purchase', 'company_id': cls.env.company.id,
            'price_include_override': 'tax_included',
        })
        cls.excluded_tax = cls.env['account.tax'].create({
            'name': '模拟报价测试13%未税', 'amount': 13, 'amount_type': 'percent',
            'type_tax_use': 'purchase', 'company_id': cls.env.company.id,
            'price_include_override': 'tax_excluded',
        })

    def make_pack(self):
        return self.env['mops.quote.pack'].with_user(self.buyer).create({
            'name': self.scenario['name'], 'product_id': self.product.id,
            'target_spec': self.scenario['target_spec'],
            'line_ids': [Command.create({
                'vendor_name': vendor['name'], 'raw_text': vendor['raw_text'],
                'partner_id': self.quote_vendors[index].id,
                'product_id': self.product.id, 'uom_id': self.product.uom_id.id,
                'tax_id': (self.included_tax if index == 0 else self.excluded_tax).id,
            }) for index, vendor in enumerate(self.scenario['vendors'])],
        })

    @contextmanager
    def model_response(self, response=None, error=None):
        with patch.object(model_client, 'model_enabled', return_value=True), \
                patch.object(model_client, 'provider_key', return_value='openai:quote-test:endpoint'), \
                patch.object(model_client, 'provider_label', return_value='AI 辅助整理 · quote-test'), \
                patch.object(model_client, 'request_json', return_value=response, side_effect=error) as request:
            yield request

    def test_shared_material_unknown_and_specification_difference(self):
        pack = self.make_pack()
        pack.action_prepare()
        first, second = pack.line_ids
        self.assertFalse(first.freight_text)
        self.assertIsNone(decimal_value(first.freight_text))
        self.assertEqual(decimal_value('0'), 0)
        self.assertIn('缺少运费', first.attention_summary)
        self.assertNotIn('缺少报价有效期', first.attention_summary)
        self.assertIn('缺少报价有效期', second.attention_summary)
        self.assertIn('NA 1.35 与目标NA 1.40不同', second.attention_summary)
        self.assertIn('24000 CNY', first.amount_summary)
        self.assertIn('到手总额待补', first.amount_summary)
        self.assertIn('24588 CNY', second.amount_summary)
        self.assertIn('运费', first.followup_draft)
        self.assertIn('报价有效期', second.followup_draft)
        self.assertFalse(second.specification_confirmed)

    def test_supplement_keeps_manual_edit_and_native_link(self):
        pack = self.make_pack()
        pack.action_prepare()
        first = pack.line_ids[0]
        first.write({'selected': True, 'unit_price_text': '12500'})
        pack.action_apply_selected()
        native_line = first.purchase_order_line_id
        self.assertTrue(native_line)
        native_line.name = '采购员修订后的原生询价描述'
        first.raw_text += self.scenario['vendors'][0]['supplement']
        pack.action_prepare()
        self.assertEqual(first.freight_text, '120')
        self.assertEqual(first.freight_tax_basis, 'included')
        self.assertEqual(first.unit_price_text, '12500')
        self.assertNotIn('缺少运费', first.attention_summary)
        self.assertNotIn('缺少运费', first.followup_draft)
        self.assertIn('25120 CNY', first.amount_summary)
        self.assertEqual(first.purchase_order_line_id, native_line)
        pack.action_apply_selected()
        self.assertEqual(first.purchase_order_line_id, native_line)
        self.assertEqual(native_line.name, '采购员修订后的原生询价描述')
        self.assertEqual(len(native_line.order_id.order_line), 1)

    def test_explicit_free_shipping_and_manual_question_survive(self):
        pack = self.make_pack()
        pack.action_prepare()
        first = pack.line_ids[0]
        first.write({'freight_text': '0', 'followup_draft': '请补充盖章版报价。'})
        first.raw_text += self.scenario['vendors'][0]['supplement']
        pack.action_prepare()
        self.assertEqual(first.freight_text, '0')
        self.assertEqual(first.followup_draft, '请补充盖章版报价。')
        self.assertNotIn('缺少运费', first.attention_summary)
        self.assertIn('含运费总额 24000', first.amount_summary)

    def test_apply_creates_native_draft_and_preserves_incomplete_line(self):
        pack = self.make_pack()
        pack.action_prepare()
        pack.line_ids.write({'selected': True})
        pack.action_apply_selected()
        first, second = pack.line_ids
        order = first.purchase_order_id
        self.assertTrue(order)
        self.assertEqual(order.state, 'draft')
        self.assertEqual(order.partner_id, self.quote_vendors[0])
        self.assertEqual(order.currency_id, self.currency)
        self.assertEqual(first.purchase_order_line_id.manufacturer_pn, 'MO60-140')
        self.assertAlmostEqual(order.amount_total, 24000)
        self.assertFalse(second.purchase_order_id)
        self.assertEqual(second.apply_state, 'prepared')
        self.assertIn('人工核对规格差异', second.apply_note)
        self.assertFalse(order.message_follower_ids)
        pack.action_apply_selected()
        self.assertEqual(first.purchase_order_id, order)
        self.assertEqual(len(order.order_line), 1)
        second.specification_confirmed = True
        pack.action_apply_selected()
        self.assertEqual(second.purchase_order_id.state, 'draft')
        self.assertAlmostEqual(second.purchase_order_id.amount_total, 24408)

    def test_missing_master_data_does_not_create_supplier_or_product(self):
        pack = self.make_pack()
        first = pack.line_ids[0]
        first.write({'vendor_name': '模拟不存在供应商731', 'partner_id': False,
                     'product_id': False, 'selected': True})
        vendors = self.env['res.partner'].search_count([])
        products = self.env['product.product'].search_count([])
        pack.action_prepare()
        pack.action_apply_selected()
        self.assertFalse(first.partner_id)
        self.assertFalse(first.product_id)
        self.assertFalse(first.purchase_order_id)
        self.assertEqual(first.apply_state, 'prepared')
        self.assertIn('已有供应商', first.apply_note)
        self.assertEqual(self.env['res.partner'].search_count([]), vendors)
        self.assertEqual(self.env['product.product'].search_count([]), products)

    def test_prepare_failure_retains_prior_editable_output(self):
        pack = self.make_pack()
        pack.action_prepare()
        first = pack.line_ids[0]
        before = first.read(['model_number', 'unit_price_text', 'followup_draft', 'extraction_snapshot'])[0]
        first.raw_text += '\n补充说明：需要重新核对交期。'
        with patch.object(quote_pack, 'extract_quotes', side_effect=ValueError('模拟解析失败')):
            pack.action_prepare()
        self.assertEqual(first.read(list(before.keys() - {'id'}))[0], before)
        self.assertIn('原有报价和关联已保留', pack.last_result)

    def test_apply_failure_preserves_preparation_and_does_not_make_order(self):
        pack = self.make_pack()
        pack.action_prepare()
        first = pack.line_ids[0]
        first.selected = True
        count = self.env['purchase.order'].search_count([])
        with patch.object(type(self.env['purchase.order']), 'create', side_effect=UserError('模拟采购写入失败')):
            pack.action_apply_selected()
        self.assertFalse(first.purchase_order_id)
        self.assertEqual(first.unit_price_text, '12000')
        self.assertTrue(first.followup_draft)
        self.assertIn('保存询价草稿失败', first.apply_note)
        self.assertEqual(self.env['purchase.order'].search_count([]), count)

    def test_staff_cannot_read_or_prepare_commercial_quotes(self):
        pack = self.make_pack()
        with self.assertRaises(AccessError):
            pack.with_user(self.staff).read(['name', 'attention_summary'])
        with self.assertRaises(AccessError):
            pack.with_user(self.staff).action_prepare()
        with self.assertRaises(AccessError):
            pack.line_ids.with_user(self.staff).read(['unit_price_text'])

    def test_adapter_does_not_invent_unknown_values(self):
        parsed = extract_quote(self.scenario['vendors'][0]['raw_text'])
        self.assertNotIn('freight_text', parsed)
        parsed = extract_quote(self.scenario['vendors'][0]['raw_text'] + self.scenario['vendors'][0]['supplement'])
        self.assertEqual(parsed['freight_text'], '120')
        self.assertEqual(parsed['freight_tax_basis'], 'included')
        self.assertEqual(parsed['unit_price_text'], '12000')

    def test_model_understands_unlabelled_material_with_grounding(self):
        text = '给您报MO60-140油镜，买2个，每个人民币12,000元含税，税率13%。下单后10天交货，免运费，有效至2027年3月8日。'
        response = {'fields': {
            'model_number': {'value': 'MO60-140', 'source': 'MO60-140油镜'},
            'product_name': {'value': '油镜', 'source': 'MO60-140油镜'},
            'quantity_text': {'value': '2', 'source': '买2个'},
            'uom_text': {'value': '个', 'source': '买2个'},
            'currency_code': {'value': 'CNY', 'source': '人民币12,000元'},
            'unit_price_text': {'value': '12,000元', 'source': '每个人民币12,000元含税'},
            'tax_basis': {'value': 'included', 'source': '每个人民币12,000元含税'},
            'tax_rate_text': {'value': '13%', 'source': '税率13%'},
            'delivery_text': {'value': '下单后10天交货', 'source': '下单后10天交货'},
            'freight_text': {'value': '0', 'source': '免运费'},
            'valid_until': {'value': '2027-03-08', 'source': '有效至2027年3月8日'},
        }}
        pack = self.make_pack()
        first = pack.line_ids[0]
        first.raw_text = text
        with self.model_response(response) as request:
            # A separate material still requires its own supported response.
            request.side_effect = lambda instruction, data: response if data['source_text'] == text else {'fields': {}}
            pack.action_prepare()
        self.assertEqual(first.model_number, 'MO60-140')
        self.assertEqual(first.unit_price_text, '12000')
        self.assertEqual(first.freight_text, '0')
        self.assertEqual(str(first.valid_until), '2027-03-08')
        self.assertEqual(first.currency_id, self.currency)
        self.assertIn('含运费总额 24000', first.amount_summary)
        self.assertFalse(first.specification)
        self.assertIn('缺少规格', first.followup_draft)
        self.assertEqual(first.extraction_sources['quantity_text'], '买2个')

    def test_model_rejects_unsupported_fields_values_and_sources(self):
        text = '数量2，单价12000元含税，税率13%，交期10天。'
        invalid = [
            {'fields': {}, 'partner_id': 1},
            {'fields': {'partner_id': {'value': '1', 'source': text}}},
            {'fields': {'quantity_text': {'value': 2, 'source': '数量2'}}},
            {'fields': {'quantity_text': {'value': '5', 'source': '数量2'}}},
            {'fields': {'quantity_text': {'value': '2', 'source': '凭空引用数量2'}}},
            {'fields': {'tax_basis': {'value': 'excluded', 'source': '单价12000元含税'}}},
            {'fields': {'delivery_text': {'value': '交期10天', 'source': text, 'id': 1}}},
            {'fields': {'model_number': {'value': 'x' * 201, 'source': text}}},
            {'fields': {'freight_text': {'value': '0', 'source': text}}},
            {'fields': {'valid_until': {'value': '2027-03-08', 'source': text}}},
        ]
        for response in invalid:
            with self.subTest(response=response), self.assertRaises(DraftValidationError):
                validate_quote_response(response, text)

    def test_provider_cache_preserves_manual_fields_questions_and_rfq(self):
        pack = self.make_pack()
        pack.action_prepare()
        first = pack.line_ids[0]
        first.selected = True
        pack.action_apply_selected()
        native_line = first.purchase_order_line_id
        first.write({'unit_price_text': '12500', 'followup_draft': '请补盖章报价。'})
        local_fingerprint = first.extraction_fingerprint
        response = {'fields': {}}
        with self.model_response(response) as request:
            pack.action_prepare()
            self.assertEqual(request.call_count, 2)
            self.assertNotEqual(first.extraction_fingerprint, local_fingerprint)
            self.assertIn('quote-test', first.extraction_provider)
            pack.action_prepare()
            self.assertEqual(request.call_count, 2)
            self.assertIn('新整理 0', pack.last_result)
            with patch.object(model_client, 'provider_key', return_value='openai:another:endpoint'), \
                    patch.object(model_client, 'provider_label', return_value='AI 辅助整理 · another'):
                pack.action_prepare()
            self.assertEqual(request.call_count, 4)
            self.assertIn('another', first.extraction_provider)
        self.assertEqual(first.unit_price_text, '12500')
        self.assertEqual(first.followup_draft, '请补盖章报价。')
        self.assertEqual(first.purchase_order_line_id, native_line)
        self.assertEqual(len(native_line.order_id.order_line), 1)

    def test_model_failure_preserves_all_existing_preparation(self):
        pack = self.make_pack()
        pack.action_prepare()
        first = pack.line_ids[0]
        first.selected = True
        pack.action_apply_selected()
        first.followup_draft = '采购手工补问。'
        tracked = ['model_number', 'unit_price_text', 'followup_draft', 'extraction_snapshot',
                   'extraction_sources', 'extraction_fingerprint', 'extraction_provider',
                   'purchase_order_id', 'purchase_order_line_id']
        before = pack.line_ids.read(tracked)
        for error, response in (
                (model_client.ModelRequestError('模型请求失败（HTTP 403）。'), None),
                (model_client.ModelRequestError('模型请求超时。'), None),
                (None, {'fields': {'partner_id': {'value': '1', 'source': '1'}}})):
            with self.subTest(error=error), self.model_response(response, error), \
                    patch.object(quote_adapter, 'extract_local_quote', side_effect=AssertionError('不能静默本地回退')):
                pack.action_prepare()
            self.assertEqual(pack.line_ids.read(tracked), before)
            self.assertIn('整理失败', pack.last_result)
            self.assertIn('原有报价和关联已保留', pack.last_result)
            self.assertIn('人工编辑', pack.last_result)

    def test_identical_materials_share_one_extraction(self):
        pack = self.make_pack()
        pack.line_ids.write({'raw_text': '买2个油镜，交期10天。'})
        response = {'fields': {'quantity_text': {'value': '2', 'source': '买2个'}}}
        with self.model_response(response) as request:
            pack.action_prepare()
            pack.action_prepare()
        self.assertEqual(request.call_count, 1)
        self.assertEqual(pack.line_ids.mapped('quantity_text'), ['2', '2'])
        self.assertEqual(len(pack.line_ids), 2)
