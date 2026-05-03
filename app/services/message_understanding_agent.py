from __future__ import annotations

import json
import logging
import re
from typing import Any

from openai import AsyncOpenAI

from app.core.llm_factory import create_cloud_llm
from app.core.llm_provider import get_llm_router
from app.agent.nodes.router import detect_out_of_scope_reason
from app.services.agentic_request_context import AgenticRequestContext
from app.services.memory_resolver import (
    extract_pending_subject_reference_clarification,
    looks_like_subject_reference_clarification_response,
)

logger = logging.getLogger(__name__)

_SUPPORTED_INTENTS = {
    "subject_reference",
    "clarification_response",
    "fact_update",
    "confirm_previous_fact",
    "legal_question",
    "advice_question",
    "document_request",
    "unknown",
}
_SUPPORTED_ROUTE_HINTS = {
    "DOSSIER_MEMORY",
    "CASE_UPDATE",
    "LEGAL_QA",
    "GENERAL_CONSULT",
    "DOCUMENT_ASSIST",
    "OUT_OF_SCOPE",
    "UNKNOWN",
}
_SUPPORTED_CONFIDENCE = {"high", "medium", "low"}
_SUPPORTED_SUBJECT_TYPES = {"organization", "person", "self", "role", "unknown"}
_SUPPORTED_CONFIRM_ACTIONS = {"confirm", "reject", "unclear"}
_SUPPORTED_SUBJECT_REFERENCE_SCOPES = {
    "current_conversation_graph",
    "current_conversation_messages",
    "dossier_confirmed_entities",
    "dossier_confirmed_participants",
    "dossier_display_graph",
    "dossier_cross_conversation_graph",
    "dossier_cross_conversation_candidates",
}
_SUBJECT_REFERENCE_SCOPE_ALIASES = {
    "dossier_confirmed_entities": "dossier_confirmed_participants",
    "dossier_confirmed_participants": "dossier_confirmed_participants",
    "dossier_cross_conversation_graph": "dossier_cross_conversation_candidates",
    "dossier_cross_conversation_candidates": "dossier_cross_conversation_candidates",
    "dossier_display_graph": "dossier_display_graph",
    "current_conversation_graph": "current_conversation_graph",
    "current_conversation_messages": "current_conversation_messages",
}
_MAX_RECENT_MESSAGES = 8
_MAX_MESSAGE_LENGTH = 500
_MAX_CONTEXT_LENGTH = 1600
_OPENAI_MODEL = "gpt-5.1"
_ZHIPU_MODEL = "glm-4-flash"
_FALLBACK_MODEL = "message-understanding-fallback-v1"
_EXPLICIT_LAW_ARTICLE_PATTERNS = (
    re.compile(r"(法|条例|规定)[^。！？\n]{0,20}第?[一二三四五六七八九十百千万两\d]+条"),
    re.compile(r"第?[一二三四五六七八九十百千万两\d]+条[^。！？\n]{0,12}(规定了什么|怎么规定|是什么|内容是什么)"),
)

