from __future__ import annotations

from typing import Any


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _build_step(
    *,
    name: str,
    agent: str,
    action: str,
    input_source: str,
    required: bool = True,
) -> dict[str, Any]:
    return {
        "name": name,
        "agent": agent,
        "action": action,
        "input_source": input_source,
        "required": required,
    }


def _resolve_previous_fact_text(
    *,
    original_message: str,
    message_understanding: dict[str, Any],
    recent_messages: list[dict[str, Any]] | None,
) -> str:
    previous_fact_confirmation = (
        message_understanding.get("previous_fact_confirmation")
        if isinstance(message_understanding.get("previous_fact_confirmation"), dict)
        else {}
    )
    normalized_current_message = _normalize_text(original_message)
    target_text = _normalize_text(previous_fact_confirmation.get("target_text"))
    if target_text and target_text != normalized_current_message:
        return target_text

    normalized_target_message_id = _coerce_int(previous_fact_confirmation.get("target_message_id"))
    candidates = list(recent_messages or [])
    if normalized_target_message_id is not None:
        for item in reversed(candidates):
            if not isinstance(item, dict):
                continue
            if str(item.get("role") or "").strip().lower() != "user":
                continue
            candidate_text = _normalize_text(item.get("content"))
            if not candidate_text or candidate_text == normalized_current_message:
                continue
            candidate_message_id = _coerce_int(item.get("message_id") or item.get("id"))
            if candidate_message_id == normalized_target_message_id:
                return candidate_text

    for item in reversed(candidates):
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "").strip().lower() != "user":
            continue
        candidate_text = _normalize_text(item.get("content"))
        if not candidate_text or candidate_text == normalized_current_message:
            continue
        return candidate_text

    return ""


