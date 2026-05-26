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
import os
from typing import Any

os.environ.setdefault("USE_TORCH", "0")

from json_repair import repair_json
from openai import AsyncOpenAI
from langchain_openai import ChatOpenAI
try:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
except ImportError:
    from langchain_core.messages import HumanMessage, SystemMessage

    AIMessage = HumanMessage

from app.agent.graph import AgentState
from app.agent.tools import AGENT_TOOLS
from app.core import llm_factory as _llm_factory
from app.core.llm_provider import LLMProvider, get_llm_router

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
OUT_OF_SCOPE_RESPONSE = (
    "抱歉，我是专注于中国法律领域的智能助手，暂时无法回答该类问题。"
    "如果您有中国法律相关的疑问，例如劳动争议、合同纠纷、刑事案件等，我很乐意为您提供帮助。"
)
def create_local_router_llm() -> ChatOpenAI:
    """
    Surgery #12: 本地 vLLM 路由模型工厂。
    用于 router_llm，承担四轨意图分类任务。
    本地 Qwen2.5-3B-Instruct，OpenAI 兼容接口。
    """
    from app.core.config import settings

    return ChatOpenAI(
        model=settings.LLM_MODEL_NAME,
        api_key=settings.API_KEY,
        base_url=settings.BASE_URL,
        temperature=0.1,
        max_tokens=64,
        timeout=15,
    )


def create_cloud_llm(model: str = "glm-4-flash") -> "ChatZhipuAI":
    """
    Surgery #12: 智谱云端模型工厂。
    用于 expert_llm / summary_llm 等需要推理质量的任务。
    """
    from langchain_community.chat_models import ChatZhipuAI
    from app.core.config import settings

    return ChatZhipuAI(
        model=model,
        api_key=settings.ZHIPUAI_API_KEY,
        temperature=0.1,
    )


def _describe_llm_target(llm: Any) -> tuple[str, str]:
    model_name = str(
        getattr(llm, "model_name", None)
        or getattr(llm, "model", None)
        or ""
    ).strip()
    base_url = str(
        getattr(llm, "openai_api_base", None)
        or getattr(llm, "base_url", None)
        or ""
    ).strip()
    return model_name, base_url


def get_agent_llm_router(self):
    router = getattr(self, "llm_router", None)
    if router is None:
        router = get_llm_router()
        setattr(self, "llm_router", router)
    return router


def normalize_chat_messages(self, messages: list[dict[str, str]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for message in messages:
        role = str(message.get("role", "user")).lower()
        if role not in {"system", "assistant", "user"}:
            role = "user"
        content = message.get("content", "")
        normalized.append(
            {
                "role": role,
                "content": content if isinstance(content, str) else str(content),
            }
        )
    return normalized


def to_langchain_messages(self, messages: list[dict[str, str]]) -> list[Any]:
    langchain_messages = []
    for message in self._normalize_chat_messages(messages):
        role = message["role"]
        content = message["content"]
        if role == "system":
            langchain_messages.append(SystemMessage(content=content))
        elif role == "assistant":
            langchain_messages.append(AIMessage(content=content))
        else:
            langchain_messages.append(HumanMessage(content=content))
    return langchain_messages


async def call_langchain_llm(self, llm, messages: list[dict[str, str]]) -> str:
    response = await llm.ainvoke(self._to_langchain_messages(messages))
    content = response.content if hasattr(response, "content") else response
    return content if isinstance(content, str) else str(content)


async def call_zhipu(self, messages: list[dict[str, str]], llm=None) -> str:
    target_llm = llm or getattr(self, "expert_llm", None) or create_cloud_llm()
    model_name, base_url = _describe_llm_target(target_llm)
    logger.info(
        "LLM dispatch | provider=zhipu model=%s base_url=%s",
        model_name or "glm-4-flash",
        base_url or "https://open.bigmodel.cn/api/paas/v4",
    )
    return await self._call_langchain_llm(target_llm, messages)


async def call_local_vllm(self, messages: list[dict[str, str]]) -> str:
    local_llm = _llm_factory.create_local_llm(temperature=0.1, max_tokens=2048)
    model_name, base_url = _describe_llm_target(local_llm)
    logger.info(
        "LLM dispatch | provider=local-vllm model=%s base_url=%s",
        model_name or "unknown",
        base_url or "unknown",
    )
    return await self._call_langchain_llm(local_llm, messages)


async def call_openai(
    self,
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.3,
) -> str:
    llm_router = self._get_llm_router()
    return await self._call_openai_compatible(
        provider_name="openai",
        model=model,
        messages=messages,
        api_key=llm_router.openai_api_key,
        base_url=llm_router.openai_base_url,
        temperature=temperature,
    )


async def call_openai_compatible(
    self,
    *,
    provider_name: str,
    model: str,
    messages: list[dict[str, str]],
    api_key: str,
    base_url: str,
    temperature: float = 0.3,
) -> str:
    logger.info(
        "LLM dispatch | provider=%s model=%s base_url=%s",
        provider_name,
        model,
        base_url,
    )
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=60.0,
    )
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=self._normalize_chat_messages(messages),
            temperature=temperature,
            max_tokens=2048,
        )
        content = response.choices[0].message.content if response.choices else ""
        return content if isinstance(content, str) else str(content or "")
    finally:
        await client.close()