_SYSTEM_PROMPT = """你是消息理解智能体，不负责提供法律建议。

你的任务是把当前消息理解为一个结构化 JSON，用于后续后端路由和智能体编排。

严格要求：
1. 只能输出一个 JSON object，不要输出 markdown，不要输出解释文字。
2. intent 只能是：
   - subject_reference
   - fact_update
   - confirm_previous_fact
   - legal_question
   - advice_question
   - document_request
   - unknown
3. route_hint 只能是：
   - DOSSIER_MEMORY
   - CASE_UPDATE
   - LEGAL_QA
   - GENERAL_CONSULT
   - DOCUMENT_ASSIST
   - UNKNOWN
4. 你不是法律顾问，不要输出法律建议、法条解释、维权方案。
5. 如果是主体指代问题，不要触发 legal RAG，应设置 needs_legal_rag=false、needs_dossier_graph_context=true。
6. 如果是事实补充或确认上一条事实，应设置 needs_structured_persistence=true、needs_graph_update=true。
7. 如果是确认上一条事实，必须优先从 recent_messages 中寻找最近一条适合被确认的 user factual message。
8. 如果 recent_messages 不足以定位上一条事实，不要编造 target_text；返回 unknown，或返回低置信度的 confirm_previous_fact 并明确 reason=no_previous_fact_found。
9. 如果是明确法条/法规定义问题，应识别为 legal_question，并设置 needs_legal_rag=true。
10. 如果信息不足，返回 unknown，保持保守。
11. 如果是主体指代问题，请输出一个 reference resolution plan，明确 reference_type、scope_order，以及是否在歧义时要求澄清。
12. 如果用户询问已记录案情事实（例如“劳动者任职的公司叫什么”“被拖欠的工资是多少”“谁拖欠了劳动者工资”“有哪些证据”），这属于案卷记忆问答，不是法律咨询、不是新增事实；应识别为 subject_reference，route_hint=DOSSIER_MEMORY，needs_dossier_graph_context=true，needs_structured_persistence=false，needs_graph_update=false。这里的 subject_reference 表示“案卷事实记忆解析计划”，不只限于主体是谁。

请输出包含以下顶层字段的 JSON：
{
  "intent": "...",
  "route_hint": "...",
  "needs_legal_rag": false,
  "needs_dossier_graph_context": false,
  "needs_structured_persistence": false,
  "needs_graph_update": false,
  "subject_reference": null,
  "fact_update": null,
  "previous_fact_confirmation": null,
  "advice_question": null,
  "legal_question": null,
  "confidence": "high",
  "reason": ""
}

可选结构：
- subject_reference:
  {
    "enabled": true,
    "target_span": "这里的公司",
    "mention": "公司",
    "target_mention": "公司",
    "target_type": "organization",
    "reference_type": "organization",
    "question": "这里的公司指的是哪家公司？",
    "resolution_strategy": "use_dossier_context",
    "scope_order": [
      "current_conversation_graph",
      "current_conversation_messages",
      "dossier_confirmed_participants",
      "dossier_display_graph",
      "dossier_cross_conversation_candidates"
    ],
    "needs_clarification_if_ambiguous": true
  }
- fact_update:
  {
    "enabled": true,
    "fact_text": "...",
    "entities": [{"label": "B公司", "type": "organization"}],
    "events": [{"summary": "...", "participants": ["王经理", "当事人"]}]
  }
- previous_fact_confirmation:
  {
    "enabled": true,
    "action": "confirm",
    "confirmation_text": "我确认这个事实",
    "target_message_id": 123,
    "target_text": "...",
    "target_fact_summary": "..."
  }
"""


def _normalize_text(value: Any, *, limit: int | None = None) -> str:
    text = " ".join(str(value or "").strip().split())
    if limit is not None and len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
    return default


def _coerce_int(value: Any) -> int | None:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized


def _build_base_result() -> dict[str, Any]:
    return {
        "source": "message_understanding_agent",
        "intent": "unknown",
        "route_hint": "UNKNOWN",
        "needs_legal_rag": False,
        "needs_dossier_graph_context": False,
        "needs_structured_persistence": False,
        "needs_graph_update": False,
        "subject_reference": None,
        "clarification_response": None,
        "fact_update": None,
        "previous_fact_confirmation": None,
        "advice_question": None,
        "legal_question": None,
        "document_request": None,
        "confidence": "low",
        "reason": "",
        "provider": "fallback",
        "model": _FALLBACK_MODEL,
        "fallback_reason": None,
        "trace": {"steps": [], "warnings": []},
    }


def _extract_json_object(raw_content: str) -> dict[str, Any] | None:
    text = str(raw_content or "").strip()
    if not text:
        return None

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None

    return parsed if isinstance(parsed, dict) else None