def build_execution_plan_from_message_understanding(
    message_understanding: dict[str, Any] | None,
    *,
    original_message: str,
    recent_messages: list[dict[str, Any]] | None,
    dossier_id: int | None,
    persistence_consent: str | None,
) -> dict[str, Any]:
    mu_result = message_understanding if isinstance(message_understanding, dict) else {}
    trace = mu_result.get("trace") if isinstance(mu_result.get("trace"), dict) else {}
    warnings = trace.get("warnings") if isinstance(trace.get("warnings"), list) else []
    normalized_message = _normalize_text(original_message)
    intent = str(mu_result.get("intent") or "unknown").strip().lower() or "unknown"
    primary_route = str(mu_result.get("route_hint") or "UNKNOWN").strip().upper() or "UNKNOWN"
    provider = str(mu_result.get("provider") or "").strip() or "fallback"
    model = str(mu_result.get("model") or "").strip()
    fallback_reason = str(mu_result.get("fallback_reason") or "").strip() or None

    plan: dict[str, Any] = {
        "source": "message_understanding",
        "intent": intent,
        "primary_route": primary_route,
        "response_mode": "legal_answer",
        "persistence_allowed": False,
        "graph_update_allowed": False,
        "steps": [],
        "guards": {
            "temporary_blocks_persistence": str(persistence_consent or "").strip().lower() == "temporary",
            "legal_qa_blocks_graph_update": True,
            "subject_reference_blocks_graph_update": intent == "subject_reference",
            "requires_previous_fact": intent == "confirm_previous_fact",
        },
        "trace": {
            "reason": str(mu_result.get("reason") or "").strip(),
            "provider": provider,
            "model": model,
            "fallback_reason": fallback_reason,
            "warnings": list(warnings),
            "use_legacy_fallback": False,
        },
        "context": {
            "dossier_id": dossier_id,
            "has_dossier": bool(dossier_id),
        },
        "resolved_inputs": {
            "current_message_text": normalized_message,
            "effective_persistence_text": normalized_message,
            "graph_update_input_text": "",
            "previous_fact_text": "",
            "subject_reference_scope_order": [],
        },
        "is_actionable": False,
    }

    if provider in {"fallback", "rules"} or fallback_reason:
        plan["trace"]["use_legacy_fallback"] = True
        if fallback_reason:
            plan["trace"]["warnings"].append(f"message_understanding_fallback:{fallback_reason}")
        return plan

    if (
        intent in {"subject_reference", "clarification_response"}
        and primary_route == "DOSSIER_MEMORY"
        and bool(mu_result.get("needs_legal_rag")) is False
    ):
        subject_reference = {}
        if intent == "subject_reference":
            subject_reference = (
                mu_result.get("subject_reference")
                if isinstance(mu_result.get("subject_reference"), dict)
                else {}
            )
        else:
            clarification_response = (
                mu_result.get("clarification_response")
                if isinstance(mu_result.get("clarification_response"), dict)
                else {}
            )
            subject_reference = {
                "scope_order": clarification_response.get("scope_attempted", []),
            }
        plan["steps"] = [
            _build_step(
                name="dossier_memory_answer",
                agent="dossier_memory",
                action="answer_subject_reference",
                input_source="current_message",
            )
        ]
        plan["response_mode"] = "dossier_memory_answer"
        plan["resolved_inputs"]["subject_reference_scope_order"] = list(
            subject_reference.get("scope_order")
            if isinstance(subject_reference.get("scope_order"), list)
            else []
        )
        plan["is_actionable"] = True
        return plan

    if intent == "fact_update" and primary_route == "CASE_UPDATE":
        plan["resolved_inputs"]["graph_update_input_text"] = normalized_message
        plan["is_actionable"] = True
        if str(persistence_consent or "").strip().lower() == "temporary":
            plan["response_mode"] = "case_analysis_without_persistence"
            plan["steps"] = [
                _build_step(
                    name="legal_agent_answer",
                    agent="legal_agent",
                    action="answer_case_analysis_without_persistence",
                    input_source="current_message",
                )
            ]
            plan["trace"]["reason"] = (
                plan["trace"]["reason"]
                or "用户选择仅本次分析，应基于事实给出法律分析，但不得持久化或更新图谱。"
            )
            return plan

        if (
            bool(mu_result.get("needs_structured_persistence")) is True
            and bool(mu_result.get("needs_graph_update")) is True
        ):
            plan["steps"] = [
                _build_step(
                    name="fact_persistence",
                    agent="fact_persistence",
                    action="update_case_summary",
                    input_source="current_message",
                ),
                _build_step(
                    name="graph_update",
                    agent="graph_extractor",
                    action="update_graph",
                    input_source="current_message",
                ),
                _build_step(
                    name="assistant_acknowledgement",
                    agent="assistant_response",
                    action="acknowledge_fact_update",
                    input_source="current_message",
                    required=False,
                ),
            ]
            plan["response_mode"] = "acknowledge_and_persist"
            plan["persistence_allowed"] = True
            plan["graph_update_allowed"] = True
            return plan

    if intent == "confirm_previous_fact" and primary_route == "CASE_UPDATE":
        previous_fact_text = _resolve_previous_fact_text(
            original_message=normalized_message,
            message_understanding=mu_result,
            recent_messages=recent_messages,
        )
        plan["steps"] = [
            _build_step(
                name="fact_confirmation",
                agent="fact_confirmation",
                action="resolve_previous_fact",
                input_source="recent_messages",
            )
        ]
        plan["resolved_inputs"]["previous_fact_text"] = previous_fact_text
        if previous_fact_text:
            plan["steps"].extend(
                [
                    _build_step(
                        name="fact_persistence",
                        agent="fact_persistence",
                        action="update_case_summary",
                        input_source="resolved_previous_fact",
                    ),
                    _build_step(
                        name="graph_update",
                        agent="graph_extractor",
                        action="update_graph",
                        input_source="resolved_previous_fact",
                    ),
                    _build_step(
                        name="assistant_acknowledgement",
                        agent="assistant_response",
                        action="acknowledge_confirmed_fact",
                        input_source="resolved_previous_fact",
                        required=False,
                    ),
                ]
            )
            plan["resolved_inputs"]["effective_persistence_text"] = previous_fact_text
            plan["resolved_inputs"]["graph_update_input_text"] = previous_fact_text
            plan["response_mode"] = "acknowledge_and_persist"
            plan["persistence_allowed"] = str(persistence_consent or "").strip().lower() != "temporary"
            plan["graph_update_allowed"] = str(persistence_consent or "").strip().lower() != "temporary"
            plan["is_actionable"] = True
        else:
            plan["trace"]["warnings"].append("confirmed_previous_fact_text_unavailable")
        return plan

    return plan
