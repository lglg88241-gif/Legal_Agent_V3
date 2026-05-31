"""Surgery #12: dual-model LLM factory tests."""
from __future__ import annotations

from unittest.mock import patch

from app.core.config import settings
from app.core.llm_provider import LLMProvider, LLMRouter
from app.core.llm_factory import (
    CloudProviderChainLLM,
    create_cloud_llm,
    create_cloud_fallback_llm,
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

    def test_router_keeps_gpt_scene_when_alt_fallback_is_configured(self) -> None:
        router = LLMRouter(
            {
                "OPENAI_API_KEY": "",
                "OPENAI_BASE_URL": "https://api.openai.com/v1",
                "ALT_GPT_API_KEY": "sk-alt",
                "ALT_GPT_BASE_URL": "https://alt-gpt.test/v1",
                "ZHIPU_API_KEY": "zhipu-test",
                "VLLM_BASE_URL": "http://127.0.0.1:8001",
            }
        )

        assert router.get_provider("advisor") == LLMProvider.GPT_5_4

    def test_router_uses_gpt_chain_for_query_expansion(self) -> None:
        router = LLMRouter(
            {
                "OPENAI_API_KEY": "",
                "OPENAI_BASE_URL": "https://api.openai.com/v1",
                "ALT_GPT_API_KEY": "sk-alt",
                "ALT_GPT_BASE_URL": "https://alt-gpt.test/v1",
                "ZHIPU_API_KEY": "zhipu-test",
                "VLLM_BASE_URL": "http://127.0.0.1:8001",
            }
        )

        assert router.get_provider("query_expansion") == LLMProvider.GPT_5_1

    def test_router_scene_uses_gpt_chain(self) -> None:
        router = LLMRouter(
            {
                "OPENAI_API_KEY": "",
                "OPENAI_BASE_URL": "https://api.openai.com/v1",
                "ALT_GPT_API_KEY": "sk-alt",
                "ALT_GPT_BASE_URL": "https://alt-gpt.test/v1",
                "ZHIPU_API_KEY": "zhipu-test",
                "VLLM_BASE_URL": "http://127.0.0.1:8001",
            }
        )

        assert router.get_provider("router") == LLMProvider.GPT_5_1

    def test_create_cloud_fallback_llm_prefers_alt_gpt_over_openai(self) -> None:
        with (
            patch.object(settings, "ALT_GPT_API_KEY", "sk-alt"),
            patch.object(settings, "ALT_GPT_BASE_URL", "https://alt-gpt.test/v1"),
            patch.object(settings, "ALT_GPT_5_2_MODEL", "gpt-5.2"),
            patch.object(settings, "OPENAI_API_KEY", "sk-openai"),
            patch.object(settings, "OPENAI_BASE_URL", "https://api.openai.test/v1"),
            patch.object(settings, "DEEPSEEK_API_KEY", "sk-deepseek"),
            patch.object(settings, "DEEPSEEK_BASE_URL", "https://deepseek.test/v1"),
            patch.object(settings, "DEEPSEEK_V4_FLASH_MODEL", "deepseek-v4-flash"),
            patch.object(settings, "ZHIPUAI_API_KEY", "zhipu-test"),
        ):
            llm = create_cloud_fallback_llm(primary_model="gpt-5.1")

        assert isinstance(llm, CloudProviderChainLLM)
        assert [target.model_name for target in llm.targets[:3]] == [
            "gpt-5.2",
            "gpt-5.1",
            "deepseek-v4-flash",
        ]