def _intent_defaults(intent: str) -> dict[str, Any]:
    defaults = {
        "route_hint": "UNKNOWN",
        "needs_legal_rag": False,
        "needs_dossier_graph_context": False,
        "needs_structured_persistence": False,
        "needs_graph_update": False,
    }
    if intent == "subject_reference":
        defaults.update(
            route_hint="DOSSIER_MEMORY",
            needs_dossier_graph_context=True,
        )
    elif intent == "clarification_response":
        defaults.update(
            route_hint="DOSSIER_MEMORY",
            needs_dossier_graph_context=False,
        )
    elif intent == "fact_update":
        defaults.update(
            route_hint="CASE_UPDATE",
            needs_structured_persistence=True,
            needs_graph_update=True,
        )
    elif intent == "confirm_previous_fact":
        defaults.update(
            route_hint="CASE_UPDATE",
            needs_structured_persistence=True,
            needs_graph_update=True,
        )
    elif intent == "legal_question":
        defaults.update(
            route_hint="LEGAL_QA",
            needs_legal_rag=True,
        )
    elif intent == "advice_question":
        defaults.update(route_hint="GENERAL_CONSULT")
    elif intent == "document_request":
        defaults.update(route_hint="DOCUMENT_ASSIST")
    return defaults


def _normalize_subject_reference(value: Any, *, message: str) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    target_type = _normalize_text(value.get("target_type")).lower() or "unknown"
    if target_type not in _SUPPORTED_SUBJECT_TYPES:
        target_type = "unknown"

    reference_type = _normalize_text(value.get("reference_type")).lower() or target_type
    if reference_type not in _SUPPORTED_SUBJECT_TYPES:
        reference_type = target_type

    raw_scope_order = value.get("scope_order")
    scope_order: list[str] = []
    for item in raw_scope_order if isinstance(raw_scope_order, list) else []:
        normalized_scope = _SUBJECT_REFERENCE_SCOPE_ALIASES.get(_normalize_text(item))
        if (
            normalized_scope in _SUPPORTED_SUBJECT_REFERENCE_SCOPES
            and normalized_scope not in scope_order
        ):
            scope_order.append(normalized_scope)
    if not scope_order:
        scope_order = [
            "current_conversation_graph",
            "current_conversation_messages",
            "dossier_confirmed_participants",
            "dossier_display_graph",
            "dossier_cross_conversation_candidates",
        ]

    return {
        "enabled": _coerce_bool(value.get("enabled"), True),
        "target_span": _normalize_text(value.get("target_span")) or message,
        "mention": _normalize_text(value.get("mention")),
        "target_mention": _normalize_text(value.get("target_mention")) or _normalize_text(value.get("mention")),
        "target_type": target_type,
        "reference_type": reference_type,
        "question": _normalize_text(value.get("question")) or message,
        "resolution_strategy": _normalize_text(value.get("resolution_strategy")) or "use_dossier_context",
        "scope_order": scope_order,
        "needs_clarification_if_ambiguous": _coerce_bool(
            value.get("needs_clarification_if_ambiguous"),
            True,
        ),
    }


def _normalize_fact_update(value: Any, *, message: str) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    entities: list[dict[str, str]] = []
    for item in value.get("entities", []):
        if not isinstance(item, dict):
            continue
        label = _normalize_text(item.get("label"))
        if not label:
            continue
        entities.append(
            {
                "label": label,
                "type": _normalize_text(item.get("type")).lower() or "unknown",
            }
        )

    events: list[dict[str, Any]] = []
    for item in value.get("events", []):
        if not isinstance(item, dict):
            continue
        summary = _normalize_text(item.get("summary"))
        if not summary:
            continue
        participants = [
            _normalize_text(participant)
            for participant in item.get("participants", [])
            if _normalize_text(participant)
        ]
        events.append({"summary": summary, "participants": participants})

    return {
        "enabled": _coerce_bool(value.get("enabled"), True),
        "fact_text": _normalize_text(value.get("fact_text")) or message,
        "entities": entities,
        "events": events,
    }


