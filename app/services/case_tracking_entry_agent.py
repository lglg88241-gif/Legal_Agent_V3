from __future__ import annotations

import logging
import re
from typing import Any

from app.services.conversation_mode import (
    detect_conversation_mode,
    normalize_conversation_mode,
    normalize_persistence_consent,
)
from app.services.message_understanding_agent import understand_message_with_agent

logger = logging.getLogger(__name__)

_CASE_FACT_INTENTS = {"fact_update", "confirm_previous_fact"}
_LEGAL_INTENTS = {"legal_question", "advice_question"}
_SUPPRESSED_INTENTS = {
    "subject_reference",
    "clarification_response",
    "document_request",
}
_SELF_ANCHORS = ("我", "本人", "当事人", "劳动者", "申请人", "我们")
_THIRD_PARTY_MARKERS = (
    "我的委托人",
    "委托人",
    "客户",
    "当事人称",
    "当事人说",
    "当事人表示",
    "其称",
    "其表示",
)
_EXPLICIT_PARTY_NAME_PATTERNS = (
    re.compile(r"(?:我叫|本人叫|我名叫)([\u4e00-\u9fff]{2,6})"),
    re.compile(r"(?:我的委托人|委托人|客户|当事人|劳动者|申请人)([\u4e00-\u9fff]{2,6})(?:称|说|表示|反映|认为)"),
    re.compile(r"([\u4e00-\u9fff]{2,6})(?:称|说|表示|反映|认为)[，,]"),
)


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _normalize_intent(value: Any) -> str:
    return _normalize_text(value).lower() or "unknown"


def _normalize_route(value: Any) -> str:
    return _normalize_text(value).upper() or "UNKNOWN"


def _normalize_confidence(value: Any, default: str = "medium") -> str:
    normalized = _normalize_text(value).lower()
    return normalized if normalized in {"high", "medium", "low"} else default


def _coerce_recent_messages(recent_messages: list[Any] | None) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for item in list(recent_messages or []):
        if isinstance(item, dict):
            content = item.get("content")
            role = item.get("role")
            message_id = item.get("message_id") or item.get("id")
        else:
            content = getattr(item, "content", None)
            role = getattr(item, "role", None)
            message_id = getattr(item, "message_id", None) or getattr(item, "id", None)
        serialized.append(
            {
                "message_id": message_id,
                "role": _normalize_text(role) or "user",
                "content": _normalize_text(content),
            }
        )
    return serialized


def _is_third_party_retelling(message_text: str) -> bool:
    return any(marker in message_text for marker in _THIRD_PARTY_MARKERS)


def _has_explicit_party_name(message_text: str) -> bool:
    return any(pattern.search(message_text) for pattern in _EXPLICIT_PARTY_NAME_PATTERNS)


def _has_self_anchor(message_text: str) -> bool:
    return any(anchor in message_text for anchor in _SELF_ANCHORS)


def _party_naming_decision(
    *,
    should_prompt: bool,
    is_case_fact: bool,
    has_self_anchor: bool,
    self_anchor_labels: list[str],
    is_third_party_retelling: bool,
    has_explicit_party_name: bool,
) -> dict[str, Any]:
    requires_blank_confirmation = (
        should_prompt
        and is_case_fact
        and has_self_anchor
        and not is_third_party_retelling
        and not has_explicit_party_name
    )
    if requires_blank_confirmation:
        reason = "self_anchor_without_explicit_name"
    elif is_third_party_retelling:
        reason = "third_party_retelling_not_user_self_anchor"
    elif has_explicit_party_name:
        reason = "explicit_party_name_available"
    elif not has_self_anchor:
        reason = "no_self_anchor"
    else:
        reason = "not_applicable"
    return {
        "requires_blank_confirmation": requires_blank_confirmation,
        "reason": reason,
        "self_anchor_labels": self_anchor_labels,
    }


def _base_decision(
    *,
    message_text: str,
    conversation_mode: str,
    persistence_consent: str,
    is_third_party_retelling: bool,
    has_explicit_party_name: bool,
    has_self_anchor: bool,
    self_anchor_labels: list[str],
) -> dict[str, Any]:
    return {
        "should_prompt": False,
        "prompt_reason": "",
        "prompt_reason_text": "",
        "suggested_mode": "none",
        "is_case_fact": False,
        "is_legal_question": False,
        "is_subject_reference": False,
        "is_third_party_retelling": is_third_party_retelling,
        "has_explicit_party_name": has_explicit_party_name,
        "has_self_anchor": has_self_anchor,
        "self_anchor_labels": self_anchor_labels,
        "confidence": "medium",
        "source": "agentic_entry_decision",
        "fallback_used": False,
        "detected_mode": "UNKNOWN",
        "intent": "unknown",
        "route_hint": "UNKNOWN",
        "conversation_mode": conversation_mode,
        "persistence_consent": persistence_consent,
        "suppressed_by": None,
        "message_summary": _normalize_text(message_text)[:160],
        "legacy_mode_result": None,
        "message_understanding": None,
        "trace": {
            "steps": ["build_case_tracking_entry_context"],
            "warnings": [],
        },
        "party_naming_decision": _party_naming_decision(
            should_prompt=False,
            is_case_fact=False,
            has_self_anchor=has_self_anchor,
            self_anchor_labels=self_anchor_labels,
            is_third_party_retelling=is_third_party_retelling,
            has_explicit_party_name=has_explicit_party_name,
        ),
    }


