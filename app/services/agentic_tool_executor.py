from __future__ import annotations

import asyncio
import json
from typing import Any

from app.services.case_update_acknowledgement import build_case_update_acknowledgement
from app.services.memory_resolver import answer_subject_reference_message_async
from app.services.tool_registry import get_tool_contract


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


def _summarize_graph_payload(graph: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
    payload = graph if isinstance(graph, dict) else {}
    nodes = payload.get("nodes") if isinstance(payload.get("nodes"), list) else []
    edges = payload.get("edges") if isinstance(payload.get("edges"), list) else []
    organization_labels = [
        _normalize_text(node.get("display_label") or node.get("label"))
        for node in nodes
        if isinstance(node, dict)
        and _normalize_text(node.get("type") or node.get("category")).lower() in {"organization", "company", "employer"}
        and _normalize_text(node.get("display_label") or node.get("label"))
    ]
    summary = f"loaded {len(nodes)} nodes and {len(edges)} edges"
    if organization_labels:
        summary += f"; organizations: {', '.join(organization_labels[:3])}"
    return summary, {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "organization_labels": organization_labels[:5],
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
) -> dict[str, Any]:
    contract = get_tool_contract(tool_name)

    if tool_name == "current_conversation_graph_query":
        graph_payload = current_conversation_graph
        if not isinstance(graph_payload, dict):
            from app.services import graph_extractor as _graph_extractor

            get_case_graph = getattr(_graph_extractor, "get_case_graph", None)
            if callable(get_case_graph):
                graph_payload = await get_case_graph(conversation_id)
            else:
                graph_payload = {"query": "", "nodes": [], "edges": []}
        result_summary, details = _summarize_graph_payload(graph_payload)
        return {
            "tool_name": tool_name,
            "contract": contract,
            "status": "success",
            "result": graph_payload,
            "result_summary": result_summary,
            "details": details,
        }

    if tool_name == "dossier_graph_context_query":
        payload = dossier_graph_context if isinstance(dossier_graph_context, dict) else {
            "participants": [],
            "participant_mentions": [],
            "matched_nodes": [],
            "matched_edges": [],
            "graph_nodes": [],
        }
        matched_nodes = payload.get("matched_nodes") if isinstance(payload.get("matched_nodes"), list) else []
        graph_nodes = payload.get("graph_nodes") if isinstance(payload.get("graph_nodes"), list) else []
        return {
            "tool_name": tool_name,
            "contract": contract,
            "status": "success",
            "result": payload,
            "result_summary": (
                f"loaded dossier graph context with {len(graph_nodes)} graph nodes and {len(matched_nodes)} matched nodes"
            ),
            "details": {
                "graph_node_count": len(graph_nodes),
                "matched_node_count": len(matched_nodes),
            },
        }

    if tool_name == "subject_reference_resolve":
        result = await answer_subject_reference_message_async(
            question=question,
            message_understanding=message_understanding or {},
            dossier_context=dossier_context,
            dossier_graph_context=dossier_graph_context,
            current_conversation_graph=current_conversation_graph,
            case_summary=case_summary,
            recent_messages=recent_messages,
            current_conversation_id=conversation_id,
        )
        resolution_trace = result.get("resolution_trace") if isinstance(result, dict) else {}
        return {
            "tool_name": tool_name,
            "contract": contract,
            "status": "success",
            "result": result,
            "result_summary": _normalize_text(result.get("response") if isinstance(result, dict) else ""),
            "details": resolution_trace if isinstance(resolution_trace, dict) else {},
        }

    if tool_name == "exact_law_lookup":
        try:
            from app.agent.tools import search_law_articles
        except Exception as exc:
            return {
                "tool_name": tool_name,
                "contract": contract,
                "status": "fallback",
                "result": [],
                "result_summary": f"exact law lookup unavailable: {exc}",
                "details": {"error": str(exc)},
            }

        try:
            raw = await asyncio.to_thread(
                _invoke_sync_tool,
                search_law_articles,
                query=question,
                chat_history=chat_history or "",
            )
        except Exception as exc:
            return {
                "tool_name": tool_name,
                "contract": contract,
                "status": "fallback",
                "result": [],
                "result_summary": f"exact law lookup failed: {exc}",
                "details": {"error": str(exc)},
            }
        parsed = _safe_json_load(raw)
        documents = parsed if isinstance(parsed, list) else []
        exact_matches = 0
        for item in documents:
            metadata = item.get("metadata") if isinstance(item, dict) else {}
            if isinstance(metadata, dict) and metadata.get("_exact_lookup"):
                exact_matches += 1
        return {
            "tool_name": tool_name,
            "contract": contract,
            "status": "success",
            "result": documents,
            "result_summary": f"retrieved {len(documents)} legal passages; exact matches: {exact_matches}",
            "details": {
                "document_count": len(documents),
                "exact_match_count": exact_matches,
            },
        }

    if tool_name == "legal_rag_search":
        try:
            from app.agent.tools import search_law_articles
        except Exception as exc:
            return {
                "tool_name": tool_name,
                "contract": contract,
                "status": "fallback",
                "result": [],
                "result_summary": f"legal search unavailable: {exc}",
                "details": {"error": str(exc)},
            }

        try:
            raw = await asyncio.to_thread(
                _invoke_sync_tool,
                search_law_articles,
                query=question,
                chat_history=chat_history or "",
            )
        except Exception as exc:
            return {
                "tool_name": tool_name,
                "contract": contract,
                "status": "fallback",
                "result": [],
                "result_summary": f"legal search failed: {exc}",
                "details": {"error": str(exc)},
            }
        parsed = _safe_json_load(raw)
        documents = parsed if isinstance(parsed, list) else []
        return {
            "tool_name": tool_name,
            "contract": contract,
            "status": "success",
            "result": documents,
            "result_summary": f"retrieved {len(documents)} legal passages",
            "details": {"document_count": len(documents)},
        }

    if tool_name == "acknowledgement_compose":
        result = build_case_update_acknowledgement(
            plan=execution_plan or {},
            intent=_normalize_text((message_understanding or {}).get("intent")).lower(),
            user_message_text=question,
            persisted_fact_text=persisted_fact_text,
            graph_update_scheduled=graph_update_scheduled,
            graph_update_actions=graph_update_actions,
            dossier_id=dossier_id,
            conversation_id=conversation_id,
            persistence_consent=persistence_consent,
            route_intent=_normalize_text((execution_plan or {}).get("primary_route")).upper() or "CASE_UPDATE",
            fallback_reason=None,
        )
        return {
            "tool_name": tool_name,
            "contract": contract,
            "status": "success",
            "result": result,
            "result_summary": _normalize_text(result.get("text")),
            "details": result.get("trace") if isinstance(result.get("trace"), dict) else {},
        }

    return {
        "tool_name": tool_name,
        "contract": contract,
        "status": "skipped",
        "result": None,
        "result_summary": "tool executor does not execute this tool directly yet",
        "details": {},
    }