def _normalize_previous_fact_confirmation(value: Any, *, message: str) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    action = _normalize_text(value.get("action")).lower() or "unclear"
    if action not in _SUPPORTED_CONFIRM_ACTIONS:
        action = "unclear"

    target_message_id = _coerce_int(value.get("target_message_id"))

    target_text = _normalize_text(value.get("target_text"))
    target_fact_summary = _normalize_text(value.get("target_fact_summary"))

    return {
        "enabled": _coerce_bool(value.get("enabled"), True),
        "action": action,
        "confirmation_text": _normalize_text(value.get("confirmation_text")) or message,
        "target_message_id": target_message_id,
        "target_text": target_text or None,
        "target_fact_summary": target_fact_summary or None,
    }


def _normalize_optional_question(value: Any, *, message: str) -> dict[str, Any] | None:
    if isinstance(value, dict):
        normalized = {
            key: value[key]
            for key in value
            if isinstance(key, str)
        }
        normalized.setdefault("enabled", True)
        normalized.setdefault("question", message)
        return normalized
    if isinstance(value, str) and _normalize_text(value):
        return {"enabled": True, "question": _normalize_text(value)}
    return None


def _apply_request_context_contract(
    result: dict[str, Any],
    *,
    request_context: AgenticRequestContext | None,
) -> dict[str, Any]:
    if not isinstance(request_context, AgenticRequestContext):
        return result

    result["context_summary"] = dict(request_context.context_summary)

    if result.get("intent") == "confirm_previous_fact":
        previous_fact_confirmation = (
            result.get("previous_fact_confirmation")
            if isinstance(result.get("previous_fact_confirmation"), dict)
            else None
        )
        previous_factual_user_message = request_context.previous_factual_user_message
        if isinstance(previous_fact_confirmation, dict) and isinstance(previous_factual_user_message, dict):
            previous_fact_confirmation["target_message_id"] = _coerce_int(
                previous_fact_confirmation.get("target_message_id")
            ) or _coerce_int(previous_factual_user_message.get("message_id"))
            previous_fact_confirmation["target_text"] = (
                _normalize_text(previous_fact_confirmation.get("target_text"))
                or _normalize_text(previous_factual_user_message.get("content"))
                or None
            )
            previous_fact_confirmation["target_fact_summary"] = (
                _normalize_text(previous_fact_confirmation.get("target_fact_summary"))
                or _normalize_text(previous_factual_user_message.get("content"))
                or None
            )
        elif isinstance(previous_fact_confirmation, dict):
            previous_fact_confirmation["target_message_id"] = None
            previous_fact_confirmation["target_text"] = None
            previous_fact_confirmation["target_fact_summary"] = None
            trace = result.get("trace") if isinstance(result.get("trace"), dict) else {"steps": [], "warnings": []}
            warnings = trace.get("warnings") if isinstance(trace.get("warnings"), list) else []
            if "no_previous_fact_found" not in warnings:
                warnings.append("no_previous_fact_found")
            trace["warnings"] = warnings
            result["trace"] = trace
            if not _normalize_text(result.get("reason")):
                result["reason"] = "no_previous_fact_found"

    return result


def _normalize_clarification_response(
    value: Any,
    *,
    message: str,
    pending_clarification: dict[str, Any] | None,
) -> dict[str, Any] | None:
    payload = value if isinstance(value, dict) else {}
    pending = pending_clarification if isinstance(pending_clarification, dict) else {}
    target_mention = _normalize_text(
        payload.get("target_mention") or pending.get("target_mention")
    )
    reference_type = _normalize_text(
        payload.get("reference_type") or pending.get("reference_type")
    ).lower() or "unknown"
    scope_attempted = pending.get("scope_attempted") if isinstance(pending.get("scope_attempted"), list) else []
    normalized_scope_attempted: list[str] = []
    for item in scope_attempted:
        normalized_scope = _SUBJECT_REFERENCE_SCOPE_ALIASES.get(_normalize_text(item))
        if normalized_scope and normalized_scope not in normalized_scope_attempted:
            normalized_scope_attempted.append(normalized_scope)
    return {
        "enabled": True,
        "question": message,
        "target_mention": target_mention,
        "reference_type": reference_type if reference_type in _SUPPORTED_SUBJECT_TYPES else "unknown",
        "scope_attempted": normalized_scope_attempted,
        "expected_response": _normalize_text(
            payload.get("expected_response") or pending.get("expected_response")
        ) or "entity_name_or_context",
        "pending_graph_update_input_text": _normalize_text(
            payload.get("pending_graph_update_input_text")
            or pending.get("pending_graph_update_input_text")
        ),
    }


