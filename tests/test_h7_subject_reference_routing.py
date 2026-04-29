from __future__ import annotations

import importlib
import sys
from types import ModuleType
from typing import Any

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

    fake_redis_mgr = ModuleType("app.memory.redis_mgr")
    fake_redis_mgr.get_redis_saver_for_current_loop = lambda: None
    sys.modules["app.memory.redis_mgr"] = fake_redis_mgr


def _load_celery_module():
    _install_celery_import_stubs()
    sys.modules.pop("app.celery_app", None)
    return importlib.import_module("app.celery_app")


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
            "target_span": "这里的公司",
            "mention": "公司",
            "target_mention": "公司",
            "target_type": "organization",
            "reference_type": "organization",
            "scope_order": [
                "current_conversation_graph",
                "current_conversation_messages",
                "dossier_confirmed_participants",
                "dossier_display_graph",
                "dossier_cross_conversation_candidates",
            ],
            "needs_clarification_if_ambiguous": True,
        },
        "fact_update": None,
        "previous_fact_confirmation": None,
        "advice_question": None,
        "legal_question": None,
        "clarification_response": None,
        "confidence": "high",
        "reason": "Current message asks for dossier subject reference.",
        "provider": "openai",
        "model": "gpt-5.1",
        "fallback_reason": None,
        "trace": {"steps": ["build_request", "call_llm"], "warnings": []},
    }


