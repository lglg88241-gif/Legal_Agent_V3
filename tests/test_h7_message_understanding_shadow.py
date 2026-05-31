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

    async def _get_case_graph(*args, **kwargs):
        return {"query": "", "nodes": [], "edges": []}

    fake_graph_extractor.format_graph_memory = _format_graph_memory
    fake_graph_extractor.refresh_case_graph = _refresh_case_graph
    fake_graph_extractor.get_case_graph = _get_case_graph
    sys.modules["app.services.graph_extractor"] = fake_graph_extractor

    fake_redis_mgr = ModuleType("app.memory.redis_mgr")
    fake_redis_mgr.get_redis_saver_for_current_loop = lambda: None
    sys.modules["app.memory.redis_mgr"] = fake_redis_mgr


def _load_celery_module():
    _install_celery_import_stubs()
    sys.modules.pop("app.celery_app", None)
    return importlib.import_module("app.celery_app")


def test_ask_agent_task_records_message_understanding_shadow_result_for_non_subject_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    celery_module = _load_celery_module()
    persisted: dict[str, object] = {}

    async def fake_run_task_async(conversation_id: int, user_id: int, current_question: str | None = None):
        return {
            "chat_history": "",
            "latest_user_input": current_question,
            "recent_messages": [
                {
                    "message_id": 41,
                    "role": "user",
                    "content": "劳动合同法第八十七条规定了什么？",
                }
            ],
            "case_summary": "旧摘要",
            "graph_memory": "",
            "saved_case_state": None,
            "persistence_consent": "tracked",
            "conversation_mode": "TRACKED_CASE",
            "dossier_id": 12,
            "dossier_context": {"id": 12, "title": "劳动争议案卷"},
            "dossier_graph_summary": "",
            "dossier_graph_context": None,
        }

    class FakeLegalAgent:
        async def ask(self, *args, **kwargs):
            return {
                "response": "主链路回答",
                "sources": [],
                "route_intent": "GENERAL_CONSULT",
                "case_summary": "",
                "confidence_score": 0.82,
            }

    async def fake_understand_message_with_agent(**kwargs):
        assert kwargs["message"] == "劳动合同法第八十七条规定了什么？"
        assert kwargs["conversation_mode"] == "TRACKED_CASE"
        assert kwargs["dossier_id"] == 12
        return {
            "source": "message_understanding_agent",
            "intent": "legal_question",
            "route_hint": "LEGAL_QA",
            "needs_legal_rag": True,
            "needs_dossier_graph_context": False,
            "needs_structured_persistence": False,
            "needs_graph_update": False,
            "subject_reference": None,
            "fact_update": None,
            "previous_fact_confirmation": None,
            "advice_question": None,
            "legal_question": {
                "enabled": True,
                "question": "劳动合同法第八十七条规定了什么？",
            },
            "confidence": "high",
            "reason": "Shadow classified this as a legal question.",
            "provider": "openai",
            "model": "gpt-5.1",
            "fallback_reason": None,
            "trace": {"steps": ["shadow_mode"], "warnings": []},
        }

    async def fake_persist_dialog_turn_async(**kwargs):
        persisted.update(kwargs)
        return {"user_message_id": 201, "assistant_message_id": 202}

    monkeypatch.setattr(celery_module, "_run_task_async", fake_run_task_async)
    monkeypatch.setattr(celery_module, "legal_agent", FakeLegalAgent())
    monkeypatch.setattr(celery_module, "understand_message_with_agent", fake_understand_message_with_agent)
    monkeypatch.setattr(celery_module, "_persist_dialog_turn_async", fake_persist_dialog_turn_async)

    result = celery_module.ask_agent_task.run(
        5,
        9,
        "session-9",
        "劳动合同法第八十七条规定了什么？",
        True,
        True,
    )

    assert result["status"] == "success"
    assert result["route_intent"] == "LEGAL_QA"
    assert result["response"] == "主链路回答"
    assert result["message_understanding"]["intent"] == "legal_question"
    assert result["message_understanding"]["route_hint"] == "LEGAL_QA"
    assert result["message_understanding"]["provider"] == "openai"
    assert persisted["metadata"]["message_understanding"]["intent"] == "legal_question"
    assert persisted["metadata"]["route_intent"] == "LEGAL_QA"
    action_tools = [item.get("tool") for item in result["agent_actions"]]
    assert "message_understanding_shadow" not in action_tools
    assert "tool_router" not in action_tools
    assert "retrieval_tools" not in action_tools
    assert "legal_advisor" not in action_tools
    assert "legal_answer_compose" in action_tools
    assert result["execution_trace"]["message_understanding"]["intent"] == "legal_question"