def _normalize_llm_result(
    parsed: dict[str, Any],
    *,
    message: str,
    provider: str,
    model: str,
    trace: dict[str, Any],
) -> dict[str, Any]:
    result = _build_base_result()
    result["provider"] = provider
    result["model"] = model
    result["fallback_reason"] = None
    result["trace"] = trace

    out_of_scope_reason = detect_out_of_scope_reason(message)
    explicit_law_question = _looks_like_explicit_law_question(message)

    intent = _normalize_text(parsed.get("intent")).lower() or "unknown"
    if intent not in _SUPPORTED_INTENTS:
        trace["warnings"].append(f"unsupported_intent:{intent}")
        intent = "unknown"

    if out_of_scope_reason:
        trace["warnings"].append(f"override_out_of_scope:{out_of_scope_reason}")
        intent = "unknown"
        parsed = {
            **parsed,
            "intent": "unknown",
            "route_hint": "OUT_OF_SCOPE",
            "needs_legal_rag": False,
            "needs_dossier_graph_context": False,
            "needs_structured_persistence": False,
            "needs_graph_update": False,
            "legal_question": None,
            "reason": f"Deterministic out-of-scope guard: {out_of_scope_reason}.",
        }
    elif explicit_law_question and intent != "legal_question":
        trace["warnings"].append("override_explicit_law_article_question")
        intent = "legal_question"
        parsed = {
            **parsed,
            "intent": "legal_question",
            "route_hint": "LEGAL_QA",
            "needs_legal_rag": True,
            "needs_dossier_graph_context": False,
            "needs_structured_persistence": False,
            "needs_graph_update": False,
            "legal_question": {"enabled": True, "question": message},
            "reason": _normalize_text(parsed.get("reason"))
            or "Deterministic explicit law article question override.",
        }

    defaults = _intent_defaults(intent)
    route_hint = _normalize_text(parsed.get("route_hint")).upper() or defaults["route_hint"]
    if route_hint not in _SUPPORTED_ROUTE_HINTS:
        trace["warnings"].append(f"unsupported_route_hint:{route_hint}")
        route_hint = defaults["route_hint"]

    confidence = _normalize_text(parsed.get("confidence")).lower() or "low"
    if confidence not in _SUPPORTED_CONFIDENCE:
        confidence = "low"

    result.update(
        intent=intent,
        route_hint=route_hint,
        needs_legal_rag=_coerce_bool(parsed.get("needs_legal_rag"), defaults["needs_legal_rag"]),
        needs_dossier_graph_context=_coerce_bool(
            parsed.get("needs_dossier_graph_context"),
            defaults["needs_dossier_graph_context"],
        ),
        needs_structured_persistence=_coerce_bool(
            parsed.get("needs_structured_persistence"),
            defaults["needs_structured_persistence"],
        ),
        needs_graph_update=_coerce_bool(
            parsed.get("needs_graph_update"),
            defaults["needs_graph_update"],
        ),
        confidence=confidence,
        reason=_normalize_text(parsed.get("reason")),
    )

    if intent == "subject_reference":
        result["subject_reference"] = _normalize_subject_reference(
            parsed.get("subject_reference"),
            message=message,
        ) or {
            "enabled": True,
            "target_span": message,
            "mention": "",
            "target_mention": "",
            "target_type": "unknown",
            "reference_type": "unknown",
            "question": message,
            "resolution_strategy": "use_dossier_context",
            "scope_order": [
                "current_conversation_graph",
                "current_conversation_messages",
                "dossier_confirmed_participants",
                "dossier_display_graph",
                "dossier_cross_conversation_candidates",
            ],
            "needs_clarification_if_ambiguous": True,
        }
    elif intent == "fact_update":
        result["fact_update"] = _normalize_fact_update(
            parsed.get("fact_update"),
            message=message,
        ) or {
            "enabled": True,
            "fact_text": message,
            "entities": [],
            "events": [],
        }
    elif intent == "confirm_previous_fact":
        result["previous_fact_confirmation"] = _normalize_previous_fact_confirmation(
            parsed.get("previous_fact_confirmation"),
            message=message,
        ) or {
            "enabled": True,
            "action": "unclear",
            "confirmation_text": message,
            "target_message_id": None,
            "target_text": None,
            "target_fact_summary": None,
        }
    elif intent == "advice_question":
        result["advice_question"] = _normalize_optional_question(
            parsed.get("advice_question"),
            message=message,
        ) or {"enabled": True, "question": message}
    elif intent == "legal_question":
        result["legal_question"] = _normalize_optional_question(
            parsed.get("legal_question"),
            message=message,
        ) or {"enabled": True, "question": message}

    if (
        result["intent"] == "confirm_previous_fact"
        and isinstance(result["previous_fact_confirmation"], dict)
        and not result["previous_fact_confirmation"].get("target_text")
        and not result["previous_fact_confirmation"].get("target_fact_summary")
    ):
        trace["warnings"].append("no_previous_fact_found")

    if result["intent"] == "clarification_response":
        result["clarification_response"] = _normalize_clarification_response(
            parsed.get("clarification_response"),
            message=message,
            pending_clarification=extract_pending_subject_reference_clarification(
                parsed.get("recent_messages") if isinstance(parsed.get("recent_messages"), list) else None
            ),
        ) or {
            "enabled": True,
            "question": message,
            "target_mention": "",
            "reference_type": "unknown",
            "scope_attempted": [],
            "expected_response": "entity_name_or_context",
        }

    return result


