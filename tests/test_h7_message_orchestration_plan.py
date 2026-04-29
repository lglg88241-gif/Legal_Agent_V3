from __future__ import annotations

import importlib
import sys
from types import ModuleType

import pytest

from app.services.message_orchestrator import build_execution_plan_from_message_understanding


FACT_MESSAGE = "B公司的人事通知我去签确认书。而且人事王经理口头通知我不用来了。"


def _subject_reference_result() -> dict[str, object]:
    return {
        "source": "message_understanding_agent",
        "intent": "subject_reference",
        "route_hint": "DOSSIER_MEMORY",
        "needs_legal_rag": False,
        "needs_dossier_graph_context": True,
        "needs_structured_persistence": False,
        "needs_graph_update": False,
        "subject_reference": {
            "enabled": True,
            "mention": "公司",
            "target_type": "organization",
        },
        "fact_update": None,
        "previous_fact_confirmation": None,
        "advice_question": None,
        "legal_question": None,
        "confidence": "high",
        "reason": "Current message asks for dossier subject reference.",
        "provider": "openai",
        "model": "gpt-5.1",
        "fallback_reason": None,
        "trace": {"steps": ["call_llm"], "warnings": []},
    }


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
            "entities": [
                {"label": "B公司", "type": "organization"},
                {"label": "王经理", "type": "person"},
                {"label": "确认书", "type": "document"},
            ],
            "events": [
                {
                    "summary": "王经理口头通知当事人不用来了",
                    "participants": ["王经理", "当事人"],
                }
            ],
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


def _confirm_previous_fact_result(
    *,
    target_text: str | None = None,
    target_message_id: int | None = 71,
) -> dict[str, object]:
    return {
        "source": "message_understanding_agent",
        "intent": "confirm_previous_fact",
        "route_hint": "CASE_UPDATE",
        "needs_legal_rag": False,
        "needs_dossier_graph_context": False,
        "needs_structured_persistence": True,
        "needs_graph_update": True,
        "subject_reference": None,
        "fact_update": None,
        "previous_fact_confirmation": {
            "enabled": True,
            "action": "confirm",
            "confirmation_text": "我确认这个事实",
            "target_message_id": target_message_id,
            "target_text": target_text,
            "target_fact_summary": "王经理口头通知当事人不用来了" if (target_text or target_message_id) else None,
        },
        "advice_question": None,
        "legal_question": None,
        "confidence": "high" if (target_text or target_message_id) else "low",
        "reason": "Current message confirms the previous factual statement.",
        "provider": "openai",
        "model": "gpt-5.1",
        "fallback_reason": None,
        "trace": {"steps": ["call_llm"], "warnings": []},
    }


@pytest.mark.parametrize(
    "message",
    [
        "这里的公司指的是哪家公司？",
        "这个公司是谁？",
        "前面说的公司是哪一个？",
    ],
)
def test_build_execution_plan_subject_reference_paraphrases(message: str) -> None:
    plan = build_execution_plan_from_message_understanding(
        _subject_reference_result(),
        original_message=message,
        recent_messages=[],
        dossier_id=12,
        persistence_consent="tracked",
    )

    assert plan["source"] == "message_understanding"
    assert plan["intent"] == "subject_reference"
    assert plan["primary_route"] == "DOSSIER_MEMORY"
    assert plan["is_actionable"] is True
    assert [step["name"] for step in plan["steps"]] == ["dossier_memory_answer"]
    assert plan["resolved_inputs"]["effective_persistence_text"] == message
    assert plan["trace"]["provider"] == "openai"
    assert all(step["name"] != "graph_update" for step in plan["steps"])


