from __future__ import annotations

import asyncio
import inspect
import json
import time
from typing import Any

from app.services.case_update_acknowledgement import build_case_update_acknowledgement
from app.services.memory_resolver import (
    answer_subject_reference_message_async,
    build_subject_reference_response_text,
)
from app.services.tool_registry import ToolContract, get_tool_contract


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _safe_json_load(payload: str) -> Any:
    try:
        return json.loads(payload)
    except Exception:
        return None


def _invoke_sync_tool(tool_obj: Any, **kwargs: Any) -> Any:
    if callable(tool_obj):
        return tool_obj(**kwargs)
    invoke = getattr(tool_obj, "invoke", None)
    if callable(invoke):
        return invoke(kwargs)
    raise TypeError(f"Unsupported tool object: {tool_obj!r}")


def _side_effect_summary(contract: ToolContract | None) -> str | None:
    if contract is None or not contract.side_effects:
        return None
    return ", ".join(contract.side_effects)


def _input_summary(tool_call: dict[str, Any] | None) -> str | None:
    if not isinstance(tool_call, dict):
        return None
    arguments = tool_call.get("arguments") if isinstance(tool_call.get("arguments"), dict) else {}
    parts: list[str] = []
    for key in ("query", "question", "conversation_id", "dossier_id", "intent", "limit"):
        if key not in arguments:
            continue
        normalized = _normalize_text(arguments.get(key))
        if not normalized:
            continue
        if key in {"query", "question"} and len(normalized) > 80:
            normalized = normalized[:77].rstrip() + "..."
        parts.append(f"{key}={normalized}")
    return "; ".join(parts) or None


def _preview_text(value: Any, *, limit: int = 120) -> str:
    normalized = _normalize_text(value)
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _output_summary(tool_name: str, result: Any) -> str:
    if tool_name in {"current_conversation_graph_query", "dossier_graph_context_query"} and isinstance(result, dict):
        nodes = result.get("nodes") if isinstance(result.get("nodes"), list) else []
        edges = result.get("edges") if isinstance(result.get("edges"), list) else []
        graph_nodes = result.get("graph_nodes") if isinstance(result.get("graph_nodes"), list) else []
        return f"nodes={len(nodes) or len(graph_nodes)} edges={len(edges)}"
    if tool_name == "current_conversation_messages_query" and isinstance(result, dict):
        messages = result.get("recent_messages") if isinstance(result.get("recent_messages"), list) else []
        return f"recent_messages={len(messages)}"
    if tool_name in {"exact_law_lookup", "legal_rag_search"} and isinstance(result, list):
        return f"documents={len(result)}"
    if tool_name == "subject_reference_resolve" and isinstance(result, dict):
        return _preview_text(result.get("response_text"))
    if tool_name in {"memory_answer_compose", "legal_answer_compose"} and isinstance(result, dict):
        return _preview_text(result.get("response"))
    if tool_name == "acknowledgement_compose" and isinstance(result, dict):
        return _preview_text(result.get("text"))
    return _preview_text(result)


def _executor_allows_contract(contract: ToolContract | None) -> tuple[bool, str | None]:
    if contract is None:
        return False, "tool_not_registered"
    if not contract.planner_callable:
        return False, "tool_not_planner_callable"
    if contract.has_side_effects:
        return False, "write_side_effect_not_executable_in_h8_2b"
    allowed_categories = {"retrieval", "graph_read", "memory", "composition"}
    if contract.category in allowed_categories:
        return True, None
    if contract.migration_status == "specialist_only":
        return True, None
    return False, "tool_category_not_executor_enabled"


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _base_tool_result(
    tool_name: str,
    *,
    contract: ToolContract | None,
    status: str,
    input_summary: str | None,
    output_summary: str | None,
    latency_ms: int,
    error: str | None,
    result: Any = None,
    details: dict[str, Any] | None = None,
    blocked_reason: str | None = None,
    dispatch_mode: str | None = None,
) -> dict[str, Any]:
    return {
        "tool_name": tool_name,
        "status": _normalize_text(status).lower() or "success",
        "input_summary": _normalize_text(input_summary) or None,
        "output_summary": _normalize_text(output_summary) or None,
        "result_summary": _normalize_text(output_summary) or None,
        "latency_ms": max(0, int(latency_ms)),
        "error": _normalize_text(error) or None,
        "side_effect": _side_effect_summary(contract),
        "registry_category": (
            _normalize_text(getattr(contract, "category", "")) or None
        ),
        "planner_callable": (
            bool(getattr(contract, "planner_callable"))
            if contract is not None
            else None
        ),
        "dispatch_mode": _normalize_text(dispatch_mode) or None,
        "blocked_reason": _normalize_text(blocked_reason) or None,
        "details": details or {},
        "result": result,
        "contract": contract,
    }


