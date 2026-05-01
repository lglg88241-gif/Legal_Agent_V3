from __future__ import annotations

import importlib
import sys
from types import ModuleType
from typing import Any

import pytest

from app.services.case_update_acknowledgement import (
    build_case_update_acknowledgement as real_build_case_update_acknowledgement,
)


FACT_MESSAGE = "B公司的人事通知我去签确认书，而且人事王经理口头通知我不用来了。"
ANCHORED_FACT_MESSAGE = "我叫张三，在上海一家叫蓝海科技的公司做后端开发。公司从2023年5月开始拖欠我工资，到现在大概欠了我6万元。"


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


def _anchored_fact_update_result() -> dict[str, object]:
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
            "fact_text": ANCHORED_FACT_MESSAGE,
            "entities": [
                {"label": "张三", "type": "person"},
                {"label": "蓝海科技", "type": "organization"},
            ],
            "events": [
                {
                    "summary": "蓝海科技拖欠张三工资",
                    "participants": ["蓝海科技", "张三"],
                }
            ],
        },
        "previous_fact_confirmation": None,
        "advice_question": None,
        "legal_question": None,
        "confidence": "high",
        "reason": "Current message anchors self and company in the same fact update.",
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

    async def _get_case_graph(*args, **kwargs):
        return {"query": "", "nodes": [], "edges": []}

    fake_graph_extractor.format_graph_memory = _format_graph_memory
    fake_graph_extractor.refresh_case_graph = _refresh_case_graph
    fake_graph_extractor.get_case_graph = _get_case_graph
    sys.modules["app.services.graph_extractor"] = fake_graph_extractor

    fake_legal_agent_module = ModuleType("app.services.legal_agent")

    class FakeImportLegalAgent:
        async def ask(self, *args, **kwargs):
            return {"response": "", "sources": [], "route_intent": "GENERAL_CONSULT"}

    fake_legal_agent_module.legal_agent = FakeImportLegalAgent()
    sys.modules["app.services.legal_agent"] = fake_legal_agent_module

    fake_redis_mgr = ModuleType("app.memory.redis_mgr")
    fake_redis_mgr.get_redis_saver_for_current_loop = lambda: None
    sys.modules["app.memory.redis_mgr"] = fake_redis_mgr


def _load_celery_module():
    _install_celery_import_stubs()
    sys.modules.pop("app.celery_app", None)
    return importlib.import_module("app.celery_app")


def _run_case_update_task(
    monkeypatch: pytest.MonkeyPatch,
    *,
    question: str,
    recent_messages: list[dict[str, object]],
    message_understanding_result: dict[str, object],
    persistence_consent: str = "tracked",
    conversation_mode: str = "TRACKED_CASE",
    legal_agent_response: dict[str, Any] | None = None,
    fail_on_legal_agent: bool = False,
    acknowledgement_handler: Any | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[tuple[int, int, int]], dict[str, Any]]:
    celery_module = _load_celery_module()
    import app.services.agentic_tool_executor as tool_executor_module

    persisted: dict[str, Any] = {}
    graph_calls: list[tuple[int, int, int]] = []
    trackers: dict[str, Any] = {
        "legal_agent_calls": 0,
        "ack_calls": [],
    }

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
            trackers["legal_agent_calls"] += 1
            if fail_on_legal_agent:
                raise AssertionError("case update specialist should not call legal_agent.ask")
            response = dict(
                legal_agent_response
                or {
                    "response": "旧链路回答",
                    "sources": [],
                    "route_intent": "GENERAL_CONSULT",
                    "case_summary": "",
                    "confidence_score": 0.6,
                }
            )
            if persistence_consent == "temporary":
                suffix = "当前会话设置不会写入结构化案情和图谱"
                if suffix not in str(response.get("response") or ""):
                    response["response"] = f"{response['response']} {suffix}".strip()
            return response

    async def fake_understand_message_with_agent(**kwargs):
        return dict(message_understanding_result)

    async def fake_persist_dialog_turn_async(**kwargs):
        persisted.update(kwargs)
        return {"user_message_id": 101, "assistant_message_id": 102}

    class FakeGraphTask:
        @staticmethod
        def delay(conversation_id: int, user_id: int, message_id: int) -> None:
            graph_calls.append((conversation_id, user_id, message_id))

    def tracked_acknowledgement_handler(**kwargs):
        trackers["ack_calls"].append(kwargs)
        if acknowledgement_handler is not None:
            return acknowledgement_handler(**kwargs)
        return real_build_case_update_acknowledgement(**kwargs)

    monkeypatch.setattr(celery_module, "_run_task_async", fake_run_task_async)
    monkeypatch.setattr(celery_module, "legal_agent", FakeLegalAgent())
    monkeypatch.setattr(celery_module, "understand_message_with_agent", fake_understand_message_with_agent)
    monkeypatch.setattr(celery_module, "_persist_dialog_turn_async", fake_persist_dialog_turn_async)
    monkeypatch.setattr(celery_module, "update_graph_task", FakeGraphTask())
    monkeypatch.setattr(celery_module, "build_case_update_acknowledgement", tracked_acknowledgement_handler)
    monkeypatch.setattr(tool_executor_module, "build_case_update_acknowledgement", tracked_acknowledgement_handler)

    result = celery_module.ask_agent_task.run(
        1,
        9,
        "session-1",
        question,
        True,
        True,
    )
    return result, persisted, graph_calls, trackers