@pytest.mark.parametrize(
    "message",
    [
        FACT_MESSAGE,
        "后来王经理又口头说让我不用再来上班。",
        "我补充一下，人事让我先签确认书再谈补缴社保。",
    ],
)
def test_build_execution_plan_fact_update_paraphrases_use_current_message(message: str) -> None:
    plan = build_execution_plan_from_message_understanding(
        _fact_update_result(),
        original_message=message,
        recent_messages=[],
        dossier_id=12,
        persistence_consent="tracked",
    )

    assert plan["intent"] == "fact_update"
    assert plan["primary_route"] == "CASE_UPDATE"
    assert plan["is_actionable"] is True
    assert [step["name"] for step in plan["steps"]] == [
        "fact_persistence",
        "graph_update",
        "assistant_acknowledgement",
    ]
    assert plan["resolved_inputs"]["effective_persistence_text"] == message
    assert plan["resolved_inputs"]["graph_update_input_text"] == message


@pytest.mark.parametrize(
    "message",
    [
        "我确认这个事实",
        "对，上面那件事属实",
        "确认上一条",
    ],
)
def test_build_execution_plan_confirm_previous_fact_paraphrases_use_previous_fact_text(
    message: str,
) -> None:
    plan = build_execution_plan_from_message_understanding(
        _confirm_previous_fact_result(target_text=None, target_message_id=71),
        original_message=message,
        recent_messages=[
            {"message_id": 71, "role": "user", "content": FACT_MESSAGE},
            {"message_id": 72, "role": "assistant", "content": "继续补充。"},
        ],
        dossier_id=12,
        persistence_consent="tracked",
    )

    assert plan["intent"] == "confirm_previous_fact"
    assert plan["primary_route"] == "CASE_UPDATE"
    assert plan["is_actionable"] is True
    assert [step["name"] for step in plan["steps"]] == [
        "fact_confirmation",
        "fact_persistence",
        "graph_update",
        "assistant_acknowledgement",
    ]
    assert plan["resolved_inputs"]["previous_fact_text"] == FACT_MESSAGE
    assert plan["resolved_inputs"]["effective_persistence_text"] == FACT_MESSAGE
    assert plan["resolved_inputs"]["graph_update_input_text"] == FACT_MESSAGE


def test_build_execution_plan_confirm_previous_fact_without_previous_fact_is_not_actionable() -> None:
    plan = build_execution_plan_from_message_understanding(
        _confirm_previous_fact_result(target_text=None, target_message_id=None),
        original_message="我确认这个事实",
        recent_messages=[],
        dossier_id=12,
        persistence_consent="tracked",
    )

    assert plan["intent"] == "confirm_previous_fact"
    assert plan["primary_route"] == "CASE_UPDATE"
    assert plan["is_actionable"] is False
    assert [step["name"] for step in plan["steps"]] == ["fact_confirmation"]
    assert plan["resolved_inputs"]["previous_fact_text"] == ""
    assert "confirmed_previous_fact_text_unavailable" in plan["trace"]["warnings"]


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
    recent_messages: list[dict[str, object]],
    message_understanding_result: dict[str, object],
    persistence_consent: str = "tracked",
    conversation_mode: str = "TRACKED_CASE",
) -> tuple[object, dict[str, object], list[tuple[int, int, int]]]:
    celery_module = _load_celery_module()
    persisted: dict[str, object] = {}
    graph_calls: list[tuple[int, int, int]] = []

    async def fake_run_task_async(conversation_id: int, user_id: int, current_question: str | None = None):
        return {
            "chat_history": "",
            "latest_user_input": current_question,
            "recent_messages": recent_messages,
            "case_summary": "旧摘要",
            "graph_memory": "",
            "saved_case_state": None,
            "persistence_consent": persistence_consent,
            "conversation_mode": conversation_mode,
            "dossier_id": 12,
            "dossier_context": {"id": 12, "title": "劳动争议案卷"},
            "dossier_graph_summary": "",
            "dossier_graph_context": None,
        }

    class FakeLegalAgent:
        async def ask(self, *args, **kwargs):
            return {
                "response": "主链路旧回答",
                "sources": [],
                "route_intent": "GENERAL_CONSULT",
                "case_summary": "",
                "confidence_score": 0.6,
            }

    async def fake_understand_message_with_agent(**kwargs):
        return dict(message_understanding_result)

    async def fake_persist_dialog_turn_async(**kwargs):
        persisted.update(kwargs)
        return {"user_message_id": 101, "assistant_message_id": 102}

    class FakeGraphTask:
        @staticmethod
        def delay(conversation_id: int, user_id: int, message_id: int) -> None:
            graph_calls.append((conversation_id, user_id, message_id))

    monkeypatch.setattr(celery_module, "_run_task_async", fake_run_task_async)
    monkeypatch.setattr(celery_module, "legal_agent", FakeLegalAgent())
    monkeypatch.setattr(celery_module, "understand_message_with_agent", fake_understand_message_with_agent)
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


