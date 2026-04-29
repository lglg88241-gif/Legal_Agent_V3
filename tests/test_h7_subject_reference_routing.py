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

    fake_redis_mgr = ModuleType("app.memory.redis_mgr")
    fake_redis_mgr.get_redis_saver_for_current_loop = lambda: None
    sys.modules["app.memory.redis_mgr"] = fake_redis_mgr


def _load_celery_module():
    _install_celery_import_stubs()
    sys.modules.pop("app.celery_app", None)
    return importlib.import_module("app.celery_app")


def test_subject_reference_routes_through_dossier_memory_without_legal_rag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    celery_module = _load_celery_module()
    persisted: dict[str, object] = {}

    async def fake_run_task_async(conversation_id: int, user_id: int, current_question: str | None = None):
        return {
            "chat_history": "",
            "latest_user_input": current_question,
            "recent_messages": [
                {"message_id": 11, "role": "user", "content": "我和B公司签了劳动合同。"},
                {"message_id": 12, "role": "assistant", "content": "继续补充。"},
            ],
            "case_summary": "我和B公司存在劳动关系，并围绕社保缴纳发生争议。",
            "graph_memory": "",
            "saved_case_state": None,
            "persistence_consent": "tracked",
            "conversation_mode": "TRACKED_CASE",
            "dossier_id": 12,
            "dossier_context": {
                "id": 12,
                "title": "劳动争议案卷",
                "confirmed_participants": [
                    {
                        "id": 101,
                        "label": "B公司",
                        "role": "用人单位",
                        "party_side": "opponent",
                        "entity_type": "organization",
                    }
                ],
                "participant_mentions": [
                    {
                        "conversation_id": 9,
                        "participant_id": 101,
                        "raw_label": "公司",
                        "graph_node_id": "company-1",
                        "graph_node_label": "公司",
                        "graph_node_type": "organization",
                        "participant_label": "B公司",
                        "participant_entity_type": "organization",
                    }
                ],
            },
            "dossier_graph_summary": "",
            "dossier_graph_context": {
                "participants": [
                    {
                        "label": "B公司",
                        "role": "用人单位",
                        "entity_type": "organization",
                    }
                ],
                "participant_mentions": [
                    {
                        "raw_label": "公司",
                        "participant_label": "B公司",
                        "participant_entity_type": "organization",
                    }
                ],
                "matched_nodes": [
                    {
                        "display_label": "B公司",
                        "raw_label": "公司",
                        "type": "organization",
                    }
                ],
            },
        }

    class FailIfCalledLegalAgent:
        async def ask(self, *args, **kwargs):
            raise AssertionError("subject_reference should not call the normal legal_agent.ask path")

    async def fake_understand_message_with_agent(**kwargs):
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
            "trace": {"steps": ["build_request", "call_llm"], "warnings": []},
        }

    async def fake_persist_dialog_turn_async(**kwargs):
        persisted.update(kwargs)
        return {"user_message_id": 401, "assistant_message_id": 402}

    class FailIfGraphUpdateCalled:
        @staticmethod
        def delay(conversation_id: int, user_id: int, message_id: int) -> None:
            raise AssertionError("subject_reference should not trigger update_graph_task")

    monkeypatch.setattr(celery_module, "_run_task_async", fake_run_task_async)
    monkeypatch.setattr(celery_module, "legal_agent", FailIfCalledLegalAgent())
    monkeypatch.setattr(celery_module, "understand_message_with_agent", fake_understand_message_with_agent)
    monkeypatch.setattr(celery_module, "_persist_dialog_turn_async", fake_persist_dialog_turn_async)
    monkeypatch.setattr(celery_module, "update_graph_task", FailIfGraphUpdateCalled())

    result = celery_module.ask_agent_task.run(
        5,
        9,
        "session-9",
        "这里的公司指的是哪家公司？",
        True,
        True,
    )

    assert result["status"] == "success"
    assert result["route_intent"] == "DOSSIER_MEMORY"
    assert "B公司" in result["response"]
    assert "劳动法第七十二条" not in result["response"]
    assert "劳动合同法" not in result["response"]
    assert "案例" not in result["response"]
    assert "仲裁" not in result["response"]
    assert "下一步建议" not in result["response"]
    assert result["message_understanding"]["intent"] == "subject_reference"
    assert result["message_understanding"]["route_hint"] == "DOSSIER_MEMORY"
    assert persisted["case_summary"] == ""
    assert persisted["case_state_dict"] is None
    assert persisted["case_type"] is None
    assert persisted["metadata"]["message_understanding"]["intent"] == "subject_reference"
    assert persisted["metadata"]["route_intent"] == "DOSSIER_MEMORY"
    trace_steps = result["execution_trace"]["steps"]
    resolver_step = next(
        step for step in trace_steps if step["name"] == "dossier_memory_subject_reference"
    )
    assert resolver_step["details"]["selected_scope"] == "current_conversation_graph"

    action_summaries = [str(item.get("result_summary") or "") for item in result["agent_actions"]]
    assert any("subject_reference -> DOSSIER_MEMORY" in summary for summary in action_summaries)
    assert any("Dossier subject reference answered" in summary for summary in action_summaries)
    assert all("General consult route selected" not in summary for summary in action_summaries)
    assert all("Retrieval pipeline completed" not in summary for summary in action_summaries)


def test_subject_reference_falls_back_to_safe_memory_answer_when_entity_is_unclear(
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
            "dossier_context": {"id": 12, "title": "劳动争议案卷", "confirmed_participants": [], "participant_mentions": []},
            "dossier_graph_summary": "",
            "dossier_graph_context": {"participants": [], "participant_mentions": [], "matched_nodes": []},
        }

    class FailIfCalledLegalAgent:
        async def ask(self, *args, **kwargs):
            raise AssertionError("unclear subject_reference should still not call legal RAG")

    async def fake_understand_message_with_agent(**kwargs):
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
            "confidence": "medium",
            "reason": "Current message asks for dossier subject reference.",
            "provider": "openai",
            "model": "gpt-5.1",
            "fallback_reason": None,
            "trace": {"steps": ["build_request", "call_llm"], "warnings": []},
        }

    async def fake_persist_dialog_turn_async(**kwargs):
        persisted.update(kwargs)
        return {"user_message_id": 501, "assistant_message_id": 502}

    class FailIfGraphUpdateCalled:
        @staticmethod
        def delay(conversation_id: int, user_id: int, message_id: int) -> None:
            raise AssertionError("subject_reference fallback should not trigger update_graph_task")

    monkeypatch.setattr(celery_module, "_run_task_async", fake_run_task_async)
    monkeypatch.setattr(celery_module, "legal_agent", FailIfCalledLegalAgent())
    monkeypatch.setattr(celery_module, "understand_message_with_agent", fake_understand_message_with_agent)
    monkeypatch.setattr(celery_module, "_persist_dialog_turn_async", fake_persist_dialog_turn_async)
    monkeypatch.setattr(celery_module, "update_graph_task", FailIfGraphUpdateCalled())

    result = celery_module.ask_agent_task.run(
        5,
        9,
        "session-9",
        "这里的公司指的是哪家公司？",
        True,
        True,
    )

    assert result["status"] == "success"
    assert result["route_intent"] == "DOSSIER_MEMORY"
    assert "还不能稳定确定" in result["response"]
    assert "劳动合同法" not in result["response"]
    assert persisted["metadata"]["message_understanding"]["intent"] == "subject_reference"