def test_fact_update_uses_case_update_acknowledgement_specialist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, persisted, graph_calls, trackers = _run_case_update_task(
        monkeypatch,
        question=ANCHORED_FACT_MESSAGE,
        recent_messages=[],
        message_understanding_result=_anchored_fact_update_result(),
        legal_agent_response={
            "response": "可以先固定这条事实，再准备确认书、社保记录和沟通证据。",
            "sources": [],
            "route_intent": "GENERAL_CONSULT",
            "case_summary": "",
            "confidence_score": 0.78,
        },
    )

    assert result["status"] == "success"
    assert result["route_intent"] == "CASE_UPDATE"
    assert trackers["legal_agent_calls"] == 0
    assert len(trackers["ack_calls"]) == 1
    assert "已记录这条新的案情事实" in result["response"]
    assert "蓝海科技" in result["response"]
    assert "法律" not in result["response"]
    assert "参与者" not in result["response"]
    assert "映射" not in result["response"]
    assert graph_calls == [(9, 1, 101)]
    action_summaries = [str(item.get("result_summary") or "") for item in result["agent_actions"]]
    assert any("Lightweight case update acknowledgement generated" in summary for summary in action_summaries)
    assert all("Retrieval pipeline completed" not in summary for summary in action_summaries)
    assert all("Final legal response generated" not in summary for summary in action_summaries)
    assert persisted["metadata"]["route_intent"] == "CASE_UPDATE"
    assert persisted["user_metadata"]["graph_update_input_text"] == ANCHORED_FACT_MESSAGE
    assert "reference_guard" not in persisted["user_metadata"]


def test_oral_reference_fact_update_schedules_graph_write_without_preblocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, persisted, graph_calls, trackers = _run_case_update_task(
        monkeypatch,
        question="后来王经理又口头说让我不用再来上班。",
        recent_messages=[],
        message_understanding_result=_fact_update_result(),
        fail_on_legal_agent=True,
    )

    assert result["status"] == "success"
    assert result["route_intent"] == "CASE_UPDATE"
    assert trackers["legal_agent_calls"] == 0
    assert len(trackers["ack_calls"]) == 1
    assert "已记录这条新的案情事实" in result["response"]
    assert "王经理" in result["response"]
    assert graph_calls == [(9, 1, 101)]
    assert persisted["metadata"]["route_intent"] == "CASE_UPDATE"
    assert persisted["user_metadata"]["graph_update_input_text"] == "后来王经理又口头说让我不用再来上班。"


def test_confirm_previous_fact_uses_previous_fact_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, persisted, graph_calls, trackers = _run_case_update_task(
        monkeypatch,
        question="我确认这个事实",
        recent_messages=[{"message_id": 71, "role": "user", "content": FACT_MESSAGE}],
        message_understanding_result=_confirm_previous_fact_result(target_text=None, target_message_id=71),
        fail_on_legal_agent=True,
    )

    assert result["status"] == "success"
    assert result["route_intent"] == "CASE_UPDATE"
    assert trackers["legal_agent_calls"] == 0
    assert len(trackers["ack_calls"]) == 1
    assert trackers["ack_calls"][0]["persisted_fact_text"] == FACT_MESSAGE
    assert "已确认上一条事实" in result["response"]
    assert persisted["user_metadata"]["confirmed_fact_text"] == FACT_MESSAGE
    assert persisted["user_metadata"]["graph_update_input_text"] == FACT_MESSAGE
    assert graph_calls == [(9, 1, 101)]


