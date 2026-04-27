from __future__ import annotations

import re

from app.services.conversation_mode import (
    normalize_conversation_mode,
    normalize_persistence_consent,
)
from app.services.dossier_graph_tool import classify_dossier_graph_query

_FACT_UPDATE_PHRASES = (
    "我补充一下",
    "补充一下",
    "再补充一下",
    "更正一下",
    "纠正一下",
    "更新一下",
    "说明一下",
    "又发生了",
    "公司又通知",
    "对方又",
    "我找到了",
    "新增证据",
    "补充证据",
    "实际入职时间是",
    "工资其实是",
    "对方名称是",
    "补充事实",
    "补充情况",
)

_CONSULT_PHRASES = (
    "准备哪些证据",
    "哪些证据",
    "需要准备什么证据",
    "需要准备什么材料",
    "下一步怎么办",
    "接下来怎么办",
    "仲裁请求怎么写",
    "仲裁申请书怎么写",
    "仲裁申请怎么写",
    "能赔多少钱",
    "赔多少钱",
    "胜算",
    "风险",
    "流程",
    "法条怎么理解",
    "怎么理解",
    "帮我整理",
    "帮我起草",
    "帮我生成",
    "证据目录",
    "证据清单",
    "仲裁请求",
    "仲裁申请书",
)

_FACT_UPDATE_REGEXES = (
    re.compile(r"(今天|刚刚|后来).{0,12}(通知|发生|收到|回复|发来|签了|支付|转账|说了)"),
    re.compile(r"(今天|刚刚|后来).{0,12}(被辞退|辞退|解除|开除|调岗|停职|约谈)"),
    re.compile(r"(实际|真实).{0,8}(入职时间|工资|名称|身份证号|联系方式).{0,4}是"),
    re.compile(r"(找到|新增|补充).{0,8}(证据|聊天记录|工资流水|录音|合同|通知)"),
)

_QUESTION_HINTS = (
    "请问",
    "怎么办",
    "如何",
    "怎么",
    "哪些",
    "多少",
    "是否",
    "能否",
    "可否",
    "需不需要",
    "要不要",
)

_FACT_PREFIX_PATTERN = re.compile(
    r"^(?:我)?(?:再)?(?:补充一下(?:案情)?|更正一下(?:案情)?|说明一下(?:案情)?|补充事实|补充情况)[：:，,\s]*"
)


def _normalize_text(text: str | None) -> str:
    return " ".join((text or "").strip().split())


def _looks_like_fact_update(text: str) -> bool:
    if any(phrase in text for phrase in _FACT_UPDATE_PHRASES):
        return True
    return any(pattern.search(text) for pattern in _FACT_UPDATE_REGEXES)


def is_case_fact_update_message(text: str | None) -> bool:
    cleaned = _normalize_text(text)
    if not cleaned:
        return False

    if _looks_like_fact_update(cleaned):
        return True

    if classify_dossier_graph_query(cleaned) in {"memory", "advice", "legal"}:
        return False

    if "?" in cleaned or "？" in cleaned:
        return False

    return not any(hint in cleaned for hint in _QUESTION_HINTS)


def is_dossier_followup_question(text: str | None) -> bool:
    cleaned = _normalize_text(text)
    if not cleaned:
        return False

    if _looks_like_fact_update(cleaned):
        return False

    if classify_dossier_graph_query(cleaned) in {"memory", "advice", "legal"}:
        return True

    if any(phrase in cleaned for phrase in _CONSULT_PHRASES):
        return True

    if "?" in cleaned or "？" in cleaned:
        return True

    return any(hint in cleaned for hint in _QUESTION_HINTS)


def should_persist_structured_case_message(
    *,
    text: str | None,
    route_intent: str | None,
    persistence_consent: str | None,
    conversation_mode: str | None = None,
    has_dossier_context: bool = False,
) -> bool:
    cleaned = _normalize_text(text)
    if not cleaned:
        return False

    normalized_consent = normalize_persistence_consent(persistence_consent)
    if normalized_consent == "temporary":
        return False

    normalized_route = str(route_intent or "").strip().upper()
    normalized_mode = normalize_conversation_mode(conversation_mode)
    message_kind = classify_dossier_graph_query(cleaned)

    if normalized_route == "CASE_UPDATE":
        if not has_dossier_context and normalized_consent != "tracked" and normalized_mode != "TRACKED_CASE":
            return True
        return message_kind not in {"memory", "advice", "legal"} and is_case_fact_update_message(cleaned)

    if normalized_consent != "tracked":
        return False

    if normalized_mode != "TRACKED_CASE" and not has_dossier_context:
        return False

    return message_kind not in {"memory", "advice", "legal"} and is_case_fact_update_message(cleaned)


def build_structured_case_summary(
    *,
    current_summary: str | None,
    user_message: str | None,
    proposed_summary: str | None,
) -> str:
    current = _normalize_text(current_summary)
    proposed = _normalize_text(proposed_summary)
    fact_text = _FACT_PREFIX_PATTERN.sub("", _normalize_text(user_message))

    if proposed and proposed != current:
        return proposed

    if current and fact_text:
        if fact_text in current:
            return current
        return f"{current}\n补充事实：{fact_text}"

    return fact_text or proposed or current