def test_ask_agent_task_greeting_does_not_enter_case_update_or_retrieval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    celery_module = _load_celery_module()
    persisted: dict[str, object] = {}

    async def fake_run_task_async(conversation_id: int, user_id: int, current_question: str | None = None):
        return {
            "chat_history": "",
            "latest_user_input": current_question,
            "recent_messages": [],
            "case_summary": "",
            "graph_memory": "",
            "saved_case_state": None,
            "persistence_consent": "tracked",
            "conversation_mode": "LEGAL_QA",
            "dossier_id": 12,
            "dossier_context": {"id": 12, "title": "case"},
            "dossier_graph_summary": "",
            "dossier_graph_context": None,
        }

    class FakeLegalAgent:
        async def ask(self, *args, **kwargs):
            raise AssertionError("greeting must not call legal_agent.ask")

    async def fake_understand_message_with_agent(**kwargs):
        assert kwargs["message"] == "\u4f60\u597d"
        assert kwargs["conversation_mode"] == "LEGAL_QA"
        return {
            "source": "message_understanding_agent",
            "intent": "unknown",
            "route_hint": "GREETING",
            "needs_legal_rag": False,
            "needs_dossier_graph_context": False,
            "needs_structured_persistence": False,
            "needs_graph_update": False,
            "subject_reference": None,
            "fact_update": None,
            "previous_fact_confirmation": None,
            "advice_question": None,
            "legal_question": None,
            "confidence": "high",
            "reason": "Deterministic greeting/chitchat override.",
            "provider": "openai",
            "model": "gpt-5.1",
            "fallback_reason": None,
            "trace": {"steps": ["normalize_result"], "warnings": ["override_greeting_chitchat"]},
        }

    async def fake_persist_dialog_turn_async(**kwargs):
        persisted.update(kwargs)
        return {"user_message_id": 401, "assistant_message_id": 402}

    monkeypatch.setattr(celery_module, "_run_task_async", fake_run_task_async)
    monkeypatch.setattr(celery_module, "legal_agent", FakeLegalAgent())
    monkeypatch.setattr(celery_module, "understand_message_with_agent", fake_understand_message_with_agent)
    monkeypatch.setattr(celery_module, "_persist_dialog_turn_async", fake_persist_dialog_turn_async)

    result = celery_module.ask_agent_task.run(
        5,
        9,
        "session-9",
        "\u4f60\u597d",
        True,
        True,
    )

    assert result["status"] == "success"
    assert result["route_intent"] == "GREETING"
    assert result["message_understanding"]["route_hint"] == "GREETING"
    assert persisted == {}
    action_tools = [item.get("tool") for item in result["agent_actions"]]
    assert action_tools == ["tool_router", "greeting"]
    assert "fact_updater" not in action_tools
    assert "retrieval_tools" not in action_tools
    assert "reflector" not in action_tools


def test_ask_agent_task_keeps_main_flow_when_message_understanding_shadow_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    celery_module = _load_celery_module()
    persisted: dict[str, object] = {}

    async def fake_run_task_async(conversation_id: int, user_id: int, current_question: str | None = None):
        return {
            "chat_history": "",
            "latest_user_input": current_question,
            "recent_messages": [],
            "case_summary": "",
            "graph_memory": "",
            "saved_case_state": None,
            "persistence_consent": "tracked",
            "conversation_mode": "TRACKED_CASE",
            "dossier_id": 12,
            "dossier_context": {"id": 12, "title": "劳动争议案卷"},
            "dossier_graph_summary": "",
            "dossier_graph_context": None,
        }

    class FakeLegalAgent:
        async def ask(self, *args, **kwargs):
            return {
                "response": "旧链路正常返回",
                "sources": ["law-db"],
                "route_intent": "GENERAL_CONSULT",
                "case_summary": "",
            }

    async def fake_understand_message_with_agent(**kwargs):
        raise RuntimeError("shadow timeout")

    async def fake_persist_dialog_turn_async(**kwargs):
        persisted.update(kwargs)
        return {"user_message_id": 301, "assistant_message_id": 302}

    monkeypatch.setattr(celery_module, "_run_task_async", fake_run_task_async)
    monkeypatch.setattr(celery_module, "legal_agent", FakeLegalAgent())
    monkeypatch.setattr(celery_module, "understand_message_with_agent", fake_understand_message_with_agent)
    monkeypatch.setattr(celery_module, "_persist_dialog_turn_async", fake_persist_dialog_turn_async)

    result = celery_module.ask_agent_task.run(
        5,
        9,
        "session-9",
        "我确认这个事实",
        True,
        True,
    )

    assert result["status"] == "success"
    assert result["route_intent"] == "GENERAL_CONSULT"
    assert result["response"] == "旧链路正常返回"
    assert result["message_understanding"]["provider"] == "fallback"
    assert result["message_understanding"]["fallback_reason"] == "shadow_call_failed"
    assert "shadow_call_failed" in result["message_understanding"]["trace"]["warnings"][0]
    assert persisted["metadata"]["message_understanding"]["fallback_reason"] == "shadow_call_failed"
    shadow_actions = [
        item for item in result["agent_actions"] if item.get("stage") == "message_understanding_shadow"
    ]
    assert len(shadow_actions) == 1
    assert shadow_actions[0]["status"] == "failed"