def test_confirm_previous_fact_without_previous_fact_returns_clarification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, persisted, graph_calls, trackers = _run_case_update_task(
        monkeypatch,
        question="我确认这个事实",
        recent_messages=[],
        message_understanding_result=_confirm_previous_fact_result(target_text=None, target_message_id=None),
        fail_on_legal_agent=True,
    )

    assert result["status"] == "success"
    assert result["route_intent"] == "CASE_UPDATE"
    assert "暂时没有可确认的事实" in result["response"]
    assert trackers["legal_agent_calls"] == 0
    assert len(trackers["ack_calls"]) == 1
    assert trackers["ack_calls"][0]["persisted_fact_text"] in {None, ""}
    assert graph_calls == []
    assert persisted["case_summary"] == ""


def test_temporary_fact_update_uses_transient_legal_analysis_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, persisted, graph_calls, trackers = _run_case_update_task(
        monkeypatch,
        question="我补充一下，人事让我先签确认书再谈补缴社保。",
        recent_messages=[],
        message_understanding_result=_fact_update_result(),
        persistence_consent="temporary",
        legal_agent_response={
            "response": "可以先固定这条事实，再准备确认书、社保记录和聊天记录。",
            "sources": [],
            "route_intent": "GENERAL_CONSULT",
            "case_summary": "",
            "confidence_score": 0.78,
        },
    )

    assert result["status"] == "success"
    assert result["route_intent"] == "GENERAL_CONSULT"
    assert trackers["legal_agent_calls"] == 1
    assert trackers["ack_calls"] == []
    assert "确认书" in result["response"]
    assert "当前会话设置不会写入结构化案情和图谱" in result["response"]
    assert graph_calls == []
    assert persisted["case_summary"] == ""
    assert persisted["case_state_dict"] is None
    assert persisted["case_type"] is None


def test_acknowledgement_specialist_exception_falls_back_to_legacy_legal_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_acknowledgement_error(**kwargs):
        raise RuntimeError("boom")

    result, persisted, graph_calls, trackers = _run_case_update_task(
        monkeypatch,
        question=ANCHORED_FACT_MESSAGE,
        recent_messages=[],
        message_understanding_result=_anchored_fact_update_result(),
        legal_agent_response={
            "response": "旧链路回答",
            "sources": [],
            "route_intent": "GENERAL_CONSULT",
            "case_summary": "",
            "confidence_score": 0.6,
        },
        acknowledgement_handler=raise_acknowledgement_error,
    )

    assert result["status"] == "success"
    assert result["route_intent"] == "CASE_UPDATE"
    assert result["response"] == "旧链路回答"
    assert trackers["legal_agent_calls"] == 1
    assert len(trackers["ack_calls"]) == 1
    assert graph_calls == [(9, 1, 101)]
    assert persisted["metadata"]["route_intent"] == "CASE_UPDATE"


@pytest.mark.parametrize(
    "message",
    [
        "我补充一下，人事让我先签确认书再谈补缴社保。",
        "后来王经理又口头说让我不用再来上班。",
        "B公司的人事通知我去签确认书，而且王经理说我不用继续来上班了。",
    ],
)
def test_fact_update_paraphrases_schedule_graph_update_without_preblocking(
    monkeypatch: pytest.MonkeyPatch,
    message: str,
) -> None:
    result, persisted, graph_calls, trackers = _run_case_update_task(
        monkeypatch,
        question=message,
        recent_messages=[],
        message_understanding_result=_fact_update_result(),
        fail_on_legal_agent=True,
    )

    assert result["status"] == "success"
    assert result["route_intent"] == "CASE_UPDATE"
    assert trackers["legal_agent_calls"] == 0
    assert len(trackers["ack_calls"]) == 1
    assert "已记录这条新的案情事实" in result["response"]
    assert "法律" not in result["response"]
    assert "案例" not in result["response"]
    assert "映射" not in result["response"]
    assert graph_calls == [(9, 1, 101)]
    assert persisted["metadata"]["route_intent"] == "CASE_UPDATE"
    assert persisted["user_metadata"]["graph_update_input_text"] == message
