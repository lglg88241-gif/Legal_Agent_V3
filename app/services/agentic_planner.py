from __future__ import annotations

import re
from typing import Any

from app.services.agentic_request_context import AgenticRequestContext
from app.services.tool_registry import get_tool_contract


_EXPLICIT_LAW_ARTICLE_PATTERNS = (
    re.compile(r"(劳动合同法|民法典|刑法|社会保险法)[^。！？\n]{0,24}第[一二三四五六七八九十百千万\d]+条"),
    re.compile(r"第[一二三四五六七八九十百千万\d]+条[^。！？\n]{0,12}(规定了什么|怎么规定|是什么意思|内容是什么)"),
)


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _looks_like_explicit_law_article_question(question: str) -> bool:
    normalized = _normalize_text(question)
    if not normalized:
        return False
    return any(pattern.search(normalized) for pattern in _EXPLICIT_LAW_ARTICLE_PATTERNS)


def _tool_call(
    tool_name: str,
    *,
    arguments: dict[str, Any],
    reason: str,
    dispatch_mode: str = "planner_direct",
) -> dict[str, Any]:
    return {
        "tool_name": tool_name,
        "arguments": arguments,
        "reason": _normalize_text(reason),
        "dispatch_mode": dispatch_mode,
        "contract": get_tool_contract(tool_name),
    }


def _write_request(
    name: str,
    *,
    tool_name: str | None = None,
    requested: bool,
    input_text: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "tool_name": _normalize_text(tool_name) or None,
        "requested": bool(requested),
        "input_text": _normalize_text(input_text),
        "reason": _normalize_text(reason),
    }


def _base_plan(
    *,
    intent: str,
    route: str,
    response_mode: str,
    specialist: str,
    needs_legal_rag: bool,
    needs_graph_context: bool,
    needs_structured_persistence: bool,
) -> dict[str, Any]:
    return {
        "planner_version": "h8-2a-v1",
        "intent": intent,
        "route": route,
        "response_mode": response_mode,
        "response_style": response_mode,
        "specialist": specialist,
        "plan_summary": "Legacy-compatible planner fallback.",
        "context_requirements": [],
        "tool_sequence": [],
        "tool_calls": [],
        "writes_requested": [],
        "needs_legal_rag": bool(needs_legal_rag),
        "needs_graph_context": bool(needs_graph_context),
        "needs_structured_persistence": bool(needs_structured_persistence),
        "final_response_strategy": "legacy_fallback",
        "missing_requirements": [],
        "fallback_allowed": True,
    }


def _resolved_previous_fact_text(
    *,
    request_context: AgenticRequestContext | None,
    execution_plan: dict[str, Any] | None,
) -> str:
    if isinstance(request_context, AgenticRequestContext):
        previous_message = request_context.previous_factual_user_message
        if isinstance(previous_message, dict):
            return _normalize_text(previous_message.get("content"))

    resolved_inputs = (
        execution_plan.get("resolved_inputs")
        if isinstance(execution_plan, dict) and isinstance(execution_plan.get("resolved_inputs"), dict)
        else {}
    )
    return _normalize_text(resolved_inputs.get("previous_fact_text"))


