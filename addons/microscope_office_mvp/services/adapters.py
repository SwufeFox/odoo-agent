"""A deterministic local demo adapter. This module has no model or network client."""

import re
from datetime import date
from time import monotonic

from .schema import (
    ADAPTER_TIMEOUT_SECONDS,
    MAX_ACTIONS,
    MAX_EXCERPT_CHARS,
    DraftValidationError,
    validate_input,
)

_ACTION = re.compile(
    r'^\s*(?:[-*•]\s*)?(?:\[\s*\]\s*)?(?:\d+[.)、]\s*)?'
    r'(?:行动项|待办|任务|TODO|action\s+item)\s*[:：]\s*(.+)$',
    re.IGNORECASE,
)
_ASSIGNEE = re.compile(r'(?:负责人|责任人|assignee|owner)\s*[:：]\s*([^；;|，,]+)', re.IGNORECASE)
_DEADLINE = re.compile(r'(?:截止日期|截止|期限|deadline|due)\s*[:：]\s*([^；;|，,]+)', re.IGNORECASE)
_METADATA = re.compile(r'[；;|]\s*(?:负责人|责任人|assignee|owner|截止日期|截止|期限|deadline|due)\s*[:：]', re.IGNORECASE)
_ISO_DATE = re.compile(r'\d{4}-\d{2}-\d{2}')


class RuleBasedDemoAdapter:
    """Extract explicitly marked lines; names and dates stay untrusted suggestions.

    ``extract(raw_content, *, deadline=None)`` returns the schema's plain dict.
    The monotonic deadline is cooperative: this fixed adapter does only bounded
    local work and checks it on each line. No threads, subprocesses or plugins
    can keep running after an extraction failure.
    """

    def extract(self, raw_content, *, deadline=None):
        raw_content = validate_input(raw_content)
        if deadline is None:
            deadline = monotonic() + ADAPTER_TIMEOUT_SECONDS
        actions = []
        for line in raw_content.splitlines():
            if monotonic() >= deadline:
                raise TimeoutError('本地草稿提取超时，请重试或手动添加行动项。')
            match = _ACTION.fullmatch(line)
            if not match:
                continue
            excerpt = line.strip()
            if len(excerpt) > MAX_EXCERPT_CHARS:
                raise DraftValidationError(f'单个行动项原文超过 {MAX_EXCERPT_CHARS} 字，请拆分或手动添加。')
            body = match.group(1).strip()
            name = _METADATA.split(body, maxsplit=1)[0].strip(' ；;|')
            owner = _ASSIGNEE.search(body)
            due = _DEADLINE.search(body)
            suggested_owner = owner.group(1).strip() if owner else ''
            suggested_due = due.group(1).strip() if due else ''
            valid_date = False
            if _ISO_DATE.fullmatch(suggested_due):
                try:
                    date.fromisoformat(suggested_due)
                    valid_date = True
                except ValueError:
                    pass
            actions.append({
                'name': name,
                'suggested_assignee_name': suggested_owner,
                'suggested_deadline': suggested_due,
                'source_excerpt': excerpt,
                'ambiguous': not bool(suggested_owner and valid_date),
            })
            if len(actions) > MAX_ACTIONS:
                raise DraftValidationError(f'一次最多提取 {MAX_ACTIONS} 个行动项，请分段处理或手动添加。')
        if monotonic() >= deadline:
            raise TimeoutError('本地草稿提取超时，请重试或手动添加行动项。')
        return {
            'summary': f'本地规则识别 {len(actions)} 个行动项；姓名与日期仅为建议，须由主持人逐项确认。',
            'actions': actions,
        }
