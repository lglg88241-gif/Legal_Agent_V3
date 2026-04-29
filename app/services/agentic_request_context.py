from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.services.conversation_mode import (
    normalize_conversation_mode,
    normalize_persistence_consent,
)
from app.services.memory_resolver import extract_pending_subject_reference_clarification


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_message(message: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return None

    normalized_content = _normalize_text(message.get("content"))
    if not normalized_content:
        return None

    return {
        "message_id": _coerce_int(message.get("message_id") or message.get("id")),
        "role": _normalize_text(message.get("role")).lower() or "user",
        "content": normalized_content,
        "metadata": (
            message.get("metadata")
            if isinstance(message.get("metadata"), dict)
            else {}
        ),
    }


def _message_understanding_intent(metadata: dict[str, Any] | None) -> str:
    if not isinstance(metadata, dict):
        return ""
    understanding = (
        metadata.get("message_understanding")
        if isinstance(metadata.get("message_understanding"), dict)
        else {}
    )
    return _normalize_text(understanding.get("intent")).lower()


def _execution_plan_route(metadata: dict[str, Any] | None) -> str:
    if not isinstance(metadata, dict):
        return ""
    execution_plan = (
        metadata.get("execution_plan")
        if isinstance(metadata.get("execution_plan"), dict)
        else {}
    )
    return _normalize_text(execution_plan.get("primary_route")).upper()


def _execution_plan_steps(metadata: dict[str, Any] | None) -> set[str]:
    if not isinstance(metadata, dict):
        return set()
    execution_plan = (
        metadata.get("execution_plan")
        if isinstance(metadata.get("execution_plan"), dict)
        else {}
    )
    steps = execution_plan.get("steps") if isinstance(execution_plan.get("steps"), list) else []
    return {
        _normalize_text(step).lower()
        for step in steps
        if _normalize_text(step)
    }


def _message_is_factual_user_message(
    message: dict[str, Any] | None,
    *,
    next_message: dict[str, Any] | None = None,
    current_message_text: str | None = None,
) -> bool:
    normalized_message = _normalize_message(message)
    if not isinstance(normalized_message, dict):
        return False
    if normalized_message.get("role") != "user":
        return False

    normalized_current_message = _normalize_text(current_message_text)
    if normalized_current_message and normalized_message.get("content") == normalized_current_message:
        return False

    candidate_metadata = [
        normalized_message.get("metadata") if isinstance(normalized_message.get("metadata"), dict) else {},
        (
            next_message.get("metadata")
            if isinstance(next_message, dict) and isinstance(next_message.get("metadata"), dict)
            else {}
        ),
    ]

    for metadata in candidate_metadata:
        intent = _message_understanding_intent(metadata)
        if intent == "fact_update":
            return True
        if intent in {"confirm_previous_fact", "legal_question", "advice_question", "subject_reference"}:
            continue

        route = _execution_plan_route(metadata)
        steps = _execution_plan_steps(metadata)
        if route == "CASE_UPDATE" and {"fact_persistence", "graph_update"} & steps:
            return True

    return False


def _target_message_id_from_message_understanding(
    message_understanding: dict[str, Any] | None,
) -> int | None:
    if not isinstance(message_understanding, dict):
        return None
    previous_fact_confirmation = (
        message_understanding.get("previous_fact_confirmation")
        if isinstance(message_understanding.get("previous_fact_confirmation"), dict)
        else {}
    )
    return _coerce_int(previous_fact_confirmation.get("target_message_id"))


def resolve_previous_factual_user_message(
    *,
    message_text: str,
    recent_messages: list[dict[str, Any]] | None,
    message_understanding: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    normalized_recent_messages = [
        normalized
        for normalized in (
            _normalize_message(item)
            for item in list(recent_messages or [])
        )
        if isinstance(normalized, dict)
    ]

    target_message_id = _target_message_id_from_message_understanding(message_understanding)
    if target_message_id is not None:
        for index, candidate in enumerate(normalized_recent_messages):
            candidate_message_id = _coerce_int(candidate.get("message_id"))
            if candidate_message_id != target_message_id:
                continue
            if candidate.get("role") != "user":
                continue
            if _normalize_text(candidate.get("content")) == _normalize_text(message_text):
                continue
            if _normalize_text(candidate.get("content")):
                return candidate
        return None

    for index in range(len(normalized_recent_messages) - 1, -1, -1):
        candidate = normalized_recent_messages[index]
        next_message = (
            normalized_recent_messages[index + 1]
            if index + 1 < len(normalized_recent_messages)
            else None
        )
        if _message_is_factual_user_message(
            candidate,
            next_message=next_message,
            current_message_text=message_text,
        ):
            return candidate

    return None


def build_request_context_summary(
    *,
    message_text: str,
    recent_messages: list[dict[str, Any]],
    conversation_mode: str,
    persistence_consent: str,
    dossier_id: int | None,
    case_summary: str | None,
    current_conversation_graph: dict[str, Any] | None,
    dossier_context: dict[str, Any] | None,
    dossier_graph_context: dict[str, Any] | None,
    pending_clarification: dict[str, Any] | None,
    previous_factual_user_message: dict[str, Any] | None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "message_text": _normalize_text(message_text),
        "recent_message_count": len(recent_messages),
        "conversation_mode": conversation_mode,
        "persistence_consent": persistence_consent,
        "has_case_summary": bool(_normalize_text(case_summary)),
        "has_dossier": bool(dossier_id),
        "has_current_conversation_graph": isinstance(current_conversation_graph, dict),
        "has_dossier_context": isinstance(dossier_context, dict),
        "has_dossier_graph_context": isinstance(dossier_graph_context, dict),
        "has_pending_clarification": isinstance(pending_clarification, dict),
        "has_previous_factual_user_message": isinstance(previous_factual_user_message, dict),
    }
    if isinstance(pending_clarification, dict):
        summary["pending_clarification"] = {
            "target_mention": _normalize_text(pending_clarification.get("target_mention")),
            "reference_type": _normalize_text(pending_clarification.get("reference_type")).lower() or "unknown",
            "expected_response": _normalize_text(pending_clarification.get("expected_response")),
        }
    if isinstance(previous_factual_user_message, dict):
        summary["previous_factual_user_message"] = {
            "message_id": _coerce_int(previous_factual_user_message.get("message_id")),
            "content_preview": _normalize_text(previous_factual_user_message.get("content"))[:120],
        }
    return summary


@dataclass(frozen=True)
class AgenticRequestContext:
    request_id: str
    user_id: int | None
    conversation_id: int
    dossier_id: int | None
    message_text: str
    recent_messages: list[dict[str, Any]]
    chat_history: str
    conversation_mode: str
    persistence_consent: str
    case_summary: str
    saved_case_state: dict[str, Any] | None
    current_conversation_graph: dict[str, Any] | None
    dossier_context: dict[str, Any] | None
    dossier_graph_context: dict[str, Any] | None
    pending_clarification: dict[str, Any] | None
    previous_factual_user_message: dict[str, Any] | None
    ownership_context: dict[str, Any]
    temporary: bool
    tracked: bool
    context_summary: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_agentic_request_context(
    *,
    request_id: str,
    user_id: int | None,
    conversation_id: int,
    message_text: str,
    chat_history: str | None = None,
    recent_messages: list[dict[str, Any]] | None = None,
    conversation_mode: str | None = None,
    persistence_consent: str | None = None,
    case_summary: str | None = None,
    saved_case_state: dict[str, Any] | None = None,
    current_conversation_graph: dict[str, Any] | None = None,
    dossier_id: int | None = None,
    dossier_context: dict[str, Any] | None = None,
    dossier_graph_context: dict[str, Any] | None = None,
    ownership_context: dict[str, Any] | None = None,
    message_understanding: dict[str, Any] | None = None,
) -> AgenticRequestContext:
    normalized_message_text = _normalize_text(message_text)
    normalized_recent_messages = [
        normalized
        for normalized in (
            _normalize_message(item)
            for item in list(recent_messages or [])
        )
        if isinstance(normalized, dict)
    ]
    normalized_conversation_mode = normalize_conversation_mode(conversation_mode)
    normalized_persistence_consent = normalize_persistence_consent(persistence_consent)
    resolved_dossier_id = _coerce_int(dossier_id)
    if resolved_dossier_id is None and isinstance(dossier_context, dict):
        resolved_dossier_id = _coerce_int(dossier_context.get("id"))

    pending_clarification = extract_pending_subject_reference_clarification(
        normalized_recent_messages
    )
    previous_factual_user_message = resolve_previous_factual_user_message(
        message_text=normalized_message_text,
        recent_messages=normalized_recent_messages,
        message_understanding=message_understanding,
    )

    normalized_ownership_context = (
        dict(ownership_context)
        if isinstance(ownership_context, dict)
        else {
            "user_id": user_id,
            "conversation_id": conversation_id,
            "dossier_id": resolved_dossier_id,
            "owner_check_pending": False,
        }
    )

    context_summary = build_request_context_summary(
        message_text=normalized_message_text,
        recent_messages=normalized_recent_messages,
        conversation_mode=normalized_conversation_mode,
        persistence_consent=normalized_persistence_consent,
        dossier_id=resolved_dossier_id,
        case_summary=case_summary,
        current_conversation_graph=current_conversation_graph,
        dossier_context=dossier_context if isinstance(dossier_context, dict) else None,
        dossier_graph_context=(
            dossier_graph_context if isinstance(dossier_graph_context, dict) else None
        ),
        pending_clarification=pending_clarification,
        previous_factual_user_message=previous_factual_user_message,
    )

    return AgenticRequestContext(
        request_id=_normalize_text(request_id) or f"context:{conversation_id}",
        user_id=_coerce_int(user_id),
        conversation_id=conversation_id,
        dossier_id=resolved_dossier_id,
        message_text=normalized_message_text,
        recent_messages=normalized_recent_messages,
        chat_history=_normalize_text(chat_history),
        conversation_mode=normalized_conversation_mode,
        persistence_consent=normalized_persistence_consent,
        case_summary=_normalize_text(case_summary),
        saved_case_state=saved_case_state if isinstance(saved_case_state, dict) else None,
        current_conversation_graph=(
            current_conversation_graph if isinstance(current_conversation_graph, dict) else None
        ),
        dossier_context=dossier_context if isinstance(dossier_context, dict) else None,
        dossier_graph_context=(
            dossier_graph_context if isinstance(dossier_graph_context, dict) else None
        ),
        pending_clarification=pending_clarification,
        previous_factual_user_message=previous_factual_user_message,
        ownership_context=normalized_ownership_context,
        temporary=normalized_persistence_consent == "temporary",
        tracked=normalized_persistence_consent == "tracked",
        context_summary=context_summary,
    )


__all__ = [
    "AgenticRequestContext",
    "build_agentic_request_context",
    "build_request_context_summary",
    "resolve_previous_factual_user_message",
]
