from __future__ import annotations

import importlib
import sys
from types import ModuleType

import pytest

from app.agent.graph import route_after_tool_router, route_after_vector_researcher
from app.services.case_followup import (
    is_case_fact_update_message,
    is_dossier_followup_question,
)


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

    fake_redis_mgr = ModuleType("app.memory.redis_mgr")
    fake_redis_mgr.get_redis_saver_for_current_loop = lambda: None
    sys.modules["app.memory.redis_mgr"] = fake_redis_mgr


def _load_celery_module():
    _install_celery_import_stubs()
    sys.modules.pop("app.celery_app", None)
    return importlib.import_module("app.celery_app")


def test_dossier_followup_question_is_not_treated_as_fact_update() -> None:
    question = "结合这个案卷，我接下来申请劳动仲裁时应该准备哪些证据？"
    assert is_dossier_followup_question(question) is True
    assert is_case_fact_update_message(question) is False


def test_case_fact_update_message_still_detects_real_fact_supplement() -> None:
    message = "我补充一下，公司今天又通知我下周不用来了，实际入职时间是 2022 年 3 月。"
    assert is_dossier_followup_question(message) is False
    assert is_case_fact_update_message(message) is True


def test_dossier_followup_question_skips_fact_updater_and_reflector_routes() -> None:
    state = {
        "route_intent": "CASE_UPDATE",
        "question": "结合这个案卷，我接下来申请劳动仲裁时应该准备哪些证据？",
        "dossier_context": {"id": 11, "title": "劳动争议案卷"},
    }

    assert route_after_tool_router(state) == "vector_researcher"
    assert route_after_vector_researcher(state) == "legal_advisor"


def test_real_fact_update_keeps_fact_updater_route_even_with_dossier_context() -> None:
    state = {
        "route_intent": "CASE_UPDATE",
        "question": "我补充一下，公司今天又通知我下周不用来了。",
        "dossier_context": {"id": 11, "title": "劳动争议案卷"},
    }

    assert route_after_tool_router(state) == "fact_updater"
    assert route_after_vector_researcher(state) == "reflector"


def _run_ask_agent_task(
    monkeypatch: pytest.MonkeyPatch,
    *,
    question: str,
    persistence_consent: str,
) -> tuple[dict[str, object], dict[str, object], list[tuple[int, int, int]]]:
    celery_module = _load_celery_module()
    persisted: dict[str, object] = {}
    graph_calls: list[tuple[int, int, int]] = []

    async def fake_run_task_async(conversation_id: int, user_id: int):
        return {
            "chat_history": "",
            "case_summary": "旧摘要",
            "graph_memory": "",
            "saved_case_state": {"monthly_salary": 12000},
            "persistence_consent": persistence_consent,
            "dossier_context": {"id": 7, "title": "劳动争议案卷"},
        }

    class FakeLegalAgent:
        async def ask(self, *args, **kwargs):
            return {
                "response": "分析结果",
                "sources": [],
                "route_intent": "CASE_UPDATE",
                "case_summary": "新摘要",
                "case_state_snapshot": {"monthly_salary": 15000, "has_unsigned_contract": True},
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
        question,
        True,
        True,
    )
    return result, persisted, graph_calls


def test_tracked_dossier_followup_question_does_not_persist_structured_case_or_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, persisted, graph_calls = _run_ask_agent_task(
        monkeypatch,
        question="结合这个案卷，我接下来申请劳动仲裁时应该准备哪些证据？",
        persistence_consent="tracked",
    )

    assert result["status"] == "success"
    assert result["route_intent"] == "GENERAL_CONSULT"
    assert persisted["case_summary"] == ""
    assert persisted["case_state_dict"] is None
    assert persisted["case_type"] is None
    assert graph_calls == []


def test_tracked_dossier_fact_update_still_persists_structured_case_and_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, persisted, graph_calls = _run_ask_agent_task(
        monkeypatch,
        question="我补充一下，公司今天又通知我下周不用来了，工资其实是 12000。",
        persistence_consent="tracked",
    )

    assert result["status"] == "success"
    assert result["route_intent"] == "CASE_UPDATE"
    assert persisted["case_summary"] == "新摘要"
    assert persisted["case_state_dict"] == {"monthly_salary": 15000, "has_unsigned_contract": True}
    assert persisted["case_type"] == "劳动争议"
    assert graph_calls == [(9, 1, 101)]
