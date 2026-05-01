from __future__ import annotations

import importlib
import sys
from types import ModuleType
from typing import Any

import pytest


FACT_MESSAGE = "我补充一下，人事让我先签确认书再谈补缴社保。"


def _fact_update_result() -> dict[str, object]:
    return {
        "source": "message_understanding_agent",
        "intent": "fact_update",
        "route_hint": "CASE_UPDATE",
        "needs_legal_rag": False,
        "needs_dossier_graph_context": False,
        "needs_structured_persistence": True,
        "needs_graph_update": True,
        "subject_reference": None,
        "fact_update": {
            "enabled": True,
            "fact_text": FACT_MESSAGE,
            "entities": [],
            "events": [],
        },
        "previous_fact_confirmation": None,
        "advice_question": None,
        "legal_question": None,
        "confidence": "high",
        "reason": "Current message adds case facts.",
        "provider": "openai",
        "model": "gpt-5.1",
        "fallback_reason": None,
        "trace": {"steps": ["call_llm"], "warnings": []},
    }


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


def test_temporary_fact_update_runs_legal_analysis_without_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    celery_module = _load_celery_module()
    persisted: dict[str, Any] = {}
    graph_calls: list[tuple[int, int, int]] = []
    trackers = {"legal_agent_calls": 0, "ack_calls": 0}

    async def fake_run_task_async(conversation_id: int, user_id: int, current_question: str | None = None):
        return {
            "chat_history": "",
            "latest_user_input": current_question,
            "recent_messages": [],
            "case_summary": "旧摘要",
            "graph_memory": "",
            "saved_case_state": None,
            "persistence_consent": "temporary",
            "conversation_mode": "TRACKED_CASE",
            "dossier_id": 12,
            "dossier_context": {"id": 12, "title": "劳动争议案卷"},
            "dossier_graph_summary": "",
            "dossier_graph_context": None,
        }

    class FakeLegalAgent:
        async def ask(self, *args, **kwargs):
            trackers["legal_agent_calls"] += 1
            return {
                "response": "可以先固定这条事实，再准备确认书、社保记录和聊天记录。",
                "sources": [],
                "route_intent": "GENERAL_CONSULT",
                "case_summary": "",
                "confidence_score": 0.77,
            }

    async def fake_understand_message_with_agent(**kwargs):
        return dict(_fact_update_result())

    async def fake_persist_dialog_turn_async(**kwargs):
        persisted.update(kwargs)
        return {"user_message_id": 101, "assistant_message_id": 102}

    class FakeGraphTask:
        @staticmethod
        def delay(conversation_id: int, user_id: int, message_id: int):
            graph_calls.append((conversation_id, user_id, message_id))
            return None

    def fail_ack(**kwargs):
        trackers["ack_calls"] += 1
        raise AssertionError("temporary case analysis should not call acknowledgement specialist")

    monkeypatch.setattr(celery_module, "_run_task_async", fake_run_task_async)
    monkeypatch.setattr(celery_module, "legal_agent", FakeLegalAgent())
    monkeypatch.setattr(celery_module, "understand_message_with_agent", fake_understand_message_with_agent)
    monkeypatch.setattr(celery_module, "_persist_dialog_turn_async", fake_persist_dialog_turn_async)
    monkeypatch.setattr(celery_module, "update_graph_task", FakeGraphTask())
    monkeypatch.setattr(celery_module, "build_case_update_acknowledgement", fail_ack)

    result = celery_module.ask_agent_task.run(
        1,
        9,
        "session-1",
        FACT_MESSAGE,
        True,
        True,
    )

    trace = result["execution_trace"]
    step_names = [step["name"] for step in trace["steps"]]
    assert result["status"] == "success"
    assert result["route_intent"] == "GENERAL_CONSULT"
    assert trackers["legal_agent_calls"] == 1
    assert trackers["ack_calls"] == 0
    assert trace["execution_plan"]["response_mode"] == "temporary_case_analysis"
    assert "legal_agent_answer" in step_names
    # temporary mode should not include persistence steps at all
    assert "fact_persistence" not in step_names
    assert "graph_update" not in step_names
    assert any(
        step["name"] == "legal_agent_answer" and step["status"] == "success"
        for step in trace["steps"]
    )
    # temporary mode doesn't request writes, so nothing to block
    assert trace["writes"]["allowed"] == []
    assert trace["writes"]["blocked"] == []
    assert trace["writes"]["results"] == []
    assert graph_calls == []
    assert persisted["case_summary"] == ""
    assert persisted["case_state_dict"] is None
    assert persisted["case_type"] is None
    assert "确认书" in result["response"]