def _looks_like_explicit_law_question(message: str) -> bool:
    normalized = _normalize_text(message)
    if not normalized:
        return False
    return any(pattern.search(normalized) for pattern in _EXPLICIT_LAW_ARTICLE_PATTERNS)


def _build_fallback_result(
    *,
    message: str,
    fallback_reason: str,
    trace: dict[str, Any],
) -> dict[str, Any]:
    result = _build_base_result()
    result["provider"] = "fallback"
    result["model"] = _FALLBACK_MODEL
    result["fallback_reason"] = fallback_reason
    result["trace"] = trace

    out_of_scope_reason = detect_out_of_scope_reason(message)
    if out_of_scope_reason:
        result.update(
            intent="unknown",
            route_hint="OUT_OF_SCOPE",
            needs_legal_rag=False,
            confidence="high",
            reason=f"Fallback detected deterministic out-of-scope request: {out_of_scope_reason}.",
        )
        trace["warnings"].append(f"override_out_of_scope:{out_of_scope_reason}")
    elif _looks_like_explicit_law_question(message):
        result.update(
            intent="legal_question",
            route_hint="LEGAL_QA",
            needs_legal_rag=True,
            confidence="low",
            reason="Fallback detected an explicit law article question.",
            legal_question={"enabled": True, "question": message},
        )
    else:
        result.update(
            intent="unknown",
            route_hint="UNKNOWN",
            confidence="low",
            reason="LLM unavailable or invalid; conservative fallback returned unknown intent.",
        )

    trace["steps"].append("apply_fallback")
    trace["warnings"].append(fallback_reason)
    return result


