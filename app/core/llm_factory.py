"""
Surgery #12: 双模型工厂 — 本地 vLLM + 云端智谱协作。

本地模型（OpenAI 兼容的 vLLM）用于：
  - router_llm: 意图路由分类
  - query_expansion_llm: 查询扩展 / 指代消解

云端模型（ChatZhipuAI）用于：
  - expert_llm: 法律回答生成
  - summary_llm: 案情摘要合并

降级策略：
  - 本地模型创建成功后，由调用方在关键路径上负责 fallback 到云端。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx
import requests
from json_repair import repair_json

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _ChatResponse:
    content: str


class ChatOpenAI:
    """Minimal OpenAI-compatible chat client for local vLLM."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        temperature: float = 0.1,
        max_tokens: int = 512,
        timeout: float = 60.0,
    ) -> None:
        self.model_name = model
        self.openai_api_key = api_key
        self.openai_api_base = base_url.rstrip("/")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

    def _chat_completions_url(self) -> str:
        return f"{self.openai_api_base}/chat/completions"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.openai_api_key:
            headers["Authorization"] = f"Bearer {self.openai_api_key}"
        return headers

    def _normalize_role(self, message: Any) -> str:
        if isinstance(message, dict):
            role = str(message.get("role", "user")).lower()
        else:
            role = str(getattr(message, "type", type(message).__name__.replace("Message", ""))).lower()

        if role in {"human", "user"}:
            return "user"
        if role in {"ai", "assistant"}:
            return "assistant"
        if role == "system":
            return "system"
        return "user"

    def _normalize_content(self, message: Any) -> str:
        if isinstance(message, dict):
            content = message.get("content", "")
        else:
            content = getattr(message, "content", "")
        return content if isinstance(content, str) else str(content)

    def _build_payload(self, messages: list[Any]) -> dict[str, Any]:
        return {
            "model": self.model_name,
            "messages": [
                {
                    "role": self._normalize_role(message),
                    "content": self._normalize_content(message),
                }
                for message in messages
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

    @staticmethod
    def _extract_content(data: dict[str, Any]) -> str:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError(f"Missing choices in vLLM response: {data}")

        message = choices[0].get("message", {})
        content = message.get("content", "")
        return content if isinstance(content, str) else str(content)

    def invoke(self, messages: list[Any]) -> _ChatResponse:
        response = requests.post(
            self._chat_completions_url(),
            headers=self._headers(),
            json=self._build_payload(messages),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return _ChatResponse(content=self._extract_content(response.json()))

    async def ainvoke(self, messages: list[Any]) -> _ChatResponse:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self._chat_completions_url(),
                headers=self._headers(),
                json=self._build_payload(messages),
            )
            response.raise_for_status()
        return _ChatResponse(content=self._extract_content(response.json()))


class ChatZhipuAI(ChatOpenAI):
    """Minimal ZhipuAI chat client using the public chat completions API."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        temperature: float = 0.1,
        max_tokens: int = 2048,
        timeout: float = 60.0,
        base_url: str = "https://open.bigmodel.cn/api/paas/v4",
    ) -> None:
        super().__init__(
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )


def _extract_expansion_array_payload(content: str) -> str:
    """Strip markdown fences and isolate the first JSON array payload."""
    cleaned = (content or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start != -1 and end != -1 and end > start:
        return cleaned[start : end + 1]
    return cleaned


def _coerce_expansion_queries(items: list[Any]) -> list[str]:
    queries: list[str] = []
    for item in items:
        if isinstance(item, str):
            queries.append(item)
            continue
        if isinstance(item, dict):
            value = item.get("query") or item.get("text") or item.get("content")
            if isinstance(value, str):
                queries.append(value)
    return queries


def _parse_expansion_queries(content: str) -> list[str] | None:
    """Best-effort parse of query-expansion JSON payload."""
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        parsed = json.loads(repair_json(_extract_expansion_array_payload(content)))
    except Exception:
        return None

    if isinstance(parsed, dict):
        queries = parsed.get("queries", [])
    elif isinstance(parsed, list):
        queries = parsed
    else:
        return None

    if not isinstance(queries, list):
        return None
    return _coerce_expansion_queries(queries)


def is_valid_expansion(content: str) -> bool:
    """A valid expansion must contain at least 2 non-empty strings."""
    queries = _parse_expansion_queries(content)
    if not queries or len(queries) < 2:
        return False
    return all(item.strip() for item in queries)


def _truncate_debug_response(content: str, limit: int = 500) -> str:
    """Keep raw local query-expansion logs short and safe to print."""
    text = (content or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "...<truncated>"


class QueryExpansionLLM:
    """Local-first query expansion wrapper with cloud fallback."""

    def __init__(
        self,
        local_llm: ChatOpenAI | None,
        cloud_llm: ChatZhipuAI,
        *,
        local_timeout: float = 5.0,
    ) -> None:
        self.local_llm = local_llm
        self.cloud_llm = cloud_llm
        self.local_timeout = local_timeout

    def invoke(self, messages: list[Any]) -> _ChatResponse:
        if self.local_llm is not None:
            try:
                local_response = self.local_llm.invoke(messages)
                if local_response:
                    logger.debug(
                        "Local query expansion raw response: %s",
                        _truncate_debug_response(local_response.content),
                    )
                if local_response and is_valid_expansion(local_response.content):
                    logger.info("Query expansion path: local")
                    return local_response
                logger.warning("Local query expansion returned invalid payload, fallback to cloud")
            except Exception as exc:
                logger.warning("Local query expansion failed, fallback to cloud: %s", exc)

        logger.info("Query expansion path: cloud_fallback")
        return self.cloud_llm.invoke(messages)

    async def ainvoke(self, messages: list[Any]) -> _ChatResponse:
        if self.local_llm is not None:
            try:
                local_response = await asyncio.wait_for(
                    self.local_llm.ainvoke(messages),
                    timeout=self.local_timeout,
                )
                if local_response:
                    logger.debug(
                        "Local query expansion raw response: %s",
                        _truncate_debug_response(local_response.content),
                    )
                if local_response and is_valid_expansion(local_response.content):
                    logger.info("Query expansion path: local")
                    return local_response
                logger.warning("Local query expansion returned invalid payload, fallback to cloud")
            except (asyncio.TimeoutError, Exception) as exc:
                logger.warning("Local query expansion failed, fallback to cloud: %s", exc)

        logger.info("Query expansion path: cloud_fallback")
        return await self.cloud_llm.ainvoke(messages)


def create_local_llm(
    temperature: float = 0.1,
    max_tokens: int = 512,
) -> ChatOpenAI:
    """Create a local OpenAI-compatible chat client for vLLM."""
    return ChatOpenAI(
        model=settings.LLM_MODEL_NAME,
        api_key=settings.API_KEY,
        base_url=settings.BASE_URL,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def create_cloud_llm(
    model: str = "glm-4-flash",
    temperature: float = 0.1,
    max_tokens: int = 2048,
) -> ChatZhipuAI:
    """Create a cloud ChatZhipuAI client."""
    return ChatZhipuAI(
        model=model,
        api_key=settings.ZHIPUAI_API_KEY,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def create_router_llm() -> ChatOpenAI | ChatZhipuAI:
    """Router LLM — prefer local vLLM, otherwise use cloud."""
    if settings.LOCAL_LLM_ENABLED:
        logger.info("Router LLM -> local vLLM (%s)", settings.LLM_MODEL_NAME)
        return create_local_llm(temperature=0.1, max_tokens=256)

    logger.info("Router LLM -> cloud ZhipuAI (LOCAL_LLM_ENABLED=false)")
    return create_cloud_llm(temperature=0.1, max_tokens=256)


def create_query_expansion_llm() -> QueryExpansionLLM:
    """Query expansion LLM: local-first with cloud fallback."""
    local_llm: ChatOpenAI | None = None

    if settings.LOCAL_LLM_ENABLED:
        try:
            local_llm = ChatOpenAI(
                model=settings.LLM_MODEL_NAME,
                api_key=settings.API_KEY,
                base_url=settings.BASE_URL,
                temperature=0.3,
                max_tokens=512,
                timeout=5.0,
            )
            logger.info(
                "Query expansion LLM -> local-first vLLM (%s)",
                settings.LLM_MODEL_NAME,
            )
        except Exception as exc:
            logger.warning(
                "Failed to initialize local query expansion LLM, use cloud fallback only: %s",
                exc,
            )

    cloud_llm = create_cloud_llm(temperature=0.3, max_tokens=512)
    if local_llm is None:
        logger.info("Query expansion LLM -> cloud-only fallback")
    return QueryExpansionLLM(
        local_llm=local_llm,
        cloud_llm=cloud_llm,
        local_timeout=5.0,
    )


def create_expert_llm(model: str = "glm-4-flash") -> ChatZhipuAI:
    """Expert LLM — always cloud for answer quality."""
    return create_cloud_llm(model=model, temperature=0.1, max_tokens=2048)


def create_summary_llm(model: str = "glm-4-flash") -> ChatZhipuAI:
    """Summary LLM — always cloud for case-summary merging."""
    return create_cloud_llm(model=model, temperature=0.1, max_tokens=1024)