def _run_subject_reference_task(
    monkeypatch: pytest.MonkeyPatch,
    *,
    question: str,
    recent_messages: list[dict[str, Any]],
    dossier_context: dict[str, Any],
    dossier_graph_context: dict[str, Any],
    message_understanding_result: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    celery_module = _load_celery_module()
    persisted: dict[str, Any] = {}
    trackers: dict[str, Any] = {
        "legal_agent_calls": 0,
        "graph_calls": [],
    }

    async def fake_run_task_async(
        conversation_id: int,
        user_id: int,
        current_question: str | None = None,
    ):
        return {
            "chat_history": "",
            "latest_user_input": current_question,
            "recent_messages": recent_messages,
            "case_summary": "",
            "graph_memory": "",
            "saved_case_state": None,
            "persistence_consent": "tracked",
            "conversation_mode": "TRACKED_CASE",
            "dossier_id": 12,
            "dossier_context": dossier_context,
            "dossier_graph_summary": "",
            "dossier_graph_context": dossier_graph_context,
        }

    class FailIfCalledLegalAgent:
        async def ask(self, *args, **kwargs):
            trackers["legal_agent_calls"] += 1
            raise AssertionError("subject_reference and clarification_response must not call legal_agent.ask")

    async def fake_persist_dialog_turn_async(**kwargs):
        persisted.update(kwargs)
        return {"user_message_id": 401, "assistant_message_id": 402}

    class FailIfGraphUpdateCalled:
        @staticmethod
        def delay(conversation_id: int, user_id: int, message_id: int) -> None:
            trackers["graph_calls"].append((conversation_id, user_id, message_id))
            raise AssertionError("subject_reference and clarification_response must not trigger update_graph_task")

    monkeypatch.setattr(celery_module, "_run_task_async", fake_run_task_async)
    monkeypatch.setattr(celery_module, "legal_agent", FailIfCalledLegalAgent())
    monkeypatch.setattr(celery_module, "_persist_dialog_turn_async", fake_persist_dialog_turn_async)
    monkeypatch.setattr(celery_module, "update_graph_task", FailIfGraphUpdateCalled())

    if message_understanding_result is not None:
        async def fake_understand_message_with_agent(**kwargs):
            return dict(message_understanding_result)

        monkeypatch.setattr(
            celery_module,
            "understand_message_with_agent",
            fake_understand_message_with_agent,
        )

    result = celery_module.ask_agent_task.run(
        5,
        9,
        "session-9",
        question,
        True,
        True,
    )
    return result, persisted, trackers


def test_subject_reference_routes_through_current_conversation_graph_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, persisted, trackers = _run_subject_reference_task(
        monkeypatch,
        question="这里的公司指的是哪家公司？",
        recent_messages=[
            {"message_id": 11, "role": "user", "content": "我和B公司签了劳动合同。", "metadata": {}},
            {"message_id": 12, "role": "assistant", "content": "继续补充。", "metadata": {}},
        ],
        dossier_context={"id": 12, "title": "劳动争议案卷", "confirmed_participants": [], "participant_mentions": []},
        dossier_graph_context={
            "participants": [],
            "participant_mentions": [],
            "graph_nodes": [
                {
                    "id": "company-b",
                    "conversation_id": 9,
                    "display_label": "B公司",
                    "raw_label": "B公司",
                    "type": "organization",
                }
            ],
            "matched_nodes": [],
        },
        message_understanding_result=_subject_reference_result(),
    )

    assert result["status"] == "success"
    assert result["route_intent"] == "DOSSIER_MEMORY"
    assert "当前会话" in result["response"]
    assert "B公司" in result["response"]
    assert result["execution_trace"]["memory_resolution"]["selected_scope"] == "current_conversation_graph"
    assert result["execution_trace"]["memory_resolution"]["candidate_count_by_scope"]["current_conversation_graph"] >= 1
    assert persisted["metadata"]["route_intent"] == "DOSSIER_MEMORY"
    assert trackers["legal_agent_calls"] == 0
    assert trackers["graph_calls"] == []


def test_subject_reference_unresolved_response_persists_pending_clarification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, persisted, trackers = _run_subject_reference_task(
        monkeypatch,
        question="这里的公司指的是哪家公司？",
        recent_messages=[],
        dossier_context={"id": 12, "title": "劳动争议案卷", "confirmed_participants": [], "participant_mentions": []},
        dossier_graph_context={"participants": [], "participant_mentions": [], "graph_nodes": [], "matched_nodes": []},
        message_understanding_result=_subject_reference_result(),
    )

    assert result["status"] == "success"
    assert result["route_intent"] == "DOSSIER_MEMORY"
    assert "当前还不能稳定确定" in result["response"]
    assert result["execution_trace"]["memory_resolution"]["unresolved_reason"] == "no_matching_subject_found"
    pending = persisted["metadata"]["pending_clarification"]
    assert pending["type"] == "subject_reference_resolution"
    assert pending["target_mention"] == "公司"
    assert pending["reference_type"] == "organization"
    assert trackers["legal_agent_calls"] == 0
    assert trackers["graph_calls"] == []


def test_pending_clarification_followup_is_routed_as_clarification_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, persisted, trackers = _run_subject_reference_task(
        monkeypatch,
        question="是B公司",
        recent_messages=[
            {"message_id": 11, "role": "user", "content": "这里的公司指的是哪家公司？", "metadata": {}},
            {
                "message_id": 12,
                "role": "assistant",
                "content": "当前还不能稳定确定“公司”具体指向哪个主体。",
                "metadata": {
                    "pending_clarification": {
                        "type": "subject_reference_resolution",
                        "target_mention": "公司",
                        "reference_type": "organization",
                        "previous_question": "这里的公司指的是哪家公司？",
                        "scope_attempted": [
                            "current_conversation_graph",
                            "current_conversation_messages",
                            "dossier_confirmed_participants",
                        ],
                        "expected_response": "entity_name_or_context",
                    }
                },
            },
        ],
        dossier_context={"id": 12, "title": "劳动争议案卷", "confirmed_participants": [], "participant_mentions": []},
        dossier_graph_context={"participants": [], "participant_mentions": [], "graph_nodes": [], "matched_nodes": []},
        message_understanding_result=None,
    )

    assert result["status"] == "success"
    assert result["message_understanding"]["intent"] == "clarification_response"
    assert result["execution_plan"]["primary_route"] == "DOSSIER_MEMORY"
    assert result["route_intent"] == "DOSSIER_MEMORY"
    assert result["response"] == "已理解，你这里说的“公司”指 B公司。"
    assert result["execution_trace"]["memory_resolution"]["selected_scope"] == "clarification_response"
    assert "pending_clarification" not in persisted["metadata"]
    assert trackers["legal_agent_calls"] == 0
    assert trackers["graph_calls"] == []


def test_pending_clarification_type_mismatch_does_not_become_new_subject_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, persisted, trackers = _run_subject_reference_task(
        monkeypatch,
        question="我，当事人",
        recent_messages=[
            {"message_id": 11, "role": "user", "content": "这里的公司指的是哪家公司？", "metadata": {}},
            {
                "message_id": 12,
                "role": "assistant",
                "content": "当前还不能稳定确定“公司”具体指向哪个主体。",
                "metadata": {
                    "pending_clarification": {
                        "type": "subject_reference_resolution",
                        "target_mention": "公司",
                        "reference_type": "organization",
                        "previous_question": "这里的公司指的是哪家公司？",
                        "scope_attempted": ["current_conversation_graph"],
                        "expected_response": "entity_name_or_context",
                    }
                },
            },
        ],
        dossier_context={"id": 12, "title": "劳动争议案卷", "confirmed_participants": [], "participant_mentions": []},
        dossier_graph_context={"participants": [], "participant_mentions": [], "graph_nodes": [], "matched_nodes": []},
        message_understanding_result=None,
    )

    assert result["status"] == "success"
    assert result["message_understanding"]["intent"] == "clarification_response"
    assert result["route_intent"] == "DOSSIER_MEMORY"
    assert "你补充的是当事人本人" in result["response"]
    assert "公司" in result["response"]
    assert result["execution_trace"]["memory_resolution"]["selected_scope"] == "clarification_response"
    assert result["execution_trace"]["memory_resolution"]["unresolved_reason"] == "clarification_type_mismatch"
    assert persisted["metadata"]["pending_clarification"]["target_mention"] == "公司"
    assert trackers["legal_agent_calls"] == 0
    assert trackers["graph_calls"] == []