def _build_pending_clarification_contract_result(
    *,
    message: str,
    pending_clarification: dict[str, Any],
    trace: dict[str, Any],
) -> dict[str, Any]:
    result = _build_base_result()
    result.update(
        intent="clarification_response",
        route_hint="DOSSIER_MEMORY",
        needs_legal_rag=False,
        needs_dossier_graph_context=False,
        needs_structured_persistence=False,
        needs_graph_update=False,
        confidence="high",
        reason="Pending subject reference clarification detected from recent assistant metadata.",
        provider="contract",
        model="pending-clarification-contract-v1",
        fallback_reason=None,
        trace=trace,
    )
    result["clarification_response"] = _normalize_clarification_response(
        {},
        message=message,
        pending_clarification=pending_clarification,
    )
    trace["steps"].append("detect_pending_clarification")
    return result


def _serialize_recent_messages(recent_messages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for item in list(recent_messages or [])[-_MAX_RECENT_MESSAGES:]:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else None
        serialized.append(
            {
                "message_id": _coerce_int(item.get("message_id") or item.get("id")),
                "role": _normalize_text(item.get("role")) or "user",
                "content": _normalize_text(item.get("content"), limit=_MAX_MESSAGE_LENGTH),
                "metadata": metadata,
            }
        )
    return serialized


def _serialize_context(value: Any) -> str:
    if value is None:
        return ""
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        text = _normalize_text(value)
    return _normalize_text(text, limit=_MAX_CONTEXT_LENGTH)


def _build_prompt_messages(
    *,
    message: str,
    recent_messages: list[dict[str, Any]] | None,
    conversation_mode: str | None,
    dossier_id: int | None,
    dossier_context: dict[str, Any] | None,
    case_summary: str | None,
    context_summary: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    request_payload = {
        "current_message": message,
        "recent_messages": _serialize_recent_messages(recent_messages),
        "conversation_mode": _normalize_text(conversation_mode),
        "has_dossier_id": bool(dossier_id),
        "dossier_id": _coerce_int(dossier_id),
        "dossier_context": _serialize_context(dossier_context),
        "case_summary": _normalize_text(case_summary, limit=_MAX_CONTEXT_LENGTH),
        "context_summary": context_summary if isinstance(context_summary, dict) else {},
    }
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(request_payload, ensure_ascii=False, indent=2)},
    ]


def _resolve_provider_name(provider_hint: str | None) -> str | None:
    router = get_llm_router()
    hint = _normalize_text(provider_hint).lower()

    if hint == "openai":
        if router.openai_api_key:
            return "openai"
        if router.zhipu_api_key:
            return "zhipu"
        return None

    if hint == "zhipu":
        if router.zhipu_api_key:
            return "zhipu"
        if router.openai_api_key:
            return "openai"
        return None

    if router.openai_api_key:
        return "openai"
    if router.zhipu_api_key:
        return "zhipu"
    return None


async def _call_openai_json_model(messages: list[dict[str, str]]) -> dict[str, str]:
    router = get_llm_router()
    client = AsyncOpenAI(
        api_key=router.openai_api_key,
        base_url=router.openai_base_url,
        timeout=60.0,
    )
    try:
        response = await client.chat.completions.create(
            model=_OPENAI_MODEL,
            messages=messages,
            temperature=0.1,
            max_tokens=1400,
        )
        content = response.choices[0].message.content if response.choices else ""
        return {
            "provider": "openai",
            "model": _OPENAI_MODEL,
            "content": content if isinstance(content, str) else str(content or ""),
        }
    finally:
        await client.close()


async def _call_zhipu_json_model(messages: list[dict[str, str]]) -> dict[str, str]:
    llm = create_cloud_llm(model=_ZHIPU_MODEL, temperature=0.1, max_tokens=1400)
    response = await llm.ainvoke(messages)
    content = response.content if hasattr(response, "content") else response
    return {
        "provider": "zhipu",
        "model": _ZHIPU_MODEL,
        "content": content if isinstance(content, str) else str(content or ""),
    }


