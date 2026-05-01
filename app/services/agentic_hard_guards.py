from __future__ import annotations

from typing import Any

from app.services.agentic_request_context import AgenticRequestContext


SUPPORTED_WRITE_TYPES = {
    "structured_case_persistence",
    "graph_update_schedule",
}


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _normalize_key(value: Any) -> str:
    return _normalize_text(value).lower()


def _normalize_route(value: Any) -> str:
    return _normalize_text(value).upper()


def _as_request_context(
    request_context: AgenticRequestContext | dict[str, Any] | None,
) -> dict[str, Any]:
    if isinstance(request_context, AgenticRequestContext):
        return request_context.as_dict()
    if isinstance(request_context, dict):
        return dict(request_context)
    return {}


def _copy_write_request(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": _normalize_text(item.get("name")) or "unknown_write",
        "tool_name": _normalize_text(item.get("tool_name")) or None,
        "requested": bool(item.get("requested", True)),
        "input_text": _normalize_text(item.get("input_text")),
        "reason": _normalize_text(item.get("reason")) or None,
    }


def _synthetic_missing_previous_fact_requests() -> list[dict[str, Any]]:
    return [
        {
            "name": "structured_case_persistence",
            "tool_name": "case_summary_update",
            "requested": False,
            "input_text": "",
            "reason": "Blocked because no verified previous factual user message is available.",
        },
        {
            "name": "graph_update_schedule",
            "tool_name": "schedule_graph_update",
            "requested": False,
            "input_text": "",
            "reason": "Blocked because no verified previous factual user message is available.",
        },
    ]


