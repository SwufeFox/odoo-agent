from datetime import timedelta
from decimal import Decimal
import logging
import re

from odoo import api, Command, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..services import model_client
from ..services.quote_adapter import decimal_value, extract_quotes, numerical_aperture, quote_fingerprint
from ..services.schema import DraftValidationError


_logger = logging.getLogger(__name__)
QUIET_CONTEXT = dict(mail_create_nosubscribe=True, mail_create_nolog=True,
                     mail_notrack=True, tracking_disable=True)
BASIS = [('unknown', '未注明'), ('included', '含税'), ('excluded', '未税')]
EXTRACTED_FIELDS = (
    'product_name', 'model_number', 'specification', 'quantity_text', 'uom_text',
    'unit_price_text', 'tax_basis', 'tax_rate_text', 'freight_text',
    'freight_tax_basis', 'freight_tax_rate_text', 'delivery_text', 'valid_until',
)


class QuotePack(models.Model):
    _name = 'mops.quote.pack'
    _description = '供应商报价准备包'
    _check_company_auto = True
    _order = 'write_date desc, id desc'

    name = fields.Char('采购事项', required=True)
    company_id = fields.Many2one('res.company', '公司', required=True, default=lambda self: self.env.company)
    product_id = fields.Many2one('product.product', '采购目标产品', check_company=True, domain=[('purchase_ok', '=', True)])
    target_spec = fields.Text('目标规格')
    line_ids = fields.One2many('mops.quote.line', 'pack_id', '供应商报价', copy=True)
    purchase_order_id = fields.Many2one('purchase.order', '关联询价草稿', check_company=True, copy=False)
    attention_summary = fields.Text('待处理事项', compute='_compute_attention', compute_sudo=False)
    comparison_summary = fields.Text('比较说明', compute='_compute_attention', compute_sudo=False)
    last_result = fields.Text('最近处理结果', readonly=True, copy=False)
    adapter_label = fields.Char('当前整理方式', compute='_compute_adapter_label')

    def _compute_adapter_label(self):
        try:
            label = model_client.provider_label()
        except DraftValidationError:
            label = '模型配置无效；可继续人工编辑报价'
        for pack in self:
            pack.adapter_label = label

    @api.depends('line_ids.attention_summary', 'line_ids.vendor_name', 'line_ids.model_number',
                 'line_ids.specification', 'line_ids.currency_id', 'line_ids.uom_text', 'line_ids.quantity_text')
    def _compute_attention(self):
        for pack in self:
            pack.attention_summary = '\n'.join(
                f'{line.vendor_name}：{line.attention_summary}'
                for line in pack.line_ids if line.attention_summary)
            differences = []
            for field, label in (('model_number', '原厂型号'), ('specification', '原始规格'),
                                 ('currency_id', '币种'), ('uom_text', '单位'), ('quantity_text', '数量')):
                values = {line[field].id if field == 'currency_id' else line[field] for line in pack.line_ids}
                if len(values) > 1:
                    differences.append(f'{label}存在差异，不能直接按总价判定可替代性。')
            pack.comparison_summary = '\n'.join(differences) or '仅展示已明确的报价口径与代码计算金额；供应商规格需由采购或研发核对。'

    def _check_actor(self):
        self.check_access('write')
        if not self.env.user.has_group('microscope_office_mvp.group_procurement'):
            raise AccessError('只有采购人员可以整理或采用报价。')

    def action_prepare(self):
        self._check_actor()
        for pack in self:
            attempted = '配置检查'
            try:
                with self.env.cr.savepoint():
                    pack.lock_for_update()
                    pack.invalidate_recordset(['line_ids'])
                    provider = model_client.provider_key()
                    attempted = model_client.provider_label()
                    lines = pack.line_ids
                    lines.check_access('read')
                    lines.check_access('write')
                    lines.invalidate_recordset()
                    fingerprints = {line.id: quote_fingerprint(line.raw_text, provider) for line in lines}
                    results = {
                        fingerprints[line.id]: {'values': line.extraction_snapshot or {},
                                                'sources': line.extraction_sources or {}}
                        for line in lines if line.extraction_fingerprint == fingerprints[line.id]
                    }
                    pending = {fingerprints[line.id]: line.raw_text or '' for line in lines
                               if fingerprints[line.id] not in results}
                    # Snapshot all authorized text on this thread, then do HTTP only in workers.
                    fresh = extract_quotes(list(pending.values())) if pending else []
                    results.update(zip(pending, fresh))
                    for line in lines:
                        fingerprint = fingerprints[line.id]
                        line._prepare_incremental(results[fingerprint], fingerprint, attempted)
                    pack.last_result = (
                        f'本次方式：{attempted}。新整理 {len(pending)} 份不同材料，'
                        f'复用 {len(lines) - len(pending)} 行同原文、同处理方式的提取结果。'
                        '人工修改、补问和已有草稿关联已保留。')
            except DraftValidationError as error:
                pack.last_result = (
                    f'本次方式：{attempted}；整理失败：{error} '
                    '原有报价和关联已保留，未回退为其他处理方式；可直接人工编辑字段和补问后采用。')
            except Exception:
                _logger.exception('Quotation preparation failed for pack %s', pack.id)
                pack.last_result = (
                    f'本次方式：{attempted}；本次整理失败，原有报价和关联已保留。'
                    '请核对材料中的日期及字段格式后重试，也可直接人工编辑后采用。')
        return True

    def action_apply_selected(self):
        self._check_actor()
        self.ensure_one()
        self.lock_for_update()
        self.invalidate_recordset(['line_ids', 'purchase_order_id'])
        selected = self.line_ids.filtered('selected')
        if not selected:
            raise UserError('请先选择需要采用的供应商报价行。')
        saved = 0
        for line in selected:
            try:
                with self.env.cr.savepoint():
                    line._apply_to_rfq()
                    saved += bool(line.purchase_order_line_id)
            except Exception:
                _logger.exception('Quotation application failed for line %s', line.id)
                line.write({'apply_state': 'prepared', 'apply_note': '保存询价草稿失败，报价准备记录和原有关联已保留；请检查采购权限和主数据后重试。'})
        self.last_result = f'已关联 {saved} 行原生询价草稿；其余选中行已保存为准备记录，请查看各行处理说明。未确认或发送订单。'
        return True

    @api.constrains('purchase_order_id', 'company_id')
    def _check_order_access(self):
        for pack in self.filtered('purchase_order_id'):
            pack.purchase_order_id.check_access('read')