def _apply_party_naming_decision(decision: dict[str, Any]) -> dict[str, Any]:
    decision["party_naming_decision"] = _party_naming_decision(
        should_prompt=bool(decision.get("should_prompt")),
        is_case_fact=bool(decision.get("is_case_fact")),
        has_self_anchor=bool(decision.get("has_self_anchor")),
        self_anchor_labels=list(decision.get("self_anchor_labels") or []),
        is_third_party_retelling=bool(decision.get("is_third_party_retelling")),
        has_explicit_party_name=bool(decision.get("has_explicit_party_name")),
    )
    return decision


def _no_prompt(
    decision: dict[str, Any],
    *,
    reason: str,
    detected_mode: str = "UNKNOWN",
    intent: str | None = None,
    route_hint: str | None = None,
    confidence: str | None = None,
    suppressed_by: str | None = None,
    is_case_fact: bool | None = None,
    is_legal_question: bool | None = None,
    is_subject_reference: bool | None = None,
) -> dict[str, Any]:
    decision.update(
        should_prompt=False,
        prompt_reason=reason,
        suggested_mode="none",
        detected_mode=detected_mode,
        suppressed_by=suppressed_by,
    )
    if intent is not None:
        decision["intent"] = intent
    if route_hint is not None:
        decision["route_hint"] = route_hint
    if confidence is not None:
        decision["confidence"] = confidence
    if is_case_fact is not None:
        decision["is_case_fact"] = is_case_fact
    if is_legal_question is not None:
        decision["is_legal_question"] = is_legal_question
    if is_subject_reference is not None:
        decision["is_subject_reference"] = is_subject_reference
    decision["trace"]["steps"].append(f"no_prompt:{reason}")
    return _apply_party_naming_decision(decision)


def _prompt_for_case_tracking(
    decision: dict[str, Any],
    *,
    reason: str,
    source: str,
    fallback_used: bool,
    intent: str,
    route_hint: str,
    confidence: str,
) -> dict[str, Any]:
    decision.update(
        should_prompt=True,
        prompt_reason=reason,
        prompt_reason_text="识别到具体案情事实，需要确认是否作为案卷持续跟踪。",
        suggested_mode="tracked_case",
        is_case_fact=True,
        is_legal_question=False,
        is_subject_reference=False,
        source=source,
        fallback_used=fallback_used,
        detected_mode="CASE_ANALYSIS",
        intent=intent,
        route_hint=route_hint,
        confidence=confidence,
        suppressed_by=None,
    )
    decision["trace"]["steps"].append(f"prompt:{reason}")
    return _apply_party_naming_decision(decision)


def _apply_legacy_fallback(
    decision: dict[str, Any],
    *,
    message_text: str,
    current_mode: str,
    case_summary: str | None,
    fallback_reason: str,
) -> dict[str, Any]:
    legacy_result = detect_conversation_mode(
        message_text,
        current_mode=current_mode,
        case_summary=case_summary,
    )
    detected_mode = str(legacy_result.get("detected_mode") or "UNKNOWN")
    requires_consent = bool(legacy_result.get("requires_persistence_consent"))
    legacy_reason = _normalize_text(legacy_result.get("reason")) or fallback_reason
    decision.update(
        source="legacy_fallback",
        fallback_used=True,
        legacy_mode_result=legacy_result,
        detected_mode=detected_mode,
        confidence="low",
    )
    decision["trace"]["steps"].append("legacy_detect_conversation_mode")
    decision["trace"]["warnings"].append(fallback_reason)

    if detected_mode == "CASE_ANALYSIS" and requires_consent:
        return _prompt_for_case_tracking(
            decision,
            reason=legacy_reason,
            source="legacy_fallback",
            fallback_used=True,
            intent="fact_update",
            route_hint="CASE_UPDATE",
            confidence="low",
        )
    if detected_mode == "LEGAL_QA":
        return _no_prompt(
            decision,
            reason=legacy_reason,
            detected_mode="LEGAL_QA",
            intent="legal_question",
            route_hint="LEGAL_QA",
            confidence="low",
            is_legal_question=True,
        )
    return _no_prompt(
        decision,
        reason=legacy_reason,
        detected_mode=detected_mode,
        confidence="low",
    )


