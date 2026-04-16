"""
Vector researcher node: RAG retrieval + graph memory loading.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.agent.graph import AgentState
from app.agent.nodes._llm import LAW_TOOL, CASE_TOOL
from app.services.graph_extractor import format_graph_memory, get_user_cases_graph_overview

logger = logging.getLogger(__name__)


async def vector_researcher_node(self, state: AgentState) -> AgentState:
    current_query = state.get("current_query") or state["question"]
    route_intent = str(state.get("route_intent", "GENERAL_CONSULT")).upper()
    chat_history = state.get("chat_history", "")
    case_summary = state.get("case_summary", "")
    conversation_id = state.get("conversation_id")
    tool_calls = state.get("tool_calls") or []

    if not tool_calls or state.get("routed_query") != current_query:
        tool_calls = self._patched_build_tool_calls_for_intent(route_intent, current_query)

    enriched_tool_calls = []
    for call in tool_calls:
        arguments = dict(call["arguments"])
        if call["name"] in {LAW_TOOL, CASE_TOOL}:
            arguments["chat_history"] = case_summary or chat_history
        enriched_tool_calls.append({"name": call["name"], "arguments": arguments})

    graph_context = (state.get("graph_context") or "").strip() or (state.get("graph_memory") or "").strip()
    retrieval_task = self._execute_tool_calls(enriched_tool_calls)
    graph_task = None
    if conversation_id:
        graph_task = format_graph_memory(int(conversation_id))

    if graph_task is not None:
        tool_results, latest_graph_memory = await asyncio.gather(retrieval_task, graph_task)
        graph_context = (
            f"当前案件图谱事实：\n{latest_graph_memory.strip()}"
            if isinstance(latest_graph_memory, str) and latest_graph_memory.strip()
            else graph_context
        )
    else:
        tool_results = await retrieval_task

    user_id = state.get("user_id")
    if user_id and conversation_id:
        try:
            user_cases = await get_user_cases_graph_overview(int(user_id))
            history_cases = [case for case in user_cases if case.get("case_id") != int(conversation_id)]
            if history_cases:
                history_lines = []
                for case in history_cases[:5]:
                    case_id = case.get("case_id", "?")
                    sources = case.get("source_names", [])
                    targets = case.get("target_names", [])
                    all_entities = list(dict.fromkeys([*sources, *targets]))[:8]
                    history_lines.append(f"  案件#{case_id}: {', '.join(all_entities)}")
                if history_lines:
                    graph_context = (
                        f"{graph_context}\n\n"
                        f"用户历史案件实体（可能与当前案件相关）：\n"
                        + "\n".join(history_lines)
                    )
        except Exception as exc:
            logger.warning("Failed to load user case history for graph context: %s", exc)

    return {
        "current_query": current_query,
        "routed_query": current_query,
        "route_intent": route_intent,
        "tool_calls": enriched_tool_calls,
        "tool_results": self._merge_tool_results([], tool_results),
        "graph_context": graph_context,
        "graph_memory": graph_context,
    }