def build_agentic_plan(
    *,
    question: str,
    message_understanding: dict[str, Any] | None,
    execution_plan: dict[str, Any] | None,
    conversation_id: int,
    dossier_id: int | None,
    persistence_consent: str | None,
    conversation_mode: str | None,
    request_context: AgenticRequestContext | None = None,
) -> dict[str, Any]:
    del conversation_mode
    del persistence_consent

    mu = message_understanding if isinstance(message_understanding, dict) else {}
    legacy_plan = execution_plan if isinstance(execution_plan, dict) else {}
    intent = _normalize_text(mu.get("intent")).lower() or "unknown"
    route = _normalize_text(legacy_plan.get("primary_route") or mu.get("route_hint")).upper() or "UNKNOWN"
    normalized_question = _normalize_text(question)
    needs_legal_rag = bool(mu.get("needs_legal_rag"))
    needs_graph_context = bool(mu.get("needs_dossier_graph_context")) or intent in {
        "subject_reference",
        "clarification_response",
    }
    needs_structured_persistence = bool(mu.get("needs_structured_persistence"))

    plan = _base_plan(
        intent=intent,
        route=route,
        response_mode=_normalize_text(legacy_plan.get("response_mode")).lower() or "legacy_fallback",
        specialist="legacy_fallback",
        needs_legal_rag=needs_legal_rag,
        needs_graph_context=needs_graph_context,
        needs_structured_persistence=needs_structured_persistence,
    )

    if intent == "clarification_response" and route == "CASE_UPDATE":
        resolved_inputs = (
            legacy_plan.get("resolved_inputs")
            if isinstance(legacy_plan.get("resolved_inputs"), dict)
            else {}
        )
        graph_update_input_text = _normalize_text(
            resolved_inputs.get("graph_update_input_text")
            or resolved_inputs.get("effective_persistence_text")
        )
        tool_sequence = [
            _tool_call(
                "case_summary_update",
                arguments={"conversation_id": conversation_id, "case_summary": graph_update_input_text},
                reason="Persist the original fact together with the user's reference clarification.",
                dispatch_mode="planned_contract",
            ),
            _tool_call(
                "schedule_graph_update",
                arguments={"conversation_id": conversation_id, "user_id": 0},
                reason="Schedule graph update from the original fact plus the resolved reference.",
                dispatch_mode="planned_contract",
            ),
            _tool_call(
                "acknowledgement_compose",
                arguments={"intent": "clarification_response"},
                reason="Acknowledge that the reference clarification was applied to the pending fact update.",
                dispatch_mode="specialist_owned",
            ),
        ]
        writes_requested = [
            _write_request(
                "structured_case_persistence",
                tool_name="case_summary_update",
                requested=bool(graph_update_input_text),
                input_text=graph_update_input_text,
                reason="Persist the pending fact after reference clarification.",
            ),
            _write_request(
                "graph_update_schedule",
                tool_name="schedule_graph_update",
                requested=bool(graph_update_input_text),
                input_text=graph_update_input_text,
                reason="Schedule graph update after reference clarification.",
            ),
        ]
        plan.update(
            response_mode="tracked_case_refresh",
            response_style="tracked_case_refresh",
            specialist="case_update_agent",
            plan_summary="Apply the user's clarification to the pending fact update, then persist and update the graph.",
            context_requirements=["pending_clarification", "message_text"],
            tool_sequence=tool_sequence,
            tool_calls=tool_sequence,
            writes_requested=writes_requested,
            needs_legal_rag=False,
            needs_graph_context=False,
            needs_structured_persistence=True,
            final_response_strategy="acknowledgement",
            missing_requirements=[] if graph_update_input_text else ["pending_graph_update_input_text"],
            fallback_allowed=False,
        )
        return plan

    if intent in {"subject_reference", "clarification_response"} and route == "DOSSIER_MEMORY":
        tool_sequence = [
            _tool_call(
                "current_conversation_graph_query",
                arguments={"conversation_id": conversation_id},
                reason="Load current conversation graph before answering the dossier memory question.",
            ),
            _tool_call(
                "current_conversation_messages_query",
                arguments={"conversation_id": conversation_id, "limit": 12},
                reason="Use recent conversation messages as secondary evidence for dossier memory resolution.",
                dispatch_mode="planned_contract",
            ),
            _tool_call(
                "dossier_graph_context_query",
                arguments={
                    "conversation_id": conversation_id,
                    "dossier_id": dossier_id or 0,
                    "query": normalized_question,
                    "limit": 20,
                },
                reason="If current conversation evidence is incomplete, use dossier-scoped graph evidence.",
                dispatch_mode="planner_direct",
            ),
            _tool_call(
                "subject_reference_resolve",
                arguments={
                    "question": normalized_question,
                    "message_understanding": mu,
                    "recent_messages": (
                        request_context.recent_messages
                        if isinstance(request_context, AgenticRequestContext)
                        else []
                    ),
                    "current_conversation_id": conversation_id,
                },
                reason="Answer the fact memory question from current-conversation and dossier memory scopes.",
            ),
            _tool_call(
                "memory_answer_compose",
                arguments={
                    "question": normalized_question,
                    "message_understanding": mu,
                    "recent_messages": (
                        request_context.recent_messages
                        if isinstance(request_context, AgenticRequestContext)
                        else []
                    ),
                    "current_conversation_id": conversation_id,
                },
                reason="Compose the user-facing dossier memory answer from resolver output.",
                dispatch_mode="specialist_owned",
            ),
        ]
        plan.update(
            response_mode="dossier_memory_answer",
            response_style="dossier_memory_answer",
            specialist="dossier_memory_agent",
            plan_summary="Answer dossier memory questions from current conversation graph/messages before escalating to dossier graph.",
            context_requirements=["current_conversation_graph", "recent_messages", "dossier_graph_context"],
            tool_sequence=tool_sequence,
            tool_calls=tool_sequence,
            writes_requested=[],
            needs_legal_rag=False,
            needs_graph_context=True,
            needs_structured_persistence=False,
            final_response_strategy="memory_answer",
            missing_requirements=[],
            fallback_allowed=False,
        )
        return plan

    if intent == "fact_update" and route == "CASE_UPDATE":
        has_bound_dossier = isinstance(dossier_id, int) and dossier_id > 0
        is_temporary = isinstance(request_context, AgenticRequestContext) and request_context.temporary
        reference_guard = (
            legacy_plan.get("reference_guard")
            if isinstance(legacy_plan.get("reference_guard"), dict)
            else {}
        )

        if is_temporary or not has_bound_dossier:
            tool_sequence = [
                _tool_call(
                    "legal_answer_compose",
                    arguments={"question": normalized_question},
                    reason="Compose temporary case analysis without persistence.",
                    dispatch_mode="specialist_owned",
                ),
            ]
            plan.update(
                response_mode="temporary_case_analysis",
                response_style="temporary_case_analysis",
                specialist="legal_agent",
                plan_summary=(
                    "Treat the message as a temporary case analysis request without persistence writes."
                    if is_temporary
                    else "Conversation is not bound to a dossier, so treat as temporary analysis without persistence writes."
                ),
                context_requirements=["message_text"],
                tool_sequence=tool_sequence,
                tool_calls=tool_sequence,
                writes_requested=[],
                needs_legal_rag=False,
                needs_graph_context=False,
                needs_structured_persistence=False,
                final_response_strategy="temporary_case_analysis",
                missing_requirements=[],
                fallback_allowed=False,
            )
            return plan

        if reference_guard.get("needs_clarification"):
            tool_sequence = [
                _tool_call(
                    "acknowledgement_compose",
                    arguments={"intent": "fact_update"},
                    reason="Ask the user to confirm ambiguous graph references before any case writes.",
                    dispatch_mode="specialist_owned",
                ),
            ]
            plan.update(
                response_mode="clarification",
                response_style="clarification",
                specialist="case_update_agent",
                plan_summary="Fact update contains unresolved oral references, so ask clarification before persistence or graph writes.",
                context_requirements=["current_conversation_graph", "pending_clarification"],
                tool_sequence=tool_sequence,
                tool_calls=tool_sequence,
                writes_requested=[],
                needs_legal_rag=False,
                needs_graph_context=True,
                needs_structured_persistence=False,
                final_response_strategy="ask_clarification",
                missing_requirements=["graph_reference_resolution"],
                fallback_allowed=False,
            )
            return plan

        tool_sequence = [
            _tool_call(
                "case_fact_extract",
                arguments={"text": normalized_question},
                reason="Extract structured factual elements from the current user message.",
                dispatch_mode="planned_contract",
            ),
            _tool_call(
                "case_summary_update",
                arguments={"conversation_id": conversation_id, "case_summary": normalized_question},
                reason="Plan a structured case summary update using the current factual message.",
                dispatch_mode="planned_contract",
            ),
            _tool_call(
                "schedule_graph_update",
                arguments={"conversation_id": conversation_id, "user_id": 0},
                reason="Plan a graph update schedule using the current factual message.",
                dispatch_mode="planned_contract",
            ),
            _tool_call(
                "acknowledgement_compose",
                arguments={"intent": "fact_update"},
                reason="Compose a lightweight acknowledgement for the factual update.",
                dispatch_mode="specialist_owned",
            ),
        ]
        writes_requested = [
            _write_request(
                "structured_case_persistence",
                tool_name="case_summary_update",
                requested=True,
                input_text=normalized_question,
                reason="Persist the new factual message into structured case summary state.",
            ),
            _write_request(
                "graph_update_schedule",
                tool_name="schedule_graph_update",
                requested=True,
                input_text=normalized_question,
                reason="Schedule graph extraction/update from the new factual message.",
            ),
        ]
        plan.update(
            response_mode="tracked_case_refresh",
            response_style="tracked_case_refresh",
            specialist="case_update_agent",
            plan_summary="Treat the message as a tracked case refresh and plan state/graph updates around the factual message.",
            context_requirements=["message_text", "persistence_consent"],
            tool_sequence=tool_sequence,
            tool_calls=tool_sequence,
            writes_requested=writes_requested,
            needs_legal_rag=False,
            needs_graph_context=False,
            needs_structured_persistence=True,
            final_response_strategy="acknowledgement",
            missing_requirements=[],
            fallback_allowed=False,
        )
        return plan

    if intent == "confirm_previous_fact" and route == "CASE_UPDATE":
        previous_fact_text = _resolved_previous_fact_text(
            request_context=request_context,
            execution_plan=legacy_plan,
        )
        base_tool_sequence = [
            _tool_call(
                "acknowledgement_compose",
                arguments={"intent": "confirm_previous_fact"},
                reason="Compose a lightweight acknowledgement for the previous fact confirmation.",
                dispatch_mode="specialist_owned",
            ),
        ]
        has_bound_dossier = isinstance(dossier_id, int) and dossier_id > 0
        is_temporary = isinstance(request_context, AgenticRequestContext) and request_context.temporary
        response_mode = "temporary_case_analysis" if (is_temporary or not has_bound_dossier) else "tracked_case_refresh"
        plan.update(
            response_mode=response_mode,
            response_style=response_mode,
            specialist="case_update_agent",
            context_requirements=["previous_factual_user_message"],
            tool_sequence=base_tool_sequence,
            tool_calls=base_tool_sequence,
            needs_legal_rag=False,
            needs_graph_context=False,
            needs_structured_persistence=True,
            fallback_allowed=False,
        )
        if previous_fact_text:
            if is_temporary or not has_bound_dossier:
                plan.update(
                    plan_summary=(
                        "Use the resolved previous factual user message only for temporary analysis without persistence."
                        if is_temporary
                        else "Conversation is not bound to a dossier, so use resolved previous fact only for temporary analysis."
                    ),
                    tool_sequence=base_tool_sequence,
                    tool_calls=base_tool_sequence,
                    writes_requested=[],
                    needs_structured_persistence=False,
                    final_response_strategy="temporary_case_analysis",
                    missing_requirements=[],
                )
                return plan

            full_tool_sequence = [
                _tool_call(
                    "case_summary_update",
                    arguments={"conversation_id": conversation_id, "case_summary": previous_fact_text},
                    reason="Plan persistence using the resolved previous factual user message, not the confirmation short sentence.",
                    dispatch_mode="planned_contract",
                ),
                _tool_call(
                    "schedule_graph_update",
                    arguments={"conversation_id": conversation_id, "user_id": 0},
                    reason="Plan graph update using the resolved previous factual user message.",
                    dispatch_mode="planned_contract",
                ),
                *base_tool_sequence,
            ]
            plan.update(
                plan_summary="Bind confirmation to the resolved previous factual user message before planning tracked refresh writes.",
                tool_sequence=full_tool_sequence,
                tool_calls=full_tool_sequence,
                writes_requested=[
                    _write_request(
                        "structured_case_persistence",
                        tool_name="case_summary_update",
                        requested=True,
                        input_text=previous_fact_text,
                        reason="Persist the resolved previous factual user message.",
                    ),
                    _write_request(
                        "graph_update_schedule",
                        tool_name="schedule_graph_update",
                        requested=True,
                        input_text=previous_fact_text,
                        reason="Schedule graph update from the resolved previous factual user message.",
                    ),
                ],
                final_response_strategy="acknowledgement",
                missing_requirements=[],
            )
            return plan

        plan.update(
            response_mode="clarification",
            response_style="clarification",
            plan_summary="Previous fact confirmation is missing a verified factual target, so writes must stay blocked.",
            tool_sequence=base_tool_sequence,
            tool_calls=base_tool_sequence,
            writes_requested=[],
            final_response_strategy="ask_clarification",
            missing_requirements=["previous_factual_user_message"],
        )
        return plan

    if intent == "legal_question" and route == "LEGAL_QA":
        explicit_article = _looks_like_explicit_law_article_question(normalized_question)
        lookup_tool = "exact_law_lookup" if explicit_article else "legal_rag_search"
        tool_sequence = [
            _tool_call(
                lookup_tool,
                arguments={"query": normalized_question},
                reason=(
                    "Use exact law lookup for an explicit statute article question."
                    if explicit_article
                    else "Use legal retrieval before composing the legal answer."
                ),
            ),
            _tool_call(
                "legal_answer_compose",
                arguments={"question": normalized_question},
                reason="Compose the final legal answer from retrieval results.",
                dispatch_mode="specialist_owned",
            ),
        ]
        plan.update(
            response_mode="legal_answer",
            response_style="legal_answer",
            specialist="legal_qa_agent",
            plan_summary="通过法条查询和法律回答专员回答当前法律问题，不写入案情或图谱。",
            context_requirements=["message_text"],
            tool_sequence=tool_sequence,
            tool_calls=tool_sequence,
            writes_requested=[],
            needs_legal_rag=True,
            needs_graph_context=False,
            needs_structured_persistence=False,
            final_response_strategy="answer_legal_question_without_case_pollution",
            missing_requirements=[],
            fallback_allowed=False,
        )
        return plan

    if intent == "advice_question" and route in {"GENERAL_CONSULT", "LEGAL_QA"}:
        tool_sequence = [
            _tool_call(
                "legal_rag_search",
                arguments={"query": normalized_question},
                reason="Retrieve legal background before composing advice.",
            ),
            _tool_call(
                "legal_answer_compose",
                arguments={"question": normalized_question},
                reason="Compose the final advice answer from retrieval results.",
                dispatch_mode="specialist_owned",
            ),
        ]
        plan.update(
            response_mode="legal_answer",
            response_style="legal_answer",
            specialist="legal_qa_agent",
            plan_summary="Answer an advice question through legal retrieval and advice composition without case writes.",
            context_requirements=["message_text"],
            tool_sequence=tool_sequence,
            tool_calls=tool_sequence,
            writes_requested=[],
            needs_legal_rag=True,
            needs_graph_context=False,
            needs_structured_persistence=False,
            final_response_strategy="legal_answer",
            missing_requirements=[],
            fallback_allowed=False,
        )
        return plan

    if intent == "document_request" and route in {"DOCUMENT_ASSIST", "GENERAL_CONSULT", "UNKNOWN"}:
        plan.update(
            route="DOCUMENT_ASSIST",
            response_mode="legacy_document_fallback",
            response_style="legacy_document_fallback",
            specialist="legacy_document_specialist",
            plan_summary="Route document request into the planner contract, then allow legacy specialist fallback.",
            context_requirements=["message_text"],
            tool_sequence=[],
            tool_calls=[],
            writes_requested=[],
            needs_legal_rag=False,
            needs_graph_context=False,
            needs_structured_persistence=False,
            final_response_strategy="document_assist",
            missing_requirements=[],
            fallback_allowed=True,
        )
        return plan

    return plan