async def decide_case_tracking_entry(
    *,
    message_text: str,
    message_understanding: dict[str, Any] | None = None,
    conversation_mode: str | None = None,
    dossier_id: int | None = None,
    recent_messages: list[Any] | None = None,
    current_conversation_graph: Any | None = None,
    case_summary: str | None = None,
    persistence_consent: str | None = None,
    provider_hint: str | None = None,
    use_message_understanding: bool = True,
) -> dict[str, Any]:
    clean_message = _normalize_text(message_text)
    normalized_mode = normalize_conversation_mode(conversation_mode)
    normalized_consent = normalize_persistence_consent(persistence_consent)
    is_third_party_retelling = _is_third_party_retelling(clean_message)
    has_explicit_party_name = _has_explicit_party_name(clean_message)
    has_self_anchor = _has_self_anchor(clean_message)
    self_anchor_labels = [anchor for anchor in _SELF_ANCHORS if anchor in clean_message]
    decision = _base_decision(
        message_text=clean_message,
        conversation_mode=normalized_mode,
        persistence_consent=normalized_consent,
        is_third_party_retelling=is_third_party_retelling,
        has_explicit_party_name=has_explicit_party_name,
        has_self_anchor=has_self_anchor,
        self_anchor_labels=self_anchor_labels,
    )
    decision["dossier_id"] = dossier_id
    decision["has_current_conversation_graph"] = bool(current_conversation_graph)

    if not clean_message:
        return _no_prompt(decision, reason="empty_input")

    has_bound_dossier = isinstance(dossier_id, int) and dossier_id > 0
    if (normalized_mode == "TRACKED_CASE" or normalized_consent == "tracked") and has_bound_dossier:
        return _no_prompt(
            decision,
            reason="tracked_conversation_already_selected",
            detected_mode="TRACKED_CASE",
            suppressed_by="tracked_conversation",
        )

    if normalized_consent == "temporary":
        return _no_prompt(
            decision,
            reason="temporary_analysis_already_selected",
            detected_mode="CASE_ANALYSIS",
            suppressed_by="temporary_analysis",
        )

    understanding = message_understanding
    if understanding is None and use_message_understanding:
        try:
            understanding = await understand_message_with_agent(
                message=clean_message,
                recent_messages=_coerce_recent_messages(recent_messages),
                conversation_mode=normalized_mode,
                dossier_id=dossier_id,
                dossier_context=None,
                case_summary=case_summary,
                provider_hint=provider_hint,
            )
            decision["trace"]["steps"].append("message_understanding")
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.warning("Case tracking entry message understanding failed: %s", exc)
            decision["trace"]["warnings"].append("message_understanding_failed")
            understanding = None

    if isinstance(understanding, dict):
        intent = _normalize_intent(understanding.get("intent"))
        route_hint = _normalize_route(understanding.get("route_hint"))
        confidence = _normalize_confidence(understanding.get("confidence"), default="medium")
        decision["message_understanding"] = {
            "intent": intent,
            "route_hint": route_hint,
            "confidence": confidence,
            "source": understanding.get("source"),
            "fallback_reason": understanding.get("fallback_reason"),
        }

        if intent in _LEGAL_INTENTS or route_hint == "LEGAL_QA":
            return _no_prompt(
                decision,
                reason="legal_or_advice_question",
                detected_mode="LEGAL_QA",
                intent=intent,
                route_hint=route_hint,
                confidence=confidence,
                is_legal_question=True,
            )

        if intent in _SUPPRESSED_INTENTS:
            return _no_prompt(
                decision,
                reason=f"{intent}_does_not_start_case_tracking",
                detected_mode="UNKNOWN",
                intent=intent,
                route_hint=route_hint,
                confidence=confidence,
                is_subject_reference=intent == "subject_reference",
            )

        if intent in _CASE_FACT_INTENTS or route_hint == "CASE_UPDATE":
            return _prompt_for_case_tracking(
                decision,
                reason="concrete_case_fact_detected",
                source="agentic_entry_decision",
                fallback_used=False,
                intent=intent,
                route_hint=route_hint,
                confidence=confidence,
            )

        fallback_reason = (
            _normalize_text(understanding.get("fallback_reason"))
            or "message_understanding_low_signal"
        )
    else:
        fallback_reason = "message_understanding_unavailable"

    return _apply_legacy_fallback(
        decision,
        message_text=clean_message,
        current_mode=normalized_mode,
        case_summary=case_summary,
        fallback_reason=fallback_reason,
    )
