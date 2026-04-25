from __future__ import annotations

import importlib
import sys
from types import ModuleType

import pytest


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

    fake_graph_extractor.format_graph_memory = _format_graph_memory
    fake_graph_extractor.refresh_case_graph = _refresh_case_graph
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


def _load_celery_module():
    _install_celery_import_stubs()
    sys.modules.pop("app.celery_app", None)
    return importlib.import_module("app.celery_app")


def _run_ask_agent_task(
    monkeypatch: pytest.MonkeyPatch,
    *,
    persistence_consent: str,
    route_intent: str,
) -> tuple[dict[str, object], dict[str, object], list[tuple[int, int, int]]]:
    celery_module = _load_celery_module()
    persisted: dict[str, object] = {}
    graph_calls: list[tuple[int, int, int]] = []

    async def fake_run_task_async(conversation_id: int):
        return {
            "chat_history": "",
            "case_summary": "旧摘要",
            "graph_memory": "",
            "saved_case_state": {"monthly_salary": 12000},
            "persistence_consent": persistence_consent,
        }

    class FakeLegalAgent:
        async def ask(self, *args, **kwargs):
            return {
                "response": "分析结果",
                "sources": [],
                "route_intent": route_intent,
                "case_summary": "新摘要",
                "case_state_snapshot": {"monthly_salary": 15000},
                "case_type": "劳动争议",
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
        "我在公司工作两年，公司一直未签合同，公司通知我明天不用来了",
        True,
        True,
    )
    return result, persisted, graph_calls


def test_temporary_consent_skips_structured_case_persistence_for_case_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, persisted, graph_calls = _run_ask_agent_task(
        monkeypatch,
        persistence_consent="temporary",
        route_intent="CASE_UPDATE",
    )

    assert result["status"] == "success"
    assert result["route_intent"] == "CASE_UPDATE"
    assert persisted["user_content"] == "我在公司工作两年，公司一直未签合同，公司通知我明天不用来了"
    assert persisted["assistant_content"] == "分析结果"
    assert persisted["case_summary"] == ""
    assert persisted["case_state_dict"] is None
    assert persisted["case_type"] is None
    assert graph_calls == []


def test_tracked_consent_keeps_existing_case_update_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, persisted, graph_calls = _run_ask_agent_task(
        monkeypatch,
        persistence_consent="tracked",
        route_intent="CASE_UPDATE",
    )

    assert result["status"] == "success"
    assert result["route_intent"] == "CASE_UPDATE"
    assert persisted["case_summary"] == "新摘要"
    assert persisted["case_state_dict"] == {"monthly_salary": 15000}
    assert persisted["case_type"] == "劳动争议"
    assert graph_calls == [(9, 1, 101)]


def test_unknown_consent_preserves_legacy_case_update_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, persisted, graph_calls = _run_ask_agent_task(
        monkeypatch,
        persistence_consent="unknown",
        route_intent="CASE_UPDATE",
    )

    assert result["status"] == "success"
    assert result["route_intent"] == "CASE_UPDATE"
    assert persisted["case_summary"] == "新摘要"
    assert persisted["case_state_dict"] == {"monthly_salary": 15000}
    assert persisted["case_type"] == "劳动争议"
    assert graph_calls == [(9, 1, 101)]


def test_general_consult_never_persists_structured_case_or_graph_even_in_temporary_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, persisted, graph_calls = _run_ask_agent_task(
        monkeypatch,
        persistence_consent="temporary",
        route_intent="GENERAL_CONSULT",
    )

    assert result["status"] == "success"
    assert result["route_intent"] == "GENERAL_CONSULT"
    assert persisted["case_summary"] == ""
    assert persisted["case_state_dict"] is None
    assert persisted["case_type"] is None
    assert graph_calls == []
