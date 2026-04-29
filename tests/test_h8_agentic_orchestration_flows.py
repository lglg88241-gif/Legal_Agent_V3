from __future__ import annotations

import importlib
import json
import sys
from types import ModuleType
from typing import Any


def _legal_question_result() -> dict[str, object]:
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
        "reason": "Current message is a legal question.",
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

    fake_redis_mgr = ModuleType("app.memory.redis_mgr")
    fake_redis_mgr.get_redis_saver_for_current_loop = lambda: None
    sys.modules["app.memory.redis_mgr"] = fake_redis_mgr


def _load_celery_module():
    _install_celery_import_stubs()
    sys.modules.pop("app.celery_app", None)
    return importlib.import_module("app.celery_app")


def test_exact_law_question_uses_exact_lookup_without_case_writes(monkeypatch) -> None:
    celery_module = _load_celery_module()
    import app.services.agentic_tool_executor as tool_executor_module
    tools_module = importlib.import_module("app.agent.tools")

    persisted: dict[str, Any] = {}
    trackers = {"legal_agent_calls": 0, "search_calls": 0}

    async def fake_run_task_async(conversation_id: int, user_id: int, current_question: str | None = None):
        return {
            "chat_history": "",
            "latest_user_input": current_question,
            "recent_messages": [],
            "case_summary": "旧摘要",
            "graph_memory": "",
            "saved_case_state": None,
            "persistence_consent": "tracked",
            "conversation_mode": "TRACKED_CASE",
            "dossier_id": None,
            "dossier_context": None,
            "dossier_graph_summary": "",
            "dossier_graph_context": None,
        }

    class FakeLegalAgent:
        async def ask(self, *args, **kwargs):
            trackers["legal_agent_calls"] += 1
            return {
                "response": "《劳动合同法》第八十七条主要规定违法解除或者终止劳动合同的赔偿责任。",
                "sources": [],
                "route_intent": "GENERAL_CONSULT",
                "case_summary": "",
                "confidence_score": 0.88,
            }

    async def fake_understand_message_with_agent(**kwargs):
        return dict(_legal_question_result())

    async def fake_persist_dialog_turn_async(**kwargs):
        persisted.update(kwargs)
        return {"user_message_id": 201, "assistant_message_id": 202}

    def fake_search_law_articles(query: str, chat_history: str = "") -> str:
        trackers["search_calls"] += 1
        return json.dumps(
            [
                {
                    "page_content": "第八十七条 用人单位违法解除或者终止劳动合同的，应当依照本法规定向劳动者支付赔偿金。",
                    "metadata": {"_exact_lookup": True, "law_name": "劳动合同法", "article": "第八十七条"},
                }
            ],
            ensure_ascii=False,
        )

    class FailIfGraphUpdateCalled:
        @staticmethod
        def delay(conversation_id: int, user_id: int, message_id: int):
            raise AssertionError("legal question must not schedule graph update")

    monkeypatch.setattr(celery_module, "_run_task_async", fake_run_task_async)
    monkeypatch.setattr(celery_module, "legal_agent", FakeLegalAgent())
    monkeypatch.setattr(celery_module, "understand_message_with_agent", fake_understand_message_with_agent)
    monkeypatch.setattr(celery_module, "_persist_dialog_turn_async", fake_persist_dialog_turn_async)
    monkeypatch.setattr(celery_module, "update_graph_task", FailIfGraphUpdateCalled())
    monkeypatch.setattr(tool_executor_module, "search_law_articles", fake_search_law_articles, raising=False)
    monkeypatch.setattr(tools_module, "search_law_articles", fake_search_law_articles, raising=False)

    result = celery_module.ask_agent_task.run(
        1,
        9,
        "session-1",
        "劳动合同法第八十七条规定了什么？",
        True,
        True,
    )

    trace = result["execution_trace"]
    tool_names = [item["name"] for item in trace["tool_calls"]]

    assert result["status"] == "success"
    assert result["route_intent"] == "LEGAL_QA"
    assert trackers["legal_agent_calls"] == 1
    assert trackers["search_calls"] >= 1
    assert "exact_law_lookup" in tool_names
    assert "legal_answer_compose" in tool_names
    assert trace["agent_plan"]["specialist"] == "legal_qa_agent"
    assert trace["writes"]["requested"] == []
    assert trace["writes"]["applied"] == []
    assert trace["legacy_fallback"]["used"] is False
    assert trace["final_basis"]["source"] == "legal_agent"
    assert persisted["case_summary"] == ""