async def _execute_current_conversation_graph_query(
    *,
    conversation_id: int,
    current_conversation_graph: dict[str, Any] | None,
) -> tuple[Any, dict[str, Any]]:
    graph_payload = current_conversation_graph
    if not isinstance(graph_payload, dict):
        from app.services import graph_extractor as _graph_extractor

        get_case_graph = getattr(_graph_extractor, "get_case_graph", None)
        if callable(get_case_graph):
            graph_payload = await get_case_graph(conversation_id)
        else:
            graph_payload = {"query": "", "nodes": [], "edges": []}
    nodes = graph_payload.get("nodes") if isinstance(graph_payload.get("nodes"), list) else []
    edges = graph_payload.get("edges") if isinstance(graph_payload.get("edges"), list) else []
    return graph_payload, {
        "node_count": len(nodes),
        "edge_count": len(edges),
    }


async def _execute_current_conversation_messages_query(
    *,
    recent_messages: list[dict[str, Any]] | None,
) -> tuple[Any, dict[str, Any]]:
    normalized_messages = [
        item
        for item in list(recent_messages or [])
        if isinstance(item, dict)
    ]
    return {"recent_messages": normalized_messages}, {"message_count": len(normalized_messages)}


async def _execute_dossier_graph_context_query(
    *,
    dossier_graph_context: dict[str, Any] | None,
) -> tuple[Any, dict[str, Any]]:
    payload = dossier_graph_context if isinstance(dossier_graph_context, dict) else {
        "participants": [],
        "participant_mentions": [],
        "matched_nodes": [],
        "matched_edges": [],
        "graph_nodes": [],
    }
    matched_nodes = payload.get("matched_nodes") if isinstance(payload.get("matched_nodes"), list) else []
    graph_nodes = payload.get("graph_nodes") if isinstance(payload.get("graph_nodes"), list) else []
    return payload, {
        "graph_node_count": len(graph_nodes),
        "matched_node_count": len(matched_nodes),
    }


