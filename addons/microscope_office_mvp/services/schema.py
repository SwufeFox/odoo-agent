"""Strict, bounded data contract for the local meeting draft assistant."""

from dataclasses import dataclass

MAX_INPUT_CHARS = 20_000
MAX_ACTIONS = 20
MAX_SUMMARY_CHARS = 2_000
MAX_NAME_CHARS = 200
MAX_SUGGESTION_CHARS = 100
MAX_EXCERPT_CHARS = 1_000
MAX_OUTPUT_CHARS = 32_000
ADAPTER_TIMEOUT_SECONDS = 1.0


class DraftValidationError(ValueError):
    """The local assistant returned unusable draft data."""


@dataclass(frozen=True, slots=True)
class ActionDraft:
    name: str
    suggested_assignee_name: str
    suggested_deadline: str
    source_excerpt: str
    ambiguous: bool


@dataclass(frozen=True, slots=True)
class MeetingDraft:
    summary: str
    actions: tuple[ActionDraft, ...]


def validate_input(raw_content):
    if not isinstance(raw_content, str) or not raw_content.strip():
        raise DraftValidationError('请先填写会议原文，也可以直接手动添加行动项。')
    if len(raw_content) > MAX_INPUT_CHARS:
        raise DraftValidationError(f'会议原文超过 {MAX_INPUT_CHARS} 字，请缩短原文或手动添加行动项。')
    return raw_content


def _text(value, field_name, maximum, *, required=False):
    if not isinstance(value, str):
        raise DraftValidationError(f'草稿字段 {field_name} 必须是文本。')
    if len(value) > maximum:
        raise DraftValidationError(f'草稿字段 {field_name} 超过 {maximum} 字。')
    value = value.strip()
    if required and not value:
        raise DraftValidationError(f'草稿字段 {field_name} 不能为空。')
    return value


def validate_draft_payload(payload, raw_content):
    """Accept only suggestion text; ORM identifiers and formal assignments never enter here."""
    validate_input(raw_content)
    if type(payload) is not dict or set(payload) != {'summary', 'actions'}:
        raise DraftValidationError('草稿必须且只能包含 summary 和 actions。')
    summary = _text(payload['summary'], 'summary', MAX_SUMMARY_CHARS, required=True)
    actions = payload['actions']
    if type(actions) is not list or not actions:
        raise DraftValidationError('未识别到行动项，请按“行动项：事项；负责人：姓名；截止：日期”填写，或手动添加。')
    if len(actions) > MAX_ACTIONS:
        raise DraftValidationError(f'一次最多提取 {MAX_ACTIONS} 个行动项，请分段处理或手动添加。')
    required = {'name', 'source_excerpt', 'ambiguous'}
    allowed = required | {'suggested_assignee_name', 'suggested_deadline'}
    result = []
    total = len(summary)
    for item in actions:
        if type(item) is not dict or not required <= set(item) or set(item) - allowed:
            raise DraftValidationError('行动项字段不完整或包含未允许字段；不接受用户编号、正式负责人或正式截止日期。')
        name = _text(item['name'], 'name', MAX_NAME_CHARS, required=True)
        excerpt = _text(item['source_excerpt'], 'source_excerpt', MAX_EXCERPT_CHARS, required=True)
        assignee = _text(item.get('suggested_assignee_name', ''), 'suggested_assignee_name', MAX_SUGGESTION_CHARS)
        deadline = _text(item.get('suggested_deadline', ''), 'suggested_deadline', MAX_SUGGESTION_CHARS)
        if type(item['ambiguous']) is not bool:
            raise DraftValidationError('ambiguous 必须为布尔值。')
        if excerpt not in raw_content:
            raise DraftValidationError('行动项引用必须来自会议原文，请手动核对。')
        total += len(name) + len(excerpt) + len(assignee) + len(deadline)
        if total > MAX_OUTPUT_CHARS:
            raise DraftValidationError(f'草稿输出超过 {MAX_OUTPUT_CHARS} 字，请分段处理。')
        result.append(ActionDraft(name, assignee, deadline, excerpt, item['ambiguous']))
    return MeetingDraft(summary, tuple(result))
