from __future__ import annotations

import asyncio
import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest


def _install_router_import_stubs() -> None:
    if "json_repair" not in sys.modules:
        fake_json_repair = ModuleType("json_repair")
        fake_json_repair.repair_json = lambda content: content
        sys.modules["json_repair"] = fake_json_repair

    if "app.agent.tools" not in sys.modules:
        fake_tools = ModuleType("app.agent.tools")
        fake_tools.AGENT_TOOLS = [
            SimpleNamespace(name="search_law_articles"),
            SimpleNamespace(name="search_case_text"),
            SimpleNamespace(name="query_neo4j_graph"),
        ]
        sys.modules["app.agent.tools"] = fake_tools


def _install_celery_import_stubs() -> None:
    fake_openai = ModuleType("openai")

    class FakeAsyncOpenAI:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def close(self) -> None:
            return None

    fake_openai.AsyncOpenAI = FakeAsyncOpenAI
    sys.modules["openai"] = fake_openai

    fake_graph_extractor = ModuleType("app.services.graph_extractor")

    async def _format_graph_memory(*args, **kwargs) -> str:
        return ""

    async def _refresh_case_graph(*args, **kwargs):
        return {"actions_applied": 0, "actions": []}

    async def _delete_case_graph(*args, **kwargs):
        return {"success": True, "error": None}

    async def _get_case_graph(*args, **kwargs):
        return {}

    async def _get_cross_case_entities(*args, **kwargs):
        return []

    async def _get_user_cases_graph_overview(*args, **kwargs):
        return []

    fake_graph_extractor.format_graph_memory = _format_graph_memory
    fake_graph_extractor.refresh_case_graph = _refresh_case_graph
    fake_graph_extractor.delete_case_graph = _delete_case_graph
    fake_graph_extractor.get_case_graph = _get_case_graph
    fake_graph_extractor.get_cross_case_entities = _get_cross_case_entities
    fake_graph_extractor.get_user_cases_graph_overview = _get_user_cases_graph_overview
    sys.modules["app.services.graph_extractor"] = fake_graph_extractor

    fake_legal_agent = ModuleType("app.services.legal_agent")

    class FakeLegalAgent:
        async def ask(self, *args, **kwargs):
            return {"response": "ok", "sources": [], "route_intent": "GENERAL_CONSULT"}

    fake_legal_agent.legal_agent = FakeLegalAgent()
    sys.modules["app.services.legal_agent"] = fake_legal_agent

    fake_redis_mgr = ModuleType("app.memory.redis_mgr")
    fake_redis_mgr.get_redis_saver_for_current_loop = lambda: None
    sys.modules["app.memory.redis_mgr"] = fake_redis_mgr


def _load_router_module():
    _install_router_import_stubs()
    sys.modules.pop("app.agent.nodes.router", None)
    return importlib.import_module("app.agent.nodes.router")


def _load_celery_module():
    _install_celery_import_stubs()
    sys.modules.pop("app.celery_app", None)
    return importlib.import_module("app.celery_app")


def test_pure_law_reference_question_stays_general_consult_in_precise_mode() -> None:
    router_module = _load_router_module()

    class DummyAgent:
        def __init__(self) -> None:
            self.router_llm = object()

        def _safe_json_load(self, content, fallback):
            return {"route_intent": "CASE_UPDATE"}

        async def _chat_model(self, llm, messages, provider=None):
            return '{"route_intent":"CASE_UPDATE"}'

        _looks_like_chitchat = lambda self, q, chat_history="": False
        _has_substantive_legal_intent = lambda self, q: router_module.has_substantive_legal_intent(self, q)
        _history_indicates_legal_dialogue = (
            lambda self, chat_history: router_module.history_indicates_legal_dialogue(self, chat_history)
        )
        _is_contextual_followup = (
            lambda self, q, chat_history="": router_module.is_contextual_followup(self, q, chat_history)
        )
        _patched_is_case_update = (
            lambda self, q, chat_history="", case_summary="": router_module.is_case_update(
                self, q, chat_history, case_summary
            )
        )
        _patched_is_general_consult = (
            lambda self, q, case_summary="": router_module.is_general_consult(self, q, case_summary)
        )
        _detect_intents = lambda self, q, chat_history="": router_module.detect_intents(self, q, chat_history)
        _fallback_route_decision = (
            lambda self, q, chat_history="": router_module.fallback_route_decision(self, q, chat_history)
        )
        _dedupe_tool_calls = lambda self, calls: router_module.dedupe_tool_calls(self, calls)
        _enforce_composite_routes = (
            lambda self, q, tool_calls: router_module.enforce_composite_routes(self, q, tool_calls)
        )
        _patched_build_tool_calls_for_intent = (
            lambda self, route_intent, query: router_module.build_tool_calls_for_intent(self, route_intent, query)
        )

    result = asyncio.run(
        router_module.route_decision(
            DummyAgent(),
            "\u52b3\u52a8\u5408\u540c\u6cd5\u7b2c\u516b\u5341\u4e03\u6761\u89c4\u5b9a\u4e86\u4ec0\u4e48\uff1f",
            chat_history="\u6211\u5de5\u4f5c\u4e94\u5e74\u88ab\u8f9e\u9000",
            case_summary="\u52b3\u52a8\u4e89\u8bae\uff0c\u8fdd\u6cd5\u89e3\u9664",
            is_precise_mode=True,
        )
    )

    assert result["route_intent"] == "GENERAL_CONSULT"
    assert result["tool_calls"] == [
        {
            "name": "search_law_articles",
            "arguments": {
                "query": "\u52b3\u52a8\u5408\u540c\u6cd5\u7b2c\u516b\u5341\u4e03\u6761\u89c4\u5b9a\u4e86\u4ec0\u4e48\uff1f"
            },
        }
    ]


