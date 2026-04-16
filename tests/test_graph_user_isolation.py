"""Surgery #14: Neo4j 用户维度图谱隔离测试。
这些测试验证纯 Python 层的逻辑，不需要真实 Neo4j 连接。
"""
from __future__ import annotations

import os
import sys
from types import ModuleType, SimpleNamespace

os.environ.setdefault("USE_TORCH", "0")

if "json_repair" not in sys.modules:
    fake_json_repair = ModuleType("json_repair")
    fake_json_repair.repair_json = lambda content: content
    sys.modules["json_repair"] = fake_json_repair

if "app.agent.tools" not in sys.modules:
    fake_tools = ModuleType("app.agent.tools")
    fake_tools.AGENT_TOOLS = [SimpleNamespace(name="search_law_articles")]
    sys.modules["app.agent.tools"] = fake_tools


class TestGraphUserIsolation:
    """验证 user_id 在图谱操作中的传递。"""

    def test_merge_fact_accepts_user_id(self) -> None:
        """_merge_fact 应该接受 user_id 参数。"""
        from app.services.graph_extractor import _merge_fact
        import inspect

        sig = inspect.signature(_merge_fact)
        assert "user_id" in sig.parameters, "_merge_fact 缺少 user_id 参数"

    def test_merge_fact_user_id_default_zero(self) -> None:
        """_merge_fact 的 user_id 默认值应为 0。"""
        from app.services.graph_extractor import _merge_fact
        import inspect

        sig = inspect.signature(_merge_fact)
        param = sig.parameters["user_id"]
        assert param.default == 0, f"user_id 默认值应为 0，实际是 {param.default}"

    def test_execute_graph_actions_sync_accepts_user_id(self) -> None:
        """_execute_graph_actions_sync 应该接受 user_id 参数。"""
        from app.services.graph_extractor import _execute_graph_actions_sync
        import inspect

        sig = inspect.signature(_execute_graph_actions_sync)
        assert "user_id" in sig.parameters

    def test_refresh_case_graph_accepts_user_id(self) -> None:
        """refresh_case_graph 应该接受 user_id 参数。"""
        from app.services.graph_extractor import refresh_case_graph
        import inspect

        sig = inspect.signature(refresh_case_graph)
        assert "user_id" in sig.parameters

    def test_upsert_case_graph_accepts_user_id(self) -> None:
        """upsert_case_graph 应该接受 user_id 参数。"""
        from app.services.graph_extractor import upsert_case_graph
        import inspect

        sig = inspect.signature(upsert_case_graph)
        assert "user_id" in sig.parameters

    def test_execute_graph_actions_accepts_user_id(self) -> None:
        """execute_graph_actions 应该接受 user_id 参数。"""
        from app.services.graph_extractor import execute_graph_actions
        import inspect

        sig = inspect.signature(execute_graph_actions)
        assert "user_id" in sig.parameters


class TestUserGraphQueries:
    """验证用户图谱查询函数存在且签名正确。"""

    def test_get_user_cases_graph_overview_exists(self) -> None:
        """get_user_cases_graph_overview 函数应该存在。"""
        from app.services.graph_extractor import get_user_cases_graph_overview
        import inspect

        assert inspect.iscoroutinefunction(get_user_cases_graph_overview)

    def test_get_cross_case_entities_exists(self) -> None:
        """get_cross_case_entities 函数应该存在。"""
        from app.services.graph_extractor import get_cross_case_entities
        import inspect

        assert inspect.iscoroutinefunction(get_cross_case_entities)

    def test_user_cases_query_constant_exists(self) -> None:
        """FETCH_USER_CASES_QUERY 常量应该存在且包含 user_id。"""
        from app.services.graph_extractor import FETCH_USER_CASES_QUERY

        assert "$user_id" in FETCH_USER_CASES_QUERY

    def test_cross_case_query_constant_exists(self) -> None:
        """FETCH_CROSS_CASE_ENTITIES_QUERY 常量应该存在。"""
        from app.services.graph_extractor import FETCH_CROSS_CASE_ENTITIES_QUERY

        assert "$user_id" in FETCH_CROSS_CASE_ENTITIES_QUERY


class TestUserGraphAPI:
    """验证用户图谱 API 端点存在。"""

    def test_user_cases_route_registered(self) -> None:
        """GET /graph/user/{user_id}/cases 路由应该存在。"""
        from app.api.graph import router

        paths = [route.path for route in router.routes]
        assert any("/user/{user_id}/cases" in path for path in paths), (
            f"未找到 user cases 路由，现有路由: {paths}"
        )

    def test_cross_case_entities_route_registered(self) -> None:
        """GET /graph/user/{user_id}/cross-case-entities 路由应该存在。"""
        from app.api.graph import router

        paths = [route.path for route in router.routes]
        assert any("cross-case-entities" in path for path in paths), (
            f"未找到 cross-case-entities 路由，现有路由: {paths}"
        )
