"""
Shared LLM calling utilities and tool execution helpers.

Every function here is a method of LegalGraphAgent (takes ``self``).
They are imported by the domain node modules and also directly
attached to the class in legal_agent.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from json_repair import repair_json
from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.graph import AgentState
from app.agent.tools import AGENT_TOOLS

logger = logging.getLogger(__name__)

TOOL_REGISTRY = {tool.name: tool for tool in AGENT_TOOLS}
LAW_TOOL = "search_law_articles"
CASE_TOOL = "search_case_text"
GRAPH_TOOL = "query_neo4j_graph"
CHITCHAT_INTENT = "CHITCHAT"
TOOLS_INTENT = "TOOLS"
GREETING_RESPONSE = (
    "你好，我在。你可以直接告诉我你的法律问题，比如劳动纠纷、工伤认定、赔偿标准、合同争议或是否有类似案例。"
)


async def chat_model(self, llm, messages: list[dict[str, str]]) -> str:
    """Send messages to a ChatZhipuAI LLM and return the text response."""
    langchain_messages = []
    for message in messages:
        role = message.get("role")
        content = message.get("content", "")
        if role == "system":
            langchain_messages.append(SystemMessage(content=content))
        else:
            langchain_messages.append(HumanMessage(content=content))

    response = await llm.ainvoke(langchain_messages)
    return response.content if isinstance(response.content, str) else str(response.content)


def safe_json_load(self, raw_content: str, fallback: dict[str, Any]) -> dict[str, Any]:
    """Parse JSON with repair, returning fallback on failure."""
    try:
        parsed = json.loads(repair_json(raw_content))
        if isinstance(parsed, dict):
            return parsed
    except Exception as exc:
        logger.warning("Failed to parse JSON payload: %s", exc)
    return fallback


def extract_candidate_sources(self, tool_results: list[dict[str, Any]]) -> list[str]:
    """Extract unique source file names from RAG tool results."""
    sources: list[str] = []
    seen: set[str] = set()

    for item in tool_results:
        try:
            parsed = json.loads(item["result"])
        except Exception:
            continue

        if not isinstance(parsed, list):
            continue

        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            source = entry.get("source")
            if not source and isinstance(entry.get("metadata"), dict):
                source = entry["metadata"].get("source")
            if not isinstance(source, str):
                continue
            cleaned = source.strip()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            sources.append(cleaned)

    return sources


def normalize_sources(self, value: Any, candidate_sources: list[str]) -> list[str]:
    """Filter and normalize source names against allowed candidates."""
    if isinstance(value, str):
        raw_sources = [value]
    elif isinstance(value, list):
        raw_sources = [item for item in value if isinstance(item, str)]
    else:
        raw_sources = []

    allowed = {source: source for source in candidate_sources}
    normalized: list[str] = []
    seen: set[str] = set()

    for source in raw_sources:
        cleaned = source.strip()
        if not cleaned:
            continue
        matched = allowed.get(cleaned)
        if not matched or matched in seen:
            continue
        seen.add(matched)
        normalized.append(matched)

    if normalized:
        return normalized

    return candidate_sources[:5]


def merge_tool_results(
    self,
    base_results: list[dict[str, Any]],
    new_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge two tool result lists, deduplicating by content."""
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in [*base_results, *new_results]:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)

    return merged


def build_context_blocks(self, tool_results: list[dict[str, Any]]) -> str:
    """Format tool results into a human-readable context string."""
    blocks = []
    for item in tool_results:
        blocks.append(
            f"工具: {item['tool']}\n参数: {json.dumps(item['arguments'], ensure_ascii=False)}\n结果: {item['result']}"
        )
    return "\n\n".join(blocks)


async def execute_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Execute a single RAG tool call in a thread."""
    tool = TOOL_REGISTRY[tool_name]
    try:
        result = await asyncio.to_thread(tool.invoke, arguments)
        return {"tool": tool_name, "arguments": arguments, "result": result}
    except Exception as exc:
        logger.warning("Tool %s execution failed: %s", tool_name, exc)
        return {
            "tool": tool_name,
            "arguments": arguments,
            "result": json.dumps({"error": str(exc)}, ensure_ascii=False),
        }


async def execute_tool_calls(self, tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Execute multiple tool calls concurrently."""
    tasks = [
        self._execute_tool_call(call["name"], call["arguments"])
        for call in tool_calls
    ]
    return await asyncio.gather(*tasks)
