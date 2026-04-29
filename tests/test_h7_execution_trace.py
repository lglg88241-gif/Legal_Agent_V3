from __future__ import annotations

import importlib
import sys
from types import ModuleType
from typing import Any

import pytest


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
    legal_agent_response: dict[str, Any] | None = None,
    fail_on_legal_agent: bool = False,
    acknowledgement_handler: Any | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[tuple[int, int, int]], dict[str, Any]]:
    celery_module = _load_celery_module()
    persisted: dict[str, Any] = {}
    graph_calls: list[tuple[int, int, int]] = []
    trackers: dict[str, Any] = {
        "legal_agent_calls": 0,
        "ack_calls": [],
        "merged_metadata": [],
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
                raise AssertionError("this path should not call legal_agent.ask")
            return legal_agent_response or {
                "response": "旧链路回复",
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

    async def fake_merge_message_metadata_async(**kwargs):
        trackers["merged_metadata"].append(kwargs)
        metadata_patch = kwargs.get("metadata_patch")
        if isinstance(metadata_patch, dict):
            persisted_metadata = persisted.get("metadata")
            if isinstance(persisted_metadata, dict):
                persisted_metadata.update(metadata_patch)
        return None

    class FakeAsyncResult:
        def __init__(self, task_id: str) -> None:
            self.id = task_id

    class FakeGraphTask:
        @staticmethod
        def delay(conversation_id: int, user_id: int, message_id: int):
            graph_calls.append((conversation_id, user_id, message_id))
            return FakeAsyncResult(f"graph-task-{conversation_id}-{message_id}")

    original_ack = celery_module.build_case_update_acknowledgement

    def wrapped_acknowledgement_handler(**kwargs):
        trackers["ack_calls"].append(kwargs)
        if acknowledgement_handler is not None:
            return acknowledgement_handler(**kwargs)
        return original_ack(**kwargs)

    monkeypatch.setattr(celery_module, "_run_task_async", fake_run_task_async)
    monkeypatch.setattr(celery_module, "legal_agent", FakeLegalAgent())
    monkeypatch.setattr(celery_module, "understand_message_with_agent", fake_understand_message_with_agent)
    monkeypatch.setattr(celery_module, "_persist_dialog_turn_async", fake_persist_dialog_turn_async)
    monkeypatch.setattr(celery_module, "_merge_message_metadata_async", fake_merge_message_metadata_async)
    monkeypatch.setattr(celery_module, "update_graph_task", FakeGraphTask())
    monkeypatch.setattr(
        celery_module,
        "build_case_update_acknowledgement",
        wrapped_acknowledgement_handler,
    )

    result = celery_module.ask_agent_task.run(
        1,
        9,
        "session-1",
        question,
        True,
        True,
    )
    return result, persisted, graph_calls, trackers


def _step_lookup(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    trace = result.get("execution_trace") if isinstance(result, dict) else {}
    steps = trace.get("steps") if isinstance(trace, dict) else []
    lookup: dict[str, dict[str, Any]] = {}
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        key = str(step.get("name") or "").strip()
        if key and key not in lookup:
            lookup[key] = step
    return lookup


def _guard_lookup(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    trace = result.get("execution_trace") if isinstance(result, dict) else {}
    guards = trace.get("guards_applied") if isinstance(trace, dict) else []
    lookup: dict[str, dict[str, Any]] = {}
    for guard in guards or []:
        if not isinstance(guard, dict):
            continue
        key = str(guard.get("name") or "").strip()
        if key:
            lookup[key] = guard
    return lookup


def test_fact_update_execution_trace_records_specialist_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, persisted, graph_calls, trackers = _run_ask_agent_task(
        monkeypatch,
        question="后来王经理又口头说让我不用再来上班。",
        recent_messages=[],
        message_understanding_result=_fact_update_result(),
        fail_on_legal_agent=True,
    )

    trace = result["execution_trace"]
    steps = _step_lookup(result)
    assert trace["schema_version"] == 1
    assert trace["message_understanding"]["intent"] == "fact_update"
    assert trace["execution_plan"]["primary_route"] == "CASE_UPDATE"
    assert trace["legacy_fallback"]["used"] is False
    assert steps["assistant_acknowledgement"]["status"] == "success"
    assert steps["assistant_acknowledgement"]["agent"] == "case_update_acknowledgement"
    assert steps["fact_persistence"]["status"] == "success"
    assert steps["graph_update"]["status"] == "success"
    assert steps["graph_update"]["agent"] == "celery.update_graph_task"
    assert steps["graph_update"]["details"]["task_id"] == "graph-task-9-101"
    assert graph_calls == [(9, 1, 101)]
    assert trackers["merged_metadata"]
    assert persisted["metadata"]["route_intent"] == "CASE_UPDATE"
    assert result["agent_actions"]


def test_confirm_previous_fact_without_previous_fact_marks_guard_and_skips_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, persisted, graph_calls, trackers = _run_ask_agent_task(
        monkeypatch,
        question="我确认这个事实",
        recent_messages=[],
        message_understanding_result=_confirm_previous_fact_result(target_text=None, target_message_id=None),
        legal_agent_response={
            "response": "需要你先说明具体事实。",
            "sources": [],
            "route_intent": "GENERAL_CONSULT",
            "case_summary": "",
            "confidence_score": 0.5,
        },
    )

    guards = _guard_lookup(result)
    steps = _step_lookup(result)
    assert guards["missing_previous_fact"]["triggered"] is True
    assert steps["fact_confirmation"]["status"] == "skipped"
    assert steps["fact_confirmation"]["skip_reason"] == "missing_previous_fact"
    assert steps["graph_update"]["status"] == "skipped"
    assert steps["graph_update"]["skip_reason"] == "missing_previous_fact"
    assert result["execution_trace"]["legacy_fallback"]["used"] is True
    assert result["execution_trace"]["legacy_fallback"]["reason"] == "missing_previous_fact"
    assert "assistant_acknowledgement" not in steps
    assert graph_calls == []
    assert trackers["legal_agent_calls"] == 1
    assert result["response"] == "需要你先说明具体事实。"
    assert persisted["case_summary"] == ""


def test_temporary_fact_update_marks_guard_and_skips_persistence_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, persisted, graph_calls, trackers = _run_ask_agent_task(
        monkeypatch,
        question="我补充一下，人事让我先签确认书再谈补缴社保。",
        recent_messages=[],
        message_understanding_result=_fact_update_result(),
        persistence_consent="temporary",
        fail_on_legal_agent=True,
    )

    guards = _guard_lookup(result)
    steps = _step_lookup(result)
    assert guards["temporary_blocks_persistence"]["triggered"] is True
    assert steps["assistant_acknowledgement"]["status"] == "success"
    assert steps["fact_persistence"]["status"] == "skipped"
    assert steps["fact_persistence"]["skip_reason"] == "temporary"
    assert steps["graph_update"]["status"] == "skipped"
    assert steps["graph_update"]["skip_reason"] == "temporary"
    assert graph_calls == []
    assert trackers["legal_agent_calls"] == 0
    assert persisted["case_summary"] == ""


def test_subject_reference_trace_uses_dossier_memory_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, persisted, graph_calls, trackers = _run_ask_agent_task(
        monkeypatch,
        question="这里的公司指的是哪家公司？",
        recent_messages=[],
        message_understanding_result=_subject_reference_result(),
        fail_on_legal_agent=True,
    )

    steps = _step_lookup(result)
    assert result["route_intent"] == "DOSSIER_MEMORY"
    assert result["execution_trace"]["legacy_fallback"]["used"] is False
    assert steps["dossier_memory_subject_reference"]["status"] == "success"
    assert steps["dossier_memory_subject_reference"]["agent"] == "dossier_memory_helper"
    assert "graph_update" not in steps
    assert trackers["legal_agent_calls"] == 0
    assert graph_calls == []
    assert persisted["metadata"]["execution_trace"]["legacy_fallback"]["used"] is False


def test_acknowledgement_failure_marks_legacy_fallback_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_acknowledgement_error(**kwargs):
        raise RuntimeError("ack boom")

    result, persisted, graph_calls, trackers = _run_ask_agent_task(
        monkeypatch,
        question="后来王经理又口头说让我不用再来上班。",
        recent_messages=[],
        message_understanding_result=_fact_update_result(),
        legal_agent_response={
            "response": "旧链路回复",
            "sources": [],
            "route_intent": "GENERAL_CONSULT",
            "case_summary": "",
            "confidence_score": 0.6,
        },
        acknowledgement_handler=raise_acknowledgement_error,
    )

    steps = _step_lookup(result)
    assert result["execution_trace"]["legacy_fallback"]["used"] is True
    assert result["execution_trace"]["legacy_fallback"]["reason"] == "case_update_acknowledgement_failed"
    assert steps["assistant_acknowledgement"]["status"] == "failed"
    assert steps["legal_agent_answer"]["status"] == "success"
    assert trackers["legal_agent_calls"] == 1
    assert graph_calls == [(9, 1, 101)]
    assert result["agent_actions"]
    assert persisted["metadata"]["route_intent"] == "CASE_UPDATE"


def test_execution_trace_schema_constraints_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, persisted, graph_calls, trackers = _run_ask_agent_task(
        monkeypatch,
        question="后来王经理又口头说让我不用再来上班。",
        recent_messages=[],
        message_understanding_result=_fact_update_result(),
        fail_on_legal_agent=True,
    )

    trace = result["execution_trace"]
    assert trace["schema_version"] == 1
    assert "provider" not in trace
    assert "model" not in trace
    for step in trace["steps"]:
        assert step["name"]
        assert step["agent"]
        assert step["status"]
        if step["status"] == "skipped":
            assert step["skip_reason"]
        if step["status"] == "fallback":
            assert step["fallback_reason"]
    assert result["agent_actions"]
