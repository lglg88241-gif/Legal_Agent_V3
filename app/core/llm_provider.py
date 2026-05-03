from __future__ import annotations

import logging
from enum import Enum
from functools import lru_cache
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class LLMProvider(str, Enum):
    GPT_5_4 = "gpt-5.4"
    GPT_5_1 = "gpt-5.1"
    ZHIPU = "zhipu"
    LOCAL_VLLM = "local-vllm"


SCENE_PROVIDER_MAP: dict[str, LLMProvider] = {
    "advisor": LLMProvider.GPT_5_4,
    "fact_updater": LLMProvider.GPT_5_1,
    "reflector": LLMProvider.GPT_5_1,
    "query_expansion": LLMProvider.ZHIPU,
    "graph_updater": LLMProvider.GPT_5_1,
    "router": LLMProvider.ZHIPU,
}

LOCAL_ELIGIBLE_SCENES = {"query_expansion", "router"}


class LLMRouter:
    def __init__(self, config: dict[str, str]) -> None:
        self.openai_api_key: str = config.get("OPENAI_API_KEY", "")
        self.openai_base_url: str = config.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.zhipu_api_key: str = config.get("ZHIPU_API_KEY", "")
        self.vllm_base_url: str = config.get("VLLM_BASE_URL", "http://127.0.0.1:8001").rstrip("/")
        self.local_enabled: bool = False
        self._vllm_healthy: Optional[bool] = None

    def get_provider(self, scene: str) -> LLMProvider:
        if self.local_enabled and scene in LOCAL_ELIGIBLE_SCENES:
            if self._vllm_healthy:
                return LLMProvider.LOCAL_VLLM
            logger.warning("vLLM unhealthy, fallback to cloud for scene=%s", scene)

        provider = SCENE_PROVIDER_MAP.get(scene, LLMProvider.ZHIPU)
        if provider in (LLMProvider.GPT_5_4, LLMProvider.GPT_5_1) and not self.openai_api_key:
            logger.warning("OPENAI_API_KEY not set, fallback to ZHIPU for scene=%s", scene)
            return LLMProvider.ZHIPU
        return provider

    async def check_vllm_health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"{self.vllm_base_url}/health")
                self._vllm_healthy = response.status_code == 200
        except Exception:
            self._vllm_healthy = False
        return bool(self._vllm_healthy)

    def set_local_enabled(self, enabled: bool) -> None:
        self.local_enabled = enabled
        if not enabled:
            self._vllm_healthy = None


def _build_config() -> dict[str, str]:
    return {
        "OPENAI_API_KEY": settings.OPENAI_API_KEY,
        "OPENAI_BASE_URL": settings.OPENAI_BASE_URL,
        "ZHIPU_API_KEY": settings.ZHIPUAI_API_KEY,
        "VLLM_BASE_URL": settings.VLLM_BASE_URL,
    }


@lru_cache(maxsize=1)
def get_llm_router() -> LLMRouter:
    return LLMRouter(_build_config())
