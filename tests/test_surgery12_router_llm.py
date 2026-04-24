"""Surgery #12: 本地 router_llm 工厂和 fallback 机制测试。"""
from __future__ import annotations

import asyncio
import os
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("USE_TORCH", "0")

if "json_repair" not in sys.modules:
    fake_json_repair = ModuleType("json_repair")
    fake_json_repair.repair_json = lambda content: content
    sys.modules["json_repair"] = fake_json_repair

if "app.agent.tools" not in sys.modules:
    fake_tools = ModuleType("app.agent.tools")
    fake_tools.AGENT_TOOLS = [SimpleNamespace(name="search_law_articles")]
    sys.modules["app.agent.tools"] = fake_tools

_GRAPH_RELOAD_MODULES = (
    "app.services.graph_extractor",
    "app.agent.nodes.researcher",
    "app.services.legal_agent",
)


@pytest.fixture(autouse=True)
def _reset_graph_related_modules():
    for module_name in _GRAPH_RELOAD_MODULES:
        sys.modules.pop(module_name, None)


class TestLocalRouterLLMFactory:
    """create_local_router_llm 工厂函数测试。"""

    def test_create_local_router_llm_returns_chat_openai(self) -> None:
        """工厂函数应返回 ChatOpenAI 实例。"""
        from langchain_openai import ChatOpenAI

        from app.agent.nodes._llm import create_local_router_llm

        llm = create_local_router_llm()
        assert isinstance(llm, ChatOpenAI)

    def test_local_router_llm_uses_correct_base_url(self) -> None:
        """本地模型应使用 BASE_URL 配置。"""
        from app.agent.nodes._llm import create_local_router_llm
        from app.core.config import settings

        llm = create_local_router_llm()
        assert str(settings.BASE_URL) == str(llm.openai_api_base)
        assert "8001" in str(llm.openai_api_base) or "localhost" in str(llm.openai_api_base)

    def test_local_router_llm_max_tokens_is_small(self) -> None:
        """路由模型只需要短输出，max_tokens 应 <= 64。"""
        from app.agent.nodes._llm import create_local_router_llm

        llm = create_local_router_llm()
        assert llm.max_tokens <= 64

    def test_create_cloud_llm_returns_zhipu(self) -> None:
        """cloud LLM 工厂应返回 ChatZhipuAI 实例。"""
        from langchain_community.chat_models import ChatZhipuAI

        from app.agent.nodes._llm import create_cloud_llm

        llm = create_cloud_llm()
        assert isinstance(llm, ChatZhipuAI)


class TestChatModelWithFallback:
    """chat_model_with_fallback 方法测试。"""

    def test_fallback_not_called_when_primary_succeeds(self) -> None:
        """primary 成功时不调用 fallback。"""
        from app.services.legal_agent import LegalGraphAgent

        agent = LegalGraphAgent.__new__(LegalGraphAgent)
        primary = MagicMock()
        fallback = MagicMock()

        async def mock_chat(llm, messages):
            if llm is primary:
                return "GENERAL_CONSULT"
            return "FALLBACK"

        agent._chat_model = mock_chat
        result = asyncio.run(agent._chat_model_with_fallback(primary, fallback, []))
        assert result == "GENERAL_CONSULT"

    def test_fallback_called_when_primary_fails(self) -> None:
        """primary 失败时自动调用 fallback。"""
        from app.services.legal_agent import LegalGraphAgent

        agent = LegalGraphAgent.__new__(LegalGraphAgent)
        primary = MagicMock()
        fallback = MagicMock()

        async def mock_chat(llm, messages):
            if llm is primary:
                raise ConnectionError("vLLM unreachable")
            return "FALLBACK_RESULT"

        agent._chat_model = mock_chat
        result = asyncio.run(agent._chat_model_with_fallback(primary, fallback, []))
        assert result == "FALLBACK_RESULT"