async def _execute_subject_reference_resolve(runtime: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    response_text, confidence_score, resolution_trace = build_subject_reference_response_text(
        question=runtime.get("question") or "",
        message_understanding=runtime.get("message_understanding") or {},
        dossier_context=runtime.get("dossier_context"),
        dossier_graph_context=runtime.get("dossier_graph_context"),
        current_conversation_graph=runtime.get("current_conversation_graph"),
        recent_messages=runtime.get("recent_messages"),
        current_conversation_id=int(runtime.get("conversation_id") or 0),
    )
    payload = {
        "response_text": response_text,
        "confidence_score": confidence_score,
        "memory_resolution": resolution_trace if isinstance(resolution_trace, dict) else {},
        "pending_clarification": (
            resolution_trace.get("pending_clarification")
            if isinstance(resolution_trace, dict)
            and isinstance(resolution_trace.get("pending_clarification"), dict)
            else None
        ),
    }
    return payload, payload.get("memory_resolution") if isinstance(payload.get("memory_resolution"), dict) else {}


async def _execute_memory_answer_compose(runtime: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    prior_result = runtime.get("subject_reference_resolve_result")
    if isinstance(prior_result, dict):
        resolution_trace = (
            prior_result.get("memory_resolution")
            if isinstance(prior_result.get("memory_resolution"), dict)
            else {}
        )
        answer = {
            "response": str(prior_result.get("response_text") or ""),
            "sources": [],
            "route_intent": "DOSSIER_MEMORY",
            "case_summary": runtime.get("case_summary") or "",
            "is_final": True,
            "confidence_score": float(prior_result.get("confidence_score") or 0.0),
            "missing_info": [],
            "resolution_trace": resolution_trace,
            "pending_clarification": (
                prior_result.get("pending_clarification")
                if isinstance(prior_result.get("pending_clarification"), dict)
                else None
            ),
        }
        return answer, resolution_trace

    result = await answer_subject_reference_message_async(
        question=runtime.get("question") or "",
        message_understanding=runtime.get("message_understanding") or {},
        dossier_context=runtime.get("dossier_context"),
        dossier_graph_context=runtime.get("dossier_graph_context"),
        current_conversation_graph=runtime.get("current_conversation_graph"),
        case_summary=runtime.get("case_summary"),
        recent_messages=runtime.get("recent_messages"),
        current_conversation_id=int(runtime.get("conversation_id") or 0),
    )
    return result, (
        result.get("resolution_trace")
        if isinstance(result.get("resolution_trace"), dict)
        else {}
    )


async def _execute_search_law_articles(
    *,
    question: str,
    chat_history: str | None,
) -> list[dict[str, Any]]:
    from app.agent.tools import search_law_articles

    raw = await asyncio.to_thread(
        _invoke_sync_tool,
        search_law_articles,
        query=question,
        chat_history=chat_history or "",
    )
    parsed = _safe_json_load(raw)
    return parsed if isinstance(parsed, list) else []


async def _execute_exact_law_lookup(runtime: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    documents = await _execute_search_law_articles(
        question=runtime.get("question") or "",
        chat_history=runtime.get("chat_history"),
    )
    exact_matches = 0
    for item in documents:
        metadata = item.get("metadata") if isinstance(item, dict) else {}
        if isinstance(metadata, dict) and metadata.get("_exact_lookup"):
            exact_matches += 1
    return documents, {
        "document_count": len(documents),
        "exact_match_count": exact_matches,
    }


async def _execute_legal_rag_search(runtime: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    documents = await _execute_search_law_articles(
        question=runtime.get("question") or "",
        chat_history=runtime.get("chat_history"),
    )
    return documents, {"document_count": len(documents)}


async def _execute_legal_answer_compose(runtime: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    handler = runtime.get("legal_answer_handler")
    if handler is None:
        raise RuntimeError("legal_answer_handler is required for legal_answer_compose")

    result = await _maybe_await(
        handler(
            runtime.get("user_id"),
            runtime.get("session_id"),
            runtime.get("question") or "",
            chat_history=runtime.get("chat_history"),
            case_summary=runtime.get("case_summary"),
            graph_memory=runtime.get("graph_memory"),
            is_precise_mode=bool(runtime.get("is_precise_mode")),
            proactive_graph_mode=bool(runtime.get("proactive_graph_mode")),
            conversation_id=runtime.get("conversation_id"),
            saved_case_state=runtime.get("saved_case_state"),
            dossier_context=runtime.get("advisor_dossier_context"),
            dossier_graph_summary=runtime.get("advisor_dossier_graph_summary"),
            dossier_graph_context=runtime.get("advisor_dossier_graph_context"),
        )
    )
    return result, {
        "route_intent": _normalize_text(result.get("route_intent")) if isinstance(result, dict) else "",
        "used_specialist": "legal_agent.ask",
    }


async def _execute_acknowledgement_compose(runtime: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    result = build_case_update_acknowledgement(
        plan=runtime.get("execution_plan") or {},
        intent=_normalize_text((runtime.get("message_understanding") or {}).get("intent")).lower(),
        user_message_text=runtime.get("question") or "",
        persisted_fact_text=runtime.get("persisted_fact_text"),
        graph_update_scheduled=bool(runtime.get("graph_update_scheduled")),
        graph_update_actions=runtime.get("graph_update_actions"),
        dossier_id=runtime.get("dossier_id"),
        conversation_id=int(runtime.get("conversation_id") or 0),
        persistence_consent=runtime.get("persistence_consent"),
        route_intent=_normalize_text((runtime.get("execution_plan") or {}).get("primary_route")).upper() or "CASE_UPDATE",
        fallback_reason=runtime.get("fallback_reason"),
    )
    return result, (
        result.get("trace")
        if isinstance(result.get("trace"), dict)
        else {}
    )


async def _dispatch_supported_tool(
    tool_name: str,
    *,
    runtime: dict[str, Any],
) -> tuple[Any, dict[str, Any]]:
    if tool_name == "current_conversation_graph_query":
        return await _execute_current_conversation_graph_query(
            conversation_id=int(runtime.get("conversation_id") or 0),
            current_conversation_graph=runtime.get("current_conversation_graph"),
        )
    if tool_name == "current_conversation_messages_query":
        return await _execute_current_conversation_messages_query(
            recent_messages=runtime.get("recent_messages"),
        )
    if tool_name == "dossier_graph_context_query":
        return await _execute_dossier_graph_context_query(
            dossier_graph_context=runtime.get("dossier_graph_context"),
        )
    if tool_name == "subject_reference_resolve":
        return await _execute_subject_reference_resolve(runtime)
    if tool_name == "memory_answer_compose":
        return await _execute_memory_answer_compose(runtime)
    if tool_name == "exact_law_lookup":
        return await _execute_exact_law_lookup(runtime)
    if tool_name == "legal_rag_search":
        return await _execute_legal_rag_search(runtime)
    if tool_name == "legal_answer_compose":
        return await _execute_legal_answer_compose(runtime)
    if tool_name == "acknowledgement_compose":
        return await _execute_acknowledgement_compose(runtime)
    raise RuntimeError(f"Unsupported executor tool: {tool_name}")


async def _execute_tool_call(
    tool_call: dict[str, Any],
    *,
    runtime: dict[str, Any],
) -> dict[str, Any]:
    tool_name = _normalize_text(tool_call.get("tool_name"))
    contract = (
        tool_call.get("contract")
        if isinstance(tool_call.get("contract"), ToolContract)
        else get_tool_contract(tool_name)
    )
    started_at = time.perf_counter()
    input_summary = _input_summary(tool_call)

    allowed, blocked_reason = _executor_allows_contract(contract)
    if not allowed:
        return _base_tool_result(
            tool_name,
            contract=contract,
            status="blocked" if contract and contract.has_side_effects else "skipped",
            input_summary=input_summary,
            output_summary=blocked_reason,
            latency_ms=int((time.perf_counter() - started_at) * 1000),
            error=None,
            result=None,
            details={},
            blocked_reason=blocked_reason,
            dispatch_mode=tool_call.get("dispatch_mode"),
        )

    try:
        result, details = await _dispatch_supported_tool(tool_name, runtime=runtime)
    except Exception as exc:
        return _base_tool_result(
            tool_name,
            contract=contract,
            status="failed",
            input_summary=input_summary,
            output_summary=f"{tool_name} failed",
            latency_ms=int((time.perf_counter() - started_at) * 1000),
            error=str(exc),
            result=None,
            details={"error": str(exc)},
            blocked_reason=None,
            dispatch_mode=tool_call.get("dispatch_mode"),
        )

    return _base_tool_result(
        tool_name,
        contract=contract,
        status="success",
        input_summary=input_summary,
        output_summary=_output_summary(tool_name, result),
        latency_ms=int((time.perf_counter() - started_at) * 1000),
        error=None,
        result=result,
        details=details if isinstance(details, dict) else {},
        blocked_reason=None,
        dispatch_mode=tool_call.get("dispatch_mode"),
    )


def _runtime_from_context(plan: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    runtime = dict(context or {})
    runtime.setdefault("plan", plan)
    runtime.setdefault("question", runtime.get("question") or runtime.get("message_text") or "")
    runtime.setdefault("execution_plan", runtime.get("execution_plan") or {})
    runtime.setdefault("message_understanding", runtime.get("message_understanding") or {})
    runtime.setdefault("recent_messages", runtime.get("recent_messages") or [])
    runtime.setdefault("tool_outputs", {})
    runtime.setdefault("tool_results", [])
    return runtime


def _tool_sequence(plan: dict[str, Any] | None) -> list[dict[str, Any]]:
    if isinstance(plan, dict):
        sequence = plan.get("tool_sequence")
        if isinstance(sequence, list):
            return [item for item in sequence if isinstance(item, dict)]
        legacy = plan.get("tool_calls")
        if isinstance(legacy, list):
            return [item for item in legacy if isinstance(item, dict)]
    return []


async def execute_agentic_plan_tools(
    plan: dict[str, Any] | None,
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    runtime = _runtime_from_context(plan if isinstance(plan, dict) else {}, context)
    tool_results: list[dict[str, Any]] = []
    outputs_by_tool: dict[str, dict[str, Any]] = {}
    final_result: dict[str, Any] | None = None
    memory_resolution: dict[str, Any] | None = None

    for tool_call in _tool_sequence(plan):
        tool_result = await _execute_tool_call(tool_call, runtime=runtime)
        tool_results.append(tool_result)
        tool_name = _normalize_text(tool_result.get("tool_name"))
        if tool_name:
            outputs_by_tool[tool_name] = tool_result
        runtime["tool_results"] = tool_results
        runtime["tool_outputs"] = outputs_by_tool

        if tool_name == "current_conversation_graph_query" and isinstance(tool_result.get("result"), dict):
            runtime["current_conversation_graph"] = tool_result.get("result")
        elif tool_name == "current_conversation_messages_query":
            result = tool_result.get("result")
            if isinstance(result, dict) and isinstance(result.get("recent_messages"), list):
                runtime["recent_messages"] = result.get("recent_messages")
        elif tool_name == "subject_reference_resolve" and isinstance(tool_result.get("result"), dict):
            runtime["subject_reference_resolve_result"] = tool_result.get("result")
            if isinstance(tool_result["result"].get("memory_resolution"), dict):
                memory_resolution = tool_result["result"].get("memory_resolution")
        elif tool_name == "memory_answer_compose" and isinstance(tool_result.get("result"), dict):
            final_result = tool_result.get("result")
            resolution_trace = tool_result["result"].get("resolution_trace")
            if isinstance(resolution_trace, dict):
                memory_resolution = resolution_trace
        elif tool_name in {"legal_answer_compose", "acknowledgement_compose"} and isinstance(tool_result.get("result"), dict):
            final_result = tool_result.get("result")

    return {
        "tool_results": tool_results,
        "outputs_by_tool": outputs_by_tool,
        "final_result": final_result,
        "memory_resolution": memory_resolution,
        "current_conversation_graph": runtime.get("current_conversation_graph"),
        "recent_messages": runtime.get("recent_messages"),
    }


async def execute_agentic_tool(
    tool_name: str,
    *,
    question: str,
    conversation_id: int,
    dossier_id: int | None,
    message_understanding: dict[str, Any] | None,
    recent_messages: list[dict[str, Any]] | None,
    dossier_context: dict[str, Any] | None,
    dossier_graph_context: dict[str, Any] | None,
    current_conversation_graph: dict[str, Any] | None,
    case_summary: str | None,
    chat_history: str | None,
    persistence_consent: str | None,
    execution_plan: dict[str, Any] | None = None,
    graph_update_scheduled: bool = False,
    graph_update_actions: list[Any] | None = None,
    persisted_fact_text: str | None = None,
    **extra_context: Any,
) -> dict[str, Any]:
    plan = {
        "tool_sequence": [
            {
                "tool_name": tool_name,
                "arguments": {"question": question, "conversation_id": conversation_id},
                "dispatch_mode": "legacy_single_call",
                "contract": get_tool_contract(tool_name),
            }
        ]
    }
    result = await execute_agentic_plan_tools(
        plan,
        {
            "question": question,
            "conversation_id": conversation_id,
            "dossier_id": dossier_id,
            "message_understanding": message_understanding or {},
            "recent_messages": recent_messages or [],
            "dossier_context": dossier_context,
            "dossier_graph_context": dossier_graph_context,
            "current_conversation_graph": current_conversation_graph,
            "case_summary": case_summary,
            "chat_history": chat_history,
            "persistence_consent": persistence_consent,
            "execution_plan": execution_plan or {},
            "graph_update_scheduled": graph_update_scheduled,
            "graph_update_actions": graph_update_actions,
            "persisted_fact_text": persisted_fact_text,
            **extra_context,
        },
    )
    tool_results = result.get("tool_results") if isinstance(result.get("tool_results"), list) else []
    if tool_results:
        return tool_results[0]
    return _base_tool_result(
        tool_name,
        contract=get_tool_contract(tool_name),
        status="skipped",
        input_summary=None,
        output_summary="no_tool_result",
        latency_ms=0,
        error=None,
        result=None,
        details={},
        blocked_reason="no_tool_result",
        dispatch_mode="legacy_single_call",
    )


__all__ = [
    "execute_agentic_plan_tools",
    "execute_agentic_tool",
]