def test_ask_agent_task_uses_execution_plan_for_fact_update_even_if_legacy_answer_route_is_general(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question = "后来王经理又口头说让我不用再来上班。"
    result, persisted, graph_calls = _run_ask_agent_task(
        monkeypatch,
        question=question,
        recent_messages=[],
        message_understanding_result=_fact_update_result(),
    )

    assert result["status"] == "success"
    assert result["route_intent"] == "CASE_UPDATE"
    assert result["execution_plan"]["primary_route"] == "CASE_UPDATE"
    assert "王经理" in persisted["case_summary"]
    assert persisted["metadata"]["execution_plan"]["primary_route"] == "CASE_UPDATE"
    assert graph_calls == [(9, 1, 101)]
    action_summaries = [str(item.get("result_summary") or "") for item in result["agent_actions"]]
    assert any("Execution plan selected CASE_UPDATE" in summary for summary in action_summaries)
    assert all("General consult route selected" not in summary for summary in action_summaries)


def test_ask_agent_task_uses_execution_plan_for_confirm_previous_fact_graph_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, persisted, graph_calls = _run_ask_agent_task(
        monkeypatch,
        question="确认上一条",
        recent_messages=[
            {"message_id": 71, "role": "user", "content": FACT_MESSAGE},
        ],
        message_understanding_result=_confirm_previous_fact_result(target_text=None, target_message_id=71),
    )

    assert result["status"] == "success"
    assert result["route_intent"] == "CASE_UPDATE"
    assert result["execution_plan"]["resolved_inputs"]["previous_fact_text"] == FACT_MESSAGE
    assert persisted["user_metadata"]["graph_update_input_text"] == FACT_MESSAGE
    assert persisted["user_metadata"]["confirmed_fact_text"] == FACT_MESSAGE
    assert "王经理" in persisted["case_summary"]
    assert graph_calls == [(9, 1, 101)]
    action_summaries = [str(item.get("result_summary") or "") for item in result["agent_actions"]]
    assert any("Execution plan selected CASE_UPDATE" in summary for summary in action_summaries)
    assert any("Previous fact confirmed for graph update" in summary for summary in action_summaries)


@pytest.mark.parametrize(
    ("persistence_consent", "conversation_mode"),
    [
        ("temporary", "TRACKED_CASE"),
        ("tracked", "LEGAL_QA"),
    ],
)
def test_execution_plan_guards_block_graph_update_when_required(
    monkeypatch: pytest.MonkeyPatch,
    persistence_consent: str,
    conversation_mode: str,
) -> None:
    result, persisted, graph_calls = _run_ask_agent_task(
        monkeypatch,
        question="我确认这个事实",
        recent_messages=[
            {"message_id": 71, "role": "user", "content": FACT_MESSAGE},
        ],
        message_understanding_result=_confirm_previous_fact_result(target_text=None, target_message_id=71),
        persistence_consent=persistence_consent,
        conversation_mode=conversation_mode,
    )

    assert result["status"] == "success"
    assert result["route_intent"] == "CASE_UPDATE"
    assert persisted["case_summary"] == ""
    assert graph_calls == []