async def _call_message_understanding_llm(
    *,
    messages: list[dict[str, str]],
    provider_hint: str | None = None,
) -> dict[str, str]:
    provider = _resolve_provider_name(provider_hint)
    if provider == "openai":
        return await _call_openai_json_model(messages)
    if provider == "zhipu":
        return await _call_zhipu_json_model(messages)
    raise RuntimeError("No LLM provider configured for message understanding.")


async def understand_message_with_agent(
    *,
    message: str,
    recent_messages: list[dict] | None = None,
    conversation_mode: str | None = None,
    dossier_id: int | None = None,
    dossier_context: dict | None = None,
    case_summary: str | None = None,
    provider_hint: str | None = None,
    use_llm: bool = True,
    request_context: AgenticRequestContext | None = None,
) -> dict[str, Any]:
    if isinstance(request_context, AgenticRequestContext):
        normalized_message = request_context.message_text
        recent_messages = request_context.recent_messages
        conversation_mode = request_context.conversation_mode
        dossier_id = request_context.dossier_id
        dossier_context = request_context.dossier_context
        case_summary = request_context.case_summary
    else:
        normalized_message = _normalize_text(message)
    trace = {"steps": ["build_request"], "warnings": []}

    if not normalized_message:
        return _build_fallback_result(
            message="",
            fallback_reason="empty_message",
            trace=trace,
        )

    pending_clarification = (
        request_context.pending_clarification
        if isinstance(request_context, AgenticRequestContext)
        else extract_pending_subject_reference_clarification(recent_messages)
    )
    if looks_like_subject_reference_clarification_response(
        message=normalized_message,
        pending_clarification=pending_clarification,
    ):
        return _apply_request_context_contract(
            _build_pending_clarification_contract_result(
                message=normalized_message,
                pending_clarification=pending_clarification,
                trace=trace,
            ),
            request_context=request_context,
        )

    if not use_llm:
        return _apply_request_context_contract(
            _build_fallback_result(
                message=normalized_message,
                fallback_reason="llm_disabled",
                trace=trace,
            ),
            request_context=request_context,
        )

    messages = _build_prompt_messages(
        message=normalized_message,
        recent_messages=recent_messages,
        conversation_mode=conversation_mode,
        dossier_id=dossier_id,
        dossier_context=dossier_context,
        case_summary=case_summary,
        context_summary=(
            request_context.context_summary
            if isinstance(request_context, AgenticRequestContext)
            else None
        ),
    )
    trace["steps"].append("build_prompt")

    try:
        llm_response = await _call_message_understanding_llm(
            messages=messages,
            provider_hint=provider_hint,
        )
    except Exception as exc:
        logger.warning("Message understanding LLM call failed: %s", exc)
        trace["steps"].append("call_llm")
        return _apply_request_context_contract(
            _build_fallback_result(
                message=normalized_message,
                fallback_reason="llm_call_failed",
                trace=trace,
            ),
            request_context=request_context,
        )

    trace["steps"].append("call_llm")
    parsed = _extract_json_object(llm_response.get("content", ""))
    if parsed is None:
        logger.warning("Message understanding LLM returned invalid JSON payload")
        return _apply_request_context_contract(
            _build_fallback_result(
                message=normalized_message,
                fallback_reason="invalid_llm_json",
                trace=trace,
            ),
            request_context=request_context,
        )

    trace["steps"].append("parse_llm_json")
    normalized_result = _normalize_llm_result(
        parsed,
        message=normalized_message,
        provider=llm_response.get("provider", "fallback"),
        model=llm_response.get("model", _FALLBACK_MODEL),
        trace=trace,
    )
    trace["steps"].append("normalize_result")
    if normalized_result.get("intent") == "document_request":
        normalized_result["document_request"] = _normalize_optional_question(
            parsed.get("document_request"),
            message=normalized_message,
        ) or {"enabled": True, "question": normalized_message}
    return _apply_request_context_contract(
        normalized_result,
        request_context=request_context,
    )
