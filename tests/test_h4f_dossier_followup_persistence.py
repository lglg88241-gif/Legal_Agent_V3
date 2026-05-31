from __future__ import annotations

import importlib
import sys
from types import ModuleType

import pytest

from app.services.case_followup import (
    is_case_fact_update_message,
    is_dossier_followup_question,
    should_persist_structured_case_message,
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


def _run_ask_agent_task(
    monkeypatch: pytest.MonkeyPatch,
    *,
    question: str,
    persistence_consent: str,
    conversation_mode: str = "TRACKED_CASE",
    dossier_context: dict[str, object] | None = None,
    answer_route_intent: str = "CASE_UPDATE",
    answer_case_summary: str = "新摘要",
    dossier_followup_question: bool | None = None,
    message_is_fact_update: bool | None = None,
) -> tuple[dict[str, object], dict[str, object], list[tuple[int, int, int]]]:
    celery_module = _load_celery_module()
    persisted: dict[str, object] = {}
    graph_calls: list[tuple[int, int, int]] = []

    async def fake_run_task_async(conversation_id: int, user_id: int, current_question: str | None = None):
        return {
            "chat_history": "",
            "case_summary": "旧摘要",
            "graph_memory": "",
            "saved_case_state": {"monthly_salary": 12000},
            "persistence_consent": persistence_consent,
            "conversation_mode": conversation_mode,
            "dossier_id": dossier_context.get("id") if isinstance(dossier_context, dict) else None,
            "dossier_context": dossier_context,
            "dossier_graph_summary": "",
            "dossier_graph_context": None,
        }

    class FakeLegalAgent:
        async def ask(self, *args, **kwargs):
            return {
                "response": "分析结果",
                "sources": [],
                "route_intent": answer_route_intent,
                "case_summary": answer_case_summary,
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
    if dossier_followup_question is not None:
        monkeypatch.setattr(
            celery_module,
            "is_dossier_followup_question",
            lambda text: dossier_followup_question,
        )
    if message_is_fact_update is not None:
        monkeypatch.setattr(
            celery_module,
            "is_case_fact_update_message",
            lambda text: message_is_fact_update,
        )

    result = celery_module.ask_agent_task.run(
        1,
        9,
        "session-1",
        question,
        True,
        True,
    )
    return result, persisted, graph_calls


def test_followup_fact_supplement_message_is_detected_as_fact_update() -> None:
    message = "后来A公司的人事又通知我补签协议，还说之前解除的事情可以再协商。"
    assert is_case_fact_update_message(message) is True
    assert is_dossier_followup_question(message) is False


def test_case_update_fact_message_persists_even_if_graph_query_classifier_thinks_it_is_followup() -> None:
    message = "后来A公司的人事又通知我补签协议，还说之前解除的事情可以再协商。"
    assert should_persist_structured_case_message(
        text=message,
        route_intent="CASE_UPDATE",
        persistence_consent="tracked",
        conversation_mode="TRACKED_CASE",
        has_dossier_context=True,
    ) is True


def test_dossier_fact_update_persists_and_updates_graph_when_fact_update_signal_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, persisted, graph_calls = _run_ask_agent_task(
        monkeypatch,
        question="后来A公司的人事又通知我补签协议，还说之前解除的事情可以再协商。",
        persistence_consent="tracked",
        dossier_context={"id": 7, "title": "劳动争议案卷"},
        dossier_followup_question=True,
        message_is_fact_update=True,
    )

    assert result["status"] == "success"
    assert result["route_intent"] == "CASE_UPDATE"
    assert persisted["case_summary"] == "新摘要"
    assert persisted["case_state_dict"] == {"monthly_salary": 15000, "has_unsigned_contract": True}
    assert persisted["case_type"] == "劳动争议"
    assert graph_calls == [(9, 1, 101)]


def test_dossier_followup_question_still_skips_when_message_is_not_fact_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, persisted, graph_calls = _run_ask_agent_task(
        monkeypatch,
        question="我下一步应该准备哪些证据？",
        persistence_consent="tracked",
        dossier_context={"id": 7, "title": "劳动争议案卷"},
        answer_route_intent="CASE_UPDATE",
        dossier_followup_question=True,
        message_is_fact_update=False,
    )

    assert result["status"] == "success"
    assert result["route_intent"] == "GENERAL_CONSULT"
    assert persisted["case_summary"] == ""
    assert persisted["case_state_dict"] is None
    assert persisted["case_type"] is None
    assert graph_calls == []


def test_temporary_consent_still_skips_fact_update_graph_write_even_with_dossier_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, persisted, graph_calls = _run_ask_agent_task(
        monkeypatch,
        question="后来A公司的人事又通知我补签协议，还说之前解除的事情可以再协商。",
        persistence_consent="temporary",
        dossier_context={"id": 7, "title": "劳动争议案卷"},
        dossier_followup_question=True,
        message_is_fact_update=True,
    )

    assert result["status"] == "success"
    assert result["route_intent"] == "CASE_UPDATE"
    assert persisted["case_summary"] == ""
    assert persisted["case_state_dict"] is None
    assert persisted["case_type"] is None
    assert graph_calls == []


def test_legal_qa_still_does_not_write_structured_case_or_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, persisted, graph_calls = _run_ask_agent_task(
        monkeypatch,
        question="我下一步应该准备哪些证据？",
        persistence_consent="tracked",
        conversation_mode="LEGAL_QA",
        dossier_context={"id": 7, "title": "劳动争议案卷"},
        answer_route_intent="GENERAL_CONSULT",
        answer_case_summary="旧摘要",
    )

    assert result["status"] == "success"
    assert result["route_intent"] == "GENERAL_CONSULT"
    assert persisted["case_summary"] == ""
    assert persisted["case_state_dict"] is None
    assert persisted["case_type"] is None
    assert graph_calls == []