def test_build_tool_calls_for_pure_law_reference_uses_law_only() -> None:
    router_module = _load_router_module()

    class DummyAgent:
        _looks_like_chitchat = lambda self, q, chat_history="": False
        _has_substantive_legal_intent = lambda self, q: router_module.has_substantive_legal_intent(self, q)
        _history_indicates_legal_dialogue = (
            lambda self, chat_history: router_module.history_indicates_legal_dialogue(self, chat_history)
        )
        _is_contextual_followup = (
            lambda self, q, chat_history="": router_module.is_contextual_followup(self, q, chat_history)
        )
        _patched_is_case_update = (
            lambda self, q, chat_history="", case_summary="": router_module.is_case_update(
                self, q, chat_history, case_summary
            )
        )
        _detect_intents = lambda self, q, chat_history="": router_module.detect_intents(self, q, chat_history)
        _fallback_route_decision = (
            lambda self, q, chat_history="": router_module.fallback_route_decision(self, q, chat_history)
        )
        _dedupe_tool_calls = lambda self, calls: router_module.dedupe_tool_calls(self, calls)

    tool_calls = router_module.build_tool_calls_for_intent(
        DummyAgent(),
        "GENERAL_CONSULT",
        "\u52b3\u52a8\u5408\u540c\u6cd5\u7b2c\u516b\u5341\u4e03\u6761\u89c4\u5b9a\u4e86\u4ec0\u4e48\uff1f",
    )

    assert tool_calls == [
        {
            "name": "search_law_articles",
            "arguments": {
                "query": "\u52b3\u52a8\u5408\u540c\u6cd5\u7b2c\u516b\u5341\u4e03\u6761\u89c4\u5b9a\u4e86\u4ec0\u4e48\uff1f"
            },
        }
    ]


def test_ask_agent_task_skips_case_state_persistence_for_general_consult(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    celery_module = _load_celery_module()
    persisted: dict[str, object] = {}
    graph_calls: list[tuple[int, int, int]] = []

    async def fake_run_task_async(conversation_id: int, user_id: int):
        return {
            "chat_history": "",
            "case_summary": "\u65e7\u6458\u8981",
            "graph_memory": "",
            "saved_case_state": {"monthly_salary": 12000},
        }

    class FakeLegalAgent:
        async def ask(self, *args, **kwargs):
            return {
                "response": "ok",
                "sources": [],
                "route_intent": "GENERAL_CONSULT",
                "case_summary": "\u4e0d\u5e94\u6301\u4e45\u5316",
                "case_state_snapshot": {"monthly_salary": 15000},
                "case_type": "\u52b3\u52a8\u4e89\u8bae",
            }

    async def fake_persist_dialog_turn_async(**kwargs):
        persisted.update(kwargs)
        return {"user_message_id": 101, "assistant_message_id": 102}

    class FakeGraphTask:
        @staticmethod
        def delay(conversation_id: int, user_id: int, message_id: int) -> None:
            graph_calls.append((conversation_id, user_id, message_id))

    monkeypatch.setattr(celery_module, "_run_task_async", fake_run_task_async)
    monkeypatch.setattr(celery_module, "legal_agent", FakeLegalAgent())
    monkeypatch.setattr(celery_module, "_persist_dialog_turn_async", fake_persist_dialog_turn_async)
    monkeypatch.setattr(celery_module, "update_graph_task", FakeGraphTask())

    result = celery_module.ask_agent_task.run(
        1,
        9,
        "session-1",
        "\u52b3\u52a8\u5408\u540c\u6cd5\u7b2c\u516b\u5341\u4e03\u6761\u89c4\u5b9a\u4e86\u4ec0\u4e48\uff1f",
        True,
        True,
    )

    assert result["status"] == "success"
    assert result["route_intent"] == "GENERAL_CONSULT"
    assert persisted["case_summary"] == ""
    assert persisted["case_state_dict"] is None
    assert persisted["case_type"] is None
    assert graph_calls == []


def test_sync_check_primary_llm_config_does_not_require_local_vllm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main_module = importlib.import_module("main")
    llm_provider_module = importlib.import_module("app.core.llm_provider")

    class FakeRouter:
        openai_api_key = "sk-test"
        openai_base_url = "https://api.openai.com/v1"
        zhipu_api_key = "zhipu-test"
        vllm_base_url = "http://127.0.0.1:8001"
        local_enabled = False

        def get_provider(self, scene: str):
            mapping = {
                "advisor": llm_provider_module.LLMProvider.GPT_5_4,
                "fact_updater": llm_provider_module.LLMProvider.GPT_5_1,
                "router": llm_provider_module.LLMProvider.ZHIPU,
                "query_expansion": llm_provider_module.LLMProvider.ZHIPU,
            }
            return mapping[scene]

    monkeypatch.setattr(main_module, "get_llm_router", lambda: FakeRouter())

    main_module.sync_check_primary_llm_config()