def evaluate_agentic_hard_guards(
    *,
    agentic_plan: dict[str, Any] | None,
    request_context: AgenticRequestContext | dict[str, Any] | None = None,
    message_understanding: dict[str, Any] | None = None,
    persistence_consent: str | None = None,
    conversation_mode: str | None = None,
    previous_factual_user_message: dict[str, Any] | None = None,
    missing_previous_fact: bool | None = None,
) -> dict[str, Any]:
    plan = agentic_plan if isinstance(agentic_plan, dict) else {}
    mu = message_understanding if isinstance(message_understanding, dict) else {}
    context = _as_request_context(request_context)

    requested_writes = [
        _copy_write_request(item)
        for item in (
            plan.get("writes_requested")
            if isinstance(plan.get("writes_requested"), list)
            else []
        )
        if isinstance(item, dict)
    ]

    intent = _normalize_key(plan.get("intent") or mu.get("intent"))
    route = _normalize_route(plan.get("route") or mu.get("route_hint"))

    normalized_persistence = _normalize_key(
        persistence_consent
        if persistence_consent is not None
        else context.get("persistence_consent")
    )
    normalized_mode = _normalize_route(
        conversation_mode
        if conversation_mode is not None
        else context.get("conversation_mode")
    )
    dossier_id = context.get("dossier_id")
    has_bound_dossier = isinstance(dossier_id, int) and dossier_id > 0
    resolved_previous_fact = (
        previous_factual_user_message
        if isinstance(previous_factual_user_message, dict)
        else (
            context.get("previous_factual_user_message")
            if isinstance(context.get("previous_factual_user_message"), dict)
            else None
        )
    )
    current_message_text = _normalize_text(context.get("message_text"))
    clarification_response = (
        mu.get("clarification_response")
        if isinstance(mu.get("clarification_response"), dict)
        else {}
    )
    clarification_applies_pending_graph_fact = bool(
        intent == "clarification_response"
        and _normalize_text(clarification_response.get("pending_graph_update_input_text"))
    )

    temporary_blocks_structured_persistence = normalized_persistence in {
        "temporary",
        "false",
        "disabled",
    }
    temporary_blocks_graph_update = temporary_blocks_structured_persistence
    tracked_writes_require_bound_dossier = (
        route == "CASE_UPDATE"
        and not has_bound_dossier
    )
    legal_qa_blocks_case_graph_pollution = (
        normalized_mode == "LEGAL_QA"
        or intent in {"legal_question", "advice_question", "document_request"}
        or route == "LEGAL_QA"
    )
    subject_reference_blocks_writes = intent in {
        "subject_reference",
        "clarification_response",
    } and not clarification_applies_pending_graph_fact
    missing_previous_fact_blocks_confirm_writes = bool(
        intent == "confirm_previous_fact"
        and (
            bool(missing_previous_fact)
            or not isinstance(resolved_previous_fact, dict)
            or not _normalize_text(resolved_previous_fact.get("content"))
        )
    )

    if missing_previous_fact_blocks_confirm_writes and not requested_writes:
        requested_writes = _synthetic_missing_previous_fact_requests()

    writes_requested: list[dict[str, Any]] = []
    writes_allowed: list[dict[str, Any]] = []
    writes_blocked: list[dict[str, Any]] = []

    unsupported_write_type_blocks_execution = False
    message_bound_graph_update_only = False

    for request in requested_writes:
        blocked_by: list[str] = []
        write_name = _normalize_text(request.get("name")) or "unknown_write"
        input_text = _normalize_text(request.get("input_text"))

        if write_name not in SUPPORTED_WRITE_TYPES:
            blocked_by.append("unsupported_write_type_blocks_execution")
            unsupported_write_type_blocks_execution = True

        if subject_reference_blocks_writes:
            blocked_by.append("subject_reference_blocks_writes")

        if write_name == "structured_case_persistence":
            if temporary_blocks_structured_persistence:
                blocked_by.append("temporary_blocks_structured_persistence")
            if tracked_writes_require_bound_dossier:
                blocked_by.append("tracked_writes_require_bound_dossier")
            if legal_qa_blocks_case_graph_pollution:
                blocked_by.append("legal_qa_blocks_case_graph_pollution")
            if missing_previous_fact_blocks_confirm_writes:
                blocked_by.append("missing_previous_fact_blocks_confirm_writes")

        if write_name == "graph_update_schedule":
            if temporary_blocks_graph_update:
                blocked_by.append("temporary_blocks_graph_update")
            if tracked_writes_require_bound_dossier:
                blocked_by.append("tracked_writes_require_bound_dossier")
            if legal_qa_blocks_case_graph_pollution:
                blocked_by.append("legal_qa_blocks_case_graph_pollution")
            if missing_previous_fact_blocks_confirm_writes:
                blocked_by.append("missing_previous_fact_blocks_confirm_writes")
            if not input_text:
                blocked_by.append("message_bound_graph_update_only")
                message_bound_graph_update_only = True
            elif intent == "confirm_previous_fact" and input_text == current_message_text:
                blocked_by.append("message_bound_graph_update_only")
                message_bound_graph_update_only = True

        evaluated = {
            **request,
            "allowed": len(blocked_by) == 0,
            "blocked_by": blocked_by,
        }
        writes_requested.append(evaluated)
        if evaluated["allowed"]:
            writes_allowed.append(evaluated)
        else:
            writes_blocked.append(evaluated)

    guards_applied = [
        {
            "name": "temporary_blocks_structured_persistence",
            "triggered": temporary_blocks_structured_persistence,
        },
        {
            "name": "temporary_blocks_graph_update",
            "triggered": temporary_blocks_graph_update,
        },
        {
            "name": "tracked_writes_require_bound_dossier",
            "triggered": tracked_writes_require_bound_dossier,
        },
        {
            "name": "legal_qa_blocks_case_graph_pollution",
            "triggered": legal_qa_blocks_case_graph_pollution,
        },
        {
            "name": "subject_reference_blocks_writes",
            "triggered": subject_reference_blocks_writes,
        },
        {
            "name": "clarification_response_resumes_pending_graph_update",
            "triggered": clarification_applies_pending_graph_fact,
        },
        {
            "name": "missing_previous_fact_blocks_confirm_writes",
            "triggered": missing_previous_fact_blocks_confirm_writes,
        },
        {
            "name": "unsupported_write_type_blocks_execution",
            "triggered": unsupported_write_type_blocks_execution,
        },
        {
            "name": "message_bound_graph_update_only",
            "triggered": message_bound_graph_update_only,
        },
        {
            "name": "temporary_blocks_persistence",
            "triggered": temporary_blocks_structured_persistence or temporary_blocks_graph_update,
        },
        {
            "name": "legal_qa_blocks_graph",
            "triggered": legal_qa_blocks_case_graph_pollution,
        },
        {
            "name": "missing_previous_fact",
            "triggered": missing_previous_fact_blocks_confirm_writes,
        },
    ]

    return {
        "guards_applied": guards_applied,
        "writes_requested": writes_requested,
        "writes_allowed": writes_allowed,
        "writes_blocked": writes_blocked,
    }


__all__ = ["evaluate_agentic_hard_guards"]
