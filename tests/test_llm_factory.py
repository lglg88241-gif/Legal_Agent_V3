"""Surgery #12: dual-model LLM factory tests."""
from __future__ import annotations

from unittest.mock import patch

from app.core.config import settings
from app.core.llm_factory import (
    create_cloud_llm,
    create_expert_llm,
    create_local_llm,
    create_router_llm,
)


class TestLLMFactory:
    """Validate factory selection between local vLLM and cloud ZhipuAI."""

    def test_create_local_llm_returns_chatopenai(self) -> None:
        llm = create_local_llm()
        assert type(llm).__name__ == "ChatOpenAI"

    def test_create_cloud_llm_returns_chatzhipuai(self) -> None:
        llm = create_cloud_llm()
        assert type(llm).__name__ == "ChatZhipuAI"

    def test_router_llm_uses_local_when_enabled(self) -> None:
        with patch.object(settings, "LOCAL_LLM_ENABLED", True):
            llm = create_router_llm()
        assert type(llm).__name__ == "ChatOpenAI"

    def test_router_llm_uses_cloud_when_disabled(self) -> None:
        with patch.object(settings, "LOCAL_LLM_ENABLED", False):
            llm = create_router_llm()
        assert type(llm).__name__ == "ChatZhipuAI"

    def test_expert_llm_always_cloud(self) -> None:
        llm = create_expert_llm()
        assert type(llm).__name__ == "ChatZhipuAI"