class QuoteLine(models.Model):
    _name = 'mops.quote.line'
    _description = '可编辑供应商报价'
    _check_company_auto = True
    _order = 'sequence, id'
    _rec_name = 'vendor_name'

    pack_id = fields.Many2one('mops.quote.pack', '采购事项', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='pack_id.company_id', store=True, index=True)
    sequence = fields.Integer(default=10)
    vendor_name = fields.Char('供应商原文名称', required=True)
    raw_text = fields.Text('报价原始材料')
    selected = fields.Boolean('采用')
    partner_id = fields.Many2one('res.partner', '已有供应商', check_company=True)
    product_id = fields.Many2one('product.product', '已有产品', check_company=True, domain=[('purchase_ok', '=', True)])
    product_name = fields.Char('产品原文')
    model_number = fields.Char('原厂型号')
    specification = fields.Text('原始规格')
    specification_confirmed = fields.Boolean('已人工核对规格差异')
    quantity_text = fields.Char('数量', help='留空表示未知；只接受非负数字参与计算。')
    uom_text = fields.Char('原文单位')
    uom_id = fields.Many2one('uom.uom', '已有计量单位')
    currency_id = fields.Many2one('res.currency', '币种')
    unit_price_text = fields.Char('单价', help='留空表示未知，明确免费时填写0。')
    tax_basis = fields.Selection(BASIS, '单价税口径', default='unknown', required=True)
    tax_rate_text = fields.Char('税率(%)')
    tax_id = fields.Many2one('account.tax', '已有采购税', check_company=True,
                             domain=[('type_tax_use', '=', 'purchase'), ('amount_type', '=', 'percent')])
    freight_text = fields.Char('运费', help='整份此行报价的运费；留空表示未知，明确免运费填0。')
    freight_tax_basis = fields.Selection(BASIS, '运费税口径', default='unknown', required=True)
    freight_tax_rate_text = fields.Char('运费税率(%)', help='未税运费需单独明确税率，不沿用产品税率。')
    delivery_text = fields.Char('交期原文')
    planned_date = fields.Datetime('已确认预计交付日期', help='未知可留空，原生询价中的预计日期会明确标注为草稿估算。')
    valid_until = fields.Date('报价有效期')
    matching_note = fields.Text('已有主数据候选', readonly=True)
    attention_summary = fields.Text('缺项与差异', compute='_compute_summary', compute_sudo=False)
    amount_summary = fields.Char('含税金额计算', compute='_compute_summary', compute_sudo=False)
    followup_draft = fields.Text('发给供应商的补问草稿')
    extraction_snapshot = fields.Json('上次提取值', readonly=True, copy=False)
    extraction_fingerprint = fields.Char('原文与处理方式指纹', readonly=True, copy=False)
    extraction_sources = fields.Json('提取原文依据', readonly=True, copy=False)
    extraction_provider = fields.Char('最近成功提取方式', readonly=True, copy=False,
                                      help='记录最近成功提取的方式；当前报价仍可能包含此前事实及人工修改。')
    manual_fields = fields.Json('人工修改字段', readonly=True, copy=False)
    purchase_order_id = fields.Many2one('purchase.order', '原生询价草稿', check_company=True, copy=False)
    purchase_order_line_id = fields.Many2one('purchase.order.line', '原生询价行', check_company=True, copy=False)
    apply_state = fields.Selection([('draft', '待采用'), ('prepared', '已保存准备记录'), ('applied', '已写入询价草稿')], default='draft', copy=False, readonly=True)
    apply_note = fields.Text('处理说明', readonly=True, copy=False)

    def write(self, vals):
        tracked = set(EXTRACTED_FIELDS) | {'partner_id', 'product_id', 'uom_id', 'currency_id', 'tax_id', 'followup_draft', 'freight_tax_rate_text'}
        if not self.env.context.get('_quote_preparing') and tracked.intersection(vals):
            for line in self:
                updates = dict(vals, manual_fields=sorted(set(line.manual_fields or []) | tracked.intersection(vals)))
                super(QuoteLine, line).write(updates)
            return True
        return super().write(vals)

    @api.constrains('purchase_order_id', 'purchase_order_line_id', 'partner_id', 'company_id')
    def _check_links(self):
        for line in self:
            order = line.purchase_order_id
            if order:
                order.check_access('read')
                if line.partner_id and order.partner_id != line.partner_id:
                    raise ValidationError('报价供应商与关联询价单不一致。')
            if line.purchase_order_line_id:
                line.purchase_order_line_id.check_access('read')
                if line.purchase_order_line_id.order_id != order:
                    raise ValidationError('报价关联的询价行必须属于所选询价单。')

    def _issues(self):
        self.ensure_one()
        issues = []
        for field, label in (('model_number', '原厂型号'), ('specification', '规格'),
                             ('uom_text', '单位'), ('currency_id', '币种'),
                             ('delivery_text', '交期'), ('valid_until', '报价有效期')):
            if not self[field]:
                issues.append(f'缺少{label}')
        for field, label in (('quantity_text', '数量'), ('unit_price_text', '单价'),
                             ('tax_rate_text', '税率'), ('freight_text', '运费')):
            if decimal_value(self[field]) is None:
                issues.append(f'缺少{label}' if not self[field] else f'{label}格式需核对')
        if self.tax_basis == 'unknown':
            issues.append('缺少单价税口径')
        if decimal_value(self.freight_text) not in (None, Decimal('0')):
            if self.freight_tax_basis == 'unknown':
                issues.append('缺少运费税口径')
            if self.freight_tax_basis == 'excluded' and decimal_value(self.freight_tax_rate_text) is None:
                issues.append('缺少运费税率')
        target_na = numerical_aperture(self.pack_id.target_spec)
        source_na = numerical_aperture(self.specification)
        if target_na is not None and target_na != source_na:
            detail = f'NA {source_na} 与目标NA {target_na}不同' if source_na is not None else f'未注明NA，目标NA {target_na}'
            issues.append(f'规格差异：{detail}' + ('（已人工核对）' if self.specification_confirmed else '，不能自动等同'))
        if self.valid_until and self.valid_until < fields.Date.context_today(self):
            issues.append('报价有效期已过，请重新确认')
        return issues

    @api.depends('model_number', 'specification', 'quantity_text', 'uom_text', 'currency_id',
                 'unit_price_text', 'tax_basis', 'tax_rate_text', 'freight_text',
                 'freight_tax_basis', 'freight_tax_rate_text', 'delivery_text', 'valid_until',
                 'pack_id.target_spec', 'specification_confirmed')
    def _compute_summary(self):
        for line in self:
            line.attention_summary = '；'.join(line._issues())
            quantity, price, rate, freight = map(decimal_value, (
                line.quantity_text, line.unit_price_text, line.tax_rate_text, line.freight_text))
            if any(value is None for value in (quantity, price, rate)) or not line.currency_id or line.tax_basis == 'unknown':
                line.amount_summary = '待补齐数量、单价、币种与税口径后计算'
                continue
            goods = quantity * price * (1 + rate / 100 if line.tax_basis == 'excluded' else 1)
            currency = line.currency_id
            goods_display = f'{currency.round(float(goods)):g} {currency.name}'
            freight_rate = decimal_value(line.freight_tax_rate_text)
            if freight is None or (freight and (line.freight_tax_basis == 'unknown' or (line.freight_tax_basis == 'excluded' and freight_rate is None))):
                line.amount_summary = f'含税货款 {goods_display}；到手总额待补运费口径'
            else:
                shipping = freight * (1 + freight_rate / 100 if line.freight_tax_basis == 'excluded' and freight_rate is not None else 1)
                line.amount_summary = f'含税货款 {goods_display}；含运费总额 {currency.round(float(goods + shipping)):g} {currency.name}'

    def _prepare_incremental(self, result, fingerprint, provider):
        self.ensure_one()
        extracted = result['values']
        previous = self.extraction_snapshot or {}
        manual = set(self.manual_fields or [])
        updates = {}
        for name in EXTRACTED_FIELDS:
            if name not in extracted or name in manual:
                continue
            current = fields.Date.to_string(self[name]) if name == 'valid_until' else self[name]
            if current in (False, '', 'unknown') or current == previous.get(name):
                updates[name] = extracted[name]
        notes = []
        company_domain = [('company_id', 'in', [False, self.company_id.id])]
        if not self.partner_id and 'partner_id' not in manual:
            partners = self.env['res.partner'].search([('name', '=', self.vendor_name)] + company_domain, limit=6)
            if len(partners) == 1:
                updates['partner_id'] = partners.id
            else:
                candidates = partners or self.env['res.partner'].search([('name', 'ilike', self.vendor_name)] + company_domain, limit=5)
                notes.append('供应商候选：' + ('、'.join(candidates.mapped('display_name')) or '未匹配，请选择已有供应商'))
        if not self.product_id and 'product_id' not in manual:
            model = updates.get('model_number', self.model_number)
            products = self.env['product.product'].search([('default_code', '=', model), ('purchase_ok', '=', True)] + company_domain, limit=6) if model else self.env['product.product']
            if len(products) == 1:
                updates['product_id'] = products.id
            elif self.pack_id.product_id:
                notes.append(f'产品候选：{self.pack_id.product_id.display_name}；核对原厂型号与规格后选择')
            else:
                notes.append('产品未唯一匹配，请选择已有产品')
        if not self.currency_id and 'currency_id' not in manual and extracted.get('currency_code'):
            currency = self.env['res.currency'].search([('name', '=', extracted['currency_code'].upper())], limit=2)
            if len(currency) == 1:
                updates['currency_id'] = currency.id
        if not self.uom_id and 'uom_id' not in manual:
            unit_name = updates.get('uom_text', self.uom_text)
            units = self.env['uom.uom'].search([('name', '=', unit_name)], limit=2) if unit_name else self.env['uom.uom']
            if len(units) == 1:
                updates['uom_id'] = units.id
            elif unit_name:
                notes.append(f'单位“{unit_name}”未唯一匹配，请选择已有单位')
        updates.update(extraction_snapshot=extracted, matching_note='\n'.join(notes),
                       extraction_fingerprint=fingerprint, extraction_sources=result['sources'],
                       extraction_provider=provider)
        self.with_context(_quote_preparing=True).write(updates)
        if not self.tax_id and 'tax_id' not in manual and decimal_value(self.tax_rate_text) is not None:
            taxes = self._tax_candidates()
            if len(taxes) == 1:
                self.with_context(_quote_preparing=True).tax_id = taxes.id
        if 'followup_draft' not in manual:
            questions = self._issues()
            draft = f'{self.vendor_name}，您好。关于{self.model_number or self.product_name or self.pack_id.name}：\n'
            if questions:
                draft += '\n'.join(f'请确认：{question}。' for question in questions)
            else:
                draft += '已记录本次报价信息，后续采购安排由采购人员另行确认。'
            self.with_context(_quote_preparing=True).followup_draft = draft

    def _tax_candidates(self):
        rate = decimal_value(self.tax_rate_text)
        return self.env['account.tax'].search([
            ('company_id', '=', self.company_id.id), ('type_tax_use', '=', 'purchase'),
            ('amount_type', '=', 'percent'), ('amount', '=', float(rate)),
            ('include_base_amount', '=', False),
            ('price_include_override', '=', 'tax_included' if self.tax_basis == 'included' else 'tax_excluded'),
        ], limit=2) if rate is not None else self.env['account.tax']

    def _apply_to_rfq(self):
        self.ensure_one()
        if self.purchase_order_line_id:
            self.write({'apply_state': 'applied', 'apply_note': '已关联原生询价行，本次未重复创建或覆盖；新增运费等信息保留在本准备记录。'})
            return
        missing = []
        for field, label in (('partner_id', '已有供应商'), ('product_id', '已有产品'),
                             ('uom_id', '已有计量单位'), ('currency_id', '币种')):
            if not self[field]:
                missing.append(label)
        quantity, price, rate = map(decimal_value, (self.quantity_text, self.unit_price_text, self.tax_rate_text))
        if quantity is None or quantity <= 0:
            missing.append('正数数量')
        if price is None:
            missing.append('明确单价')
        if rate is None or rate > 100 or self.tax_basis == 'unknown':
            missing.append('明确税口径与税率')
        if any(issue.startswith('规格差异') for issue in self._issues()) and not self.specification_confirmed:
            missing.append('人工核对规格差异')
        tax = self.tax_id
        if not tax and rate is not None:
            candidates = self._tax_candidates()
            if len(candidates) == 1:
                tax = candidates
        if not tax:
            missing.append('唯一已有采购税（含零税率）')
        elif tax.type_tax_use != 'purchase' or tax.amount_type != 'percent' or Decimal(str(tax.amount)) != rate or tax.include_base_amount:
            missing.append('与报价一致的简单百分比采购税')
        if self.product_id and self.uom_id and self.uom_id not in (self.product_id.uom_id | self.product_id.uom_ids | self.product_id.extra_uom_ids):
            missing.append('产品允许的计量单位')
        if missing:
            self.write({'apply_state': 'prepared', 'apply_note': '已保存准备记录；创建询价草稿还需：' + '、'.join(missing)})
            return
        order = self.purchase_order_id or self.pack_id.purchase_order_id
        mode = 'tax_included' if self.tax_basis == 'included' else 'tax_excluded'
        if order and (order.partner_id != self.partner_id or order.currency_id != self.currency_id):
            if self.purchase_order_id:
                raise UserError('关联询价的供应商或币种与报价不一致。')
            order = self.env['purchase.order']
        if not order:
            order = self.pack_id.line_ids.filtered(lambda row: row.purchase_order_id and row.partner_id == self.partner_id and row.currency_id == self.currency_id).mapped('purchase_order_id').filtered(lambda po: po.state == 'draft')[:1]
        if order:
            order.check_access('write')
            if order.state != 'draft':
                raise UserError('只能写入未发送的询价草稿。')
            mode = order.document_tax_mode
        else:
            order = self.env['purchase.order'].with_context(**QUIET_CONTEXT).create({
                'partner_id': self.partner_id.id, 'company_id': self.company_id.id,
                'currency_id': self.currency_id.id, 'origin': self.pack_id.name,
                'document_tax_mode': mode,
            })
        net_price = price / (1 + rate / 100) if self.tax_basis == 'included' else price
        native_price = net_price * (1 + rate / 100) if mode == 'tax_included' else net_price
        date_planned = self.planned_date
        description = '\n'.join(filter(None, [self.product_name or self.product_id.display_name, self.model_number, self.specification,
            f'交期原文：{self.delivery_text or "未注明"}', f'报价有效期：{self.valid_until or "未注明"}',
            f'运费：{self.freight_text if self.freight_text else "未注明"}；运费保留于报价准备记录，未计入本产品行。']))
        if not date_planned:
            days = re.search(r'(\d+)\s*天', self.delivery_text or '')
            date_planned = fields.Datetime.now() + timedelta(days=int(days.group(1)) if days else 0)
            description += '\n预计交付日期为按草稿准备日推算的系统占位，尚非供应商承诺；以交期原文及后续确认为准。'
        order_line = self.env['purchase.order.line'].with_context(**QUIET_CONTEXT).create({
            'order_id': order.id, 'product_id': self.product_id.id, 'name': description,
            'product_qty': float(quantity), 'uom_id': self.uom_id.id,
            'price_unit': float(native_price), 'tax_ids': [Command.set(tax.ids)],
            'date_planned': date_planned, 'manufacturer_pn': self.model_number,
            'technical_spec': self.specification,
        })
        self.write({'purchase_order_id': order.id, 'purchase_order_line_id': order_line.id,
                    'apply_state': 'applied', 'apply_note': '已保存原生询价草稿。运费及未决事项保留于报价准备记录；未确认或发送。'})
        if not self.pack_id.purchase_order_id:
            self.pack_id.purchase_order_id = order