def _cloud_model_targets_for_model(self, primary_model: str) -> list[dict[str, str]]:
    llm_router = self._get_llm_router()
    high_quality = primary_model == "gpt-5.4"
    targets: list[dict[str, str]] = []
    openai_api_key = str(getattr(llm_router, "openai_api_key", "") or "")
    openai_base_url = str(getattr(llm_router, "openai_base_url", "") or "")
    alt_gpt_api_key = str(getattr(llm_router, "alt_gpt_api_key", "") or "")
    alt_gpt_base_url = str(getattr(llm_router, "alt_gpt_base_url", "") or "")
    deepseek_api_key = str(getattr(llm_router, "deepseek_api_key", "") or "")
    deepseek_base_url = str(getattr(llm_router, "deepseek_base_url", "") or "")

    if alt_gpt_api_key and alt_gpt_base_url:
        targets.append(
            {
                "provider": "alt-gpt",
                "model": (
                    str(getattr(llm_router, "alt_gpt_5_5_model", "") or "gpt-5.5")
                    if high_quality
                    else str(getattr(llm_router, "alt_gpt_5_2_model", "") or "gpt-5.2")
                ),
                "api_key": alt_gpt_api_key,
                "base_url": alt_gpt_base_url,
            }
        )

    if openai_api_key and openai_base_url:
        targets.append(
            {
                "provider": "openai",
                "model": primary_model,
                "api_key": openai_api_key,
                "base_url": openai_base_url,
            }
        )

    if deepseek_api_key and deepseek_base_url:
        targets.append(
            {
                "provider": "deepseek",
                "model": (
                    str(getattr(llm_router, "deepseek_v4_pro_model", "") or "deepseek-v4-pro")
                    if high_quality
                    else str(getattr(llm_router, "deepseek_v4_flash_model", "") or "deepseek-v4-flash")
                ),
                "api_key": deepseek_api_key,
                "base_url": deepseek_base_url,
            }
        )

    return targets


def _cloud_fallback_targets_for_model(self, primary_model: str) -> list[dict[str, str]]:
    return [
        target
        for target in self._cloud_model_targets_for_model(primary_model)
        if target.get("provider") != "openai"
    ]


async def call_cloud_model_chain(
    self,
    *,
    primary_model: str,
    messages: list[dict[str, str]],
    llm=None,
) -> str:
    for target in self._cloud_model_targets_for_model(primary_model):
        try:
            return await self._call_openai_compatible(
                provider_name=target["provider"],
                model=target["model"],
                messages=messages,
                api_key=target["api_key"],
                base_url=target["base_url"],
            )
        except Exception as exc:
            logger.warning(
                "Fallback provider failed | provider=%s model=%s error=%s",
                target["provider"],
                target["model"],
                exc,
            )

    logger.warning("Cloud model chain exhausted before ZHIPU, fallback to ZHIPU")
    return await self._call_zhipu(messages, llm=llm)


async def call_cloud_fallback_chain(
    self,
    *,
    primary_model: str,
    messages: list[dict[str, str]],
    llm=None,
) -> str:
    return await self._call_cloud_model_chain(
        primary_model=primary_model,
        messages=messages,
        llm=llm,
    )


async def chat_model(
    self,
    llm,
    messages: list[dict[str, str]],
    provider: LLMProvider = LLMProvider.ZHIPU,
) -> str:
    """Send messages to the configured provider and return the text response."""
    if provider == LLMProvider.GPT_5_4:
        return await self._call_cloud_model_chain(
            primary_model="gpt-5.4",
            messages=messages,
            llm=llm,
        )

    if provider == LLMProvider.GPT_5_1:
        return await self._call_cloud_model_chain(
            primary_model="gpt-5.1",
            messages=messages,
            llm=llm,
        )

    if provider == LLMProvider.LOCAL_VLLM:
        try:
            return await self._call_local_vllm(messages)
        except Exception as exc:
            logger.warning("Local vLLM failed, use cloud fallback chain before ZHIPU: %s", exc)
            return await self._call_cloud_model_chain(
                primary_model="gpt-5.1",
                messages=messages,
                llm=llm,
            )

    return await self._call_zhipu(messages, llm=llm)


async def chat_model_with_fallback(self, primary_llm, fallback_llm, messages: list[dict[str, str]]) -> str:
    """
    Surgery #12: 带 fallback 的 LLM 调用。
    primary_llm 失败时自动降级到 fallback_llm。
    """
    try:
        return await self._chat_model(primary_llm, messages)
    except Exception as exc:
        logger.warning("Primary LLM failed, falling back to cloud LLM: %s", exc)
        return await self._chat_model(fallback_llm, messages)


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
