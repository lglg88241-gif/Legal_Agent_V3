"""Surgery #14: Neo4j 用户维度图谱隔离测试。
这些测试验证纯 Python 层的逻辑，不需要真实 Neo4j 连接。
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

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

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GRAPH_EXTRACTOR_SOURCE = (
    PROJECT_ROOT / "app" / "services" / "graph_extractor.py"
).read_text(encoding="utf-8")

_GRAPH_RELOAD_MODULES = (
    "app.services.graph_extractor",
    "app.api.graph",
    "app.agent.nodes.researcher",
    "app.services.legal_agent",
)


@pytest.fixture(autouse=True)
def _reset_graph_related_modules():
    for module_name in _GRAPH_RELOAD_MODULES:
        sys.modules.pop(module_name, None)


def _load_graph_router():
    fake_graph_extractor = ModuleType("app.services.graph_extractor")

    async def _async_empty(*args, **kwargs):
        return []

    async def _async_graph(*args, **kwargs):
        return {}

    fake_graph_extractor.get_user_cases_graph_overview = _async_empty
    fake_graph_extractor.get_cross_case_entities = _async_empty
    fake_graph_extractor.get_case_graph = _async_graph
    sys.modules["app.services.graph_extractor"] = fake_graph_extractor
    module = importlib.import_module("app.api.graph")
    return module.router


class TestGraphUserIsolation:
    """验证 user_id 在图谱操作中的传递。"""

    def test_merge_fact_accepts_user_id(self) -> None:
        """_merge_fact 应该接受 user_id 参数。"""
        assert "def _merge_fact(session, conversation_id: int, fact: dict[str, str], user_id: int = 0)" in GRAPH_EXTRACTOR_SOURCE

    def test_merge_fact_user_id_default_zero(self) -> None:
        """_merge_fact 的 user_id 默认值应为 0。"""
        assert "user_id: int = 0" in GRAPH_EXTRACTOR_SOURCE

    def test_execute_graph_actions_sync_accepts_user_id(self) -> None:
        """_execute_graph_actions_sync 应该接受 user_id 参数。"""
        assert "def _execute_graph_actions_sync(" in GRAPH_EXTRACTOR_SOURCE
        assert "user_id: int = 0" in GRAPH_EXTRACTOR_SOURCE

    def test_refresh_case_graph_accepts_user_id(self) -> None:
        """refresh_case_graph 应该接受 user_id 参数。"""
        assert "async def refresh_case_graph(" in GRAPH_EXTRACTOR_SOURCE
        assert "user_id: int = 0" in GRAPH_EXTRACTOR_SOURCE

    def test_upsert_case_graph_accepts_user_id(self) -> None:
        """upsert_case_graph 应该接受 user_id 参数。"""
        assert "async def upsert_case_graph(conversation_id: int, triples: list[dict[str, str]], user_id: int = 0)" in GRAPH_EXTRACTOR_SOURCE

    def test_execute_graph_actions_accepts_user_id(self) -> None:
        """execute_graph_actions 应该接受 user_id 参数。"""
        assert "async def execute_graph_actions(" in GRAPH_EXTRACTOR_SOURCE
        assert "user_id: int = 0" in GRAPH_EXTRACTOR_SOURCE


class TestUserGraphQueries:
    """验证用户图谱查询函数存在且签名正确。"""

    def test_get_user_cases_graph_overview_exists(self) -> None:
        """get_user_cases_graph_overview 函数应该存在。"""
        assert "async def get_user_cases_graph_overview(user_id: int) -> list[dict[str, Any]]:" in GRAPH_EXTRACTOR_SOURCE

    def test_get_cross_case_entities_exists(self) -> None:
        """get_cross_case_entities 函数应该存在。"""
        assert "async def get_cross_case_entities(user_id: int) -> list[dict[str, Any]]:" in GRAPH_EXTRACTOR_SOURCE

    def test_user_cases_query_constant_exists(self) -> None:
        """FETCH_USER_CASES_QUERY 常量应该存在且包含 user_id。"""
        assert "FETCH_USER_CASES_QUERY = \"\"\"" in GRAPH_EXTRACTOR_SOURCE
        assert "$user_id" in GRAPH_EXTRACTOR_SOURCE

    def test_cross_case_query_constant_exists(self) -> None:
        """FETCH_CROSS_CASE_ENTITIES_QUERY 常量应该存在。"""
        assert "FETCH_CROSS_CASE_ENTITIES_QUERY = \"\"\"" in GRAPH_EXTRACTOR_SOURCE
        assert "$user_id" in GRAPH_EXTRACTOR_SOURCE


class TestUserGraphAPI:
    """验证用户图谱 API 端点存在。"""

    def test_user_cases_route_registered(self) -> None:
        """GET /graph/user/{user_id}/cases 路由应该存在。"""
        router = _load_graph_router()

        paths = [route.path for route in router.routes]
        assert any("/user/{user_id}/cases" in path for path in paths), (
            f"未找到 user cases 路由，现有路由: {paths}"
        )

    def test_cross_case_entities_route_registered(self) -> None:
        """GET /graph/user/{user_id}/cross-case-entities 路由应该存在。"""
        router = _load_graph_router()

        paths = [route.path for route in router.routes]
        assert any("cross-case-entities" in path for path in paths), (
            f"未找到 cross-case-entities 路由，现有路由: {paths}"
        )
