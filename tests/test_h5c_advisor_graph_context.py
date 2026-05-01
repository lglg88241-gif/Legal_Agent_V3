from __future__ import annotations

import asyncio
import importlib
import sys
from types import ModuleType

import pytest

from app.agent.nodes.advisor import (
    _format_dossier_graph_context_block,
    build_advisor_system_prompt,
)
from app.core.security import AuthenticatedUser
from app.logic.calculators import build_claim_payload
from app.models import Conversation
from app.schemas.case_state import CaseState
from app.services import dossier_graph_tool


class DummyDB:
    pass


def _sample_payload():
    return build_claim_payload(
        CaseState.from_text(
            "我在公司工作两年，公司一直没签劳动合同。",
            "公司突然通知我明天不用来了怎么办？",
            "",
        )
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


def test_should_query_dossier_graph_context_returns_false_without_dossier_id() -> None:
    assert dossier_graph_tool.should_query_dossier_graph_context(
        conversation_mode="TRACKED_CASE",
        dossier_id=None,
        user_message="这个案卷里之前提到的公司是谁？",
    ) is False


def test_should_query_dossier_graph_context_returns_false_for_legal_qa_mode() -> None:
    assert dossier_graph_tool.should_query_dossier_graph_context(
        conversation_mode="LEGAL_QA",
        dossier_id=12,
        user_message="这个案卷里之前提到的公司是谁？",
    ) is False


def test_should_query_dossier_graph_context_returns_false_for_explicit_article_query() -> None:
    assert dossier_graph_tool.should_query_dossier_graph_context(
        conversation_mode="TRACKED_CASE",
        dossier_id=12,
        user_message="劳动合同法第八十七条规定了什么？",
    ) is False


def test_should_include_dossier_context_for_advisor_skips_explicit_article_query() -> None:
    assert dossier_graph_tool.should_include_dossier_context_for_advisor(
        conversation_mode="TRACKED_CASE",
        dossier_id=12,
        user_message="劳动合同法第八十七条规定了什么？",
        persistence_consent="tracked",
    ) is False


def test_should_include_dossier_context_for_advisor_allows_case_applied_article_query() -> None:
    assert dossier_graph_tool.should_include_dossier_context_for_advisor(
        conversation_mode="TRACKED_CASE",
        dossier_id=12,
        user_message="结合我这个案子，劳动合同法第八十七条怎么适用？",
        persistence_consent="tracked",
    ) is True


def test_should_query_dossier_graph_context_allows_case_applied_article_query() -> None:
    assert dossier_graph_tool.should_query_dossier_graph_context(
        conversation_mode="TRACKED_CASE",
        dossier_id=12,
        user_message="结合我这个案子，劳动合同法第八十七条怎么适用？",
        persistence_consent="tracked",
    ) is True


def test_load_dossier_graph_context_for_conversation_calls_query_for_memory_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = Conversation(
        id=51,
        user_id=7,
        dossier_id=12,
        session_id="session-51",
        title="Tracked",
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )
    captured: dict[str, object] = {}

    async def fake_get_conversation_for_user(db, user_id: int, conversation_id: int):
        return conversation

    async def fake_query_dossier_graph_context(
        db,
        *,
        current_user,
        conversation_id: int,
        dossier_id: int,
        query: str,
        focus_participant_id=None,
        limit: int = 20,
    ):
        captured["user_id"] = current_user.user_id
        captured["conversation_id"] = conversation_id
        captured["dossier_id"] = dossier_id
        captured["query"] = query
        captured["limit"] = limit
        return {
            "status": "success",
            "source": "graph_context",
            "legal_basis": False,
            "scope": {
                "user_id": current_user.user_id,
                "dossier_id": dossier_id,
                "conversation_id": conversation_id,
            },
            "stats": {"conversation_count": 1, "node_count": 1, "edge_count": 0},
            "participants": [],
            "participant_mentions": [],
            "matched_nodes": [],
            "matched_edges": [],
            "warnings": [],
            "notes": ["graph_context 仅代表案卷结构化背景，不构成法律依据。"],
            "truncated": False,
        }

    monkeypatch.setattr(
        dossier_graph_tool,
        "get_conversation_for_user",
        fake_get_conversation_for_user,
    )
    monkeypatch.setattr(
        dossier_graph_tool,
        "query_dossier_graph_context",
        fake_query_dossier_graph_context,
    )

    result = asyncio.run(
        dossier_graph_tool.load_dossier_graph_context_for_conversation(
            DummyDB(),
            current_user=AuthenticatedUser(user_id=7, username="owner"),
            conversation_id=51,
            user_message="这个案卷里之前提到的公司是谁？",
        )
    )

    assert result is not None
    assert captured == {
        "user_id": 7,
        "conversation_id": 51,
        "dossier_id": 12,
        "query": "这个案卷里之前提到的公司是谁？",
        "limit": 12,
    }


def test_load_dossier_graph_context_for_conversation_skips_query_for_non_memory_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = Conversation(
        id=61,
        user_id=7,
        dossier_id=12,
        session_id="session-61",
        title="Tracked",
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )

    async def fake_get_conversation_for_user(db, user_id: int, conversation_id: int):
        return conversation

    async def fake_query_dossier_graph_context(*args, **kwargs):
        raise AssertionError("query_dossier_graph_context should not be called")

    monkeypatch.setattr(
        dossier_graph_tool,
        "get_conversation_for_user",
        fake_get_conversation_for_user,
    )
    monkeypatch.setattr(
        dossier_graph_tool,
        "query_dossier_graph_context",
        fake_query_dossier_graph_context,
    )

    result = asyncio.run(
        dossier_graph_tool.load_dossier_graph_context_for_conversation(
            DummyDB(),
            current_user=AuthenticatedUser(user_id=7, username="owner"),
            conversation_id=61,
            user_message="劳动合同法第八十七条规定了什么？",
        )
    )

    assert result is None


def test_load_controlled_dossier_graph_context_async_swallow_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    celery_module = _load_celery_module()

    async def fake_load_dossier_graph_context_for_conversation(*args, **kwargs):
        raise RuntimeError("neo4j unavailable")

    monkeypatch.setattr(
        celery_module,
        "load_dossier_graph_context_for_conversation",
        fake_load_dossier_graph_context_for_conversation,
    )

    result = asyncio.run(
        celery_module._load_controlled_dossier_graph_context_async(
            DummyDB(),
            user_id=7,
            conversation_id=81,
            user_message="这个案卷里之前提到的公司是谁？",
        )
    )

    assert result is None


def test_build_advisor_system_prompt_includes_dossier_graph_context_section() -> None:
    block = _format_dossier_graph_context_block(
        {
            "status": "success",
            "source": "graph_context",
            "legal_basis": False,
            "query_type": "participant",
            "query": "这个案卷里之前提到的公司是谁？",
            "scope": {"user_id": 7, "dossier_id": 12, "conversation_id": 88},
            "stats": {"conversation_count": 2, "node_count": 3, "edge_count": 1},
            "participants": [
                {
                    "label": "A公司",
                    "role": "用人单位",
                    "party_side": "opponent",
                    "entity_type": "organization",
                }
            ],
            "participant_mentions": [
                {"raw_label": "公司", "participant_label": "A公司", "source": "graph"}
            ],
            "matched_nodes": [
                {
                    "display_label": "A公司",
                    "raw_label": "公司",
                    "type": "organization",
                    "conversation_id": 88,
                }
            ],
            "matched_edges": [
                {"source": "node-company", "target": "node-event", "label": "通知解除"}
            ],
            "warnings": [],
            "notes": ["graph_context 仅代表案卷结构化背景，不构成法律依据。"],
            "truncated": False,
        }
    )

    prompt = build_advisor_system_prompt(
        case_summary="当前会话摘要",
        graph_context="",
        rag_context="《劳动合同法》第四十七条：经济补偿按工作年限计算。",
        payload=_sample_payload(),
        question="结合这个案卷，我和公司之间目前有哪些关键事实？",
        dossier_graph_context=block,
    )

    assert "<dossier_graph_context>" in prompt
    assert "案卷图谱查询结果（仅作案情背景，不是法律依据）" in prompt
    assert "legal_basis=false" in prompt
    assert "confirmed_participants:" in prompt
    assert "A公司" in prompt
    assert "不能作为法条或案例来源引用" in prompt
    assert "根据你当前案卷中已记录/已确认的信息" in prompt


def test_ask_agent_task_passes_dossier_graph_context_to_legal_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    celery_module = _load_celery_module()
    captured: dict[str, object] = {}

    async def fake_run_task_async(conversation_id: int, user_id: int, current_question: str | None = None):
        captured["current_question"] = current_question
        return {
            "chat_history": "",
            "case_summary": "旧摘要",
            "graph_memory": "",
            "saved_case_state": {"monthly_salary": 12000},
            "persistence_consent": "tracked",
            "conversation_mode": "TRACKED_CASE",
            "dossier_id": 12,
            "dossier_context": {"id": 12, "title": "劳动争议案卷"},
            "dossier_graph_summary": "",
            "dossier_graph_context": {
                "status": "success",
                "source": "graph_context",
                "legal_basis": False,
                "scope": {"user_id": 1, "dossier_id": 12, "conversation_id": 9},
                "stats": {"conversation_count": 1, "node_count": 1, "edge_count": 0},
                "participants": [],
                "participant_mentions": [],
                "matched_nodes": [],
                "matched_edges": [],
                "warnings": [],
                "notes": ["graph_context 仅代表案卷结构化背景，不构成法律依据。"],
                "truncated": False,
            },
        }

    class FakeLegalAgent:
        async def ask(self, *args, **kwargs):
            captured["dossier_graph_context"] = kwargs.get("dossier_graph_context")
            return {
                "response": "分析结果",
                "sources": [],
                "route_intent": "GENERAL_CONSULT",
                "case_summary": "",
            }

    async def fake_persist_dialog_turn_async(**kwargs):
        return {"user_message_id": 11, "assistant_message_id": 12}

    monkeypatch.setattr(celery_module, "_run_task_async", fake_run_task_async)
    monkeypatch.setattr(celery_module, "legal_agent", FakeLegalAgent())
    monkeypatch.setattr(celery_module, "_persist_dialog_turn_async", fake_persist_dialog_turn_async)

    result = celery_module.ask_agent_task.run(
        1,
        9,
        "session-1",
        "这个案卷里之前提到的公司是谁？",
        True,
        True,
    )

    assert result["status"] == "success"
    assert captured["current_question"] == "这个案卷里之前提到的公司是谁？"
    assert isinstance(captured["dossier_graph_context"], dict)
    assert captured["dossier_graph_context"]["source"] == "graph_context"
    assert captured["dossier_graph_context"]["legal_basis"] is False


def test_ask_agent_task_skips_dossier_context_for_explicit_law_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    celery_module = _load_celery_module()
    captured: dict[str, object] = {}

    async def fake_run_task_async(conversation_id: int, user_id: int, current_question: str | None = None):
        return {
            "chat_history": "",
            "case_summary": "",
            "graph_memory": "",
            "saved_case_state": None,
            "persistence_consent": "tracked",
            "conversation_mode": "TRACKED_CASE",
            "dossier_id": 12,
            "dossier_context": {"id": 12, "title": "劳动争议案卷", "summary": "A公司解除劳动合同"},
            "dossier_graph_summary": "<dossier_graph_summary>A公司</dossier_graph_summary>",
            "dossier_graph_context": {
                "status": "success",
                "source": "graph_context",
                "legal_basis": False,
                "scope": {"user_id": 1, "dossier_id": 12, "conversation_id": 9},
                "stats": {"conversation_count": 1, "node_count": 1, "edge_count": 0},
                "participants": [],
                "participant_mentions": [],
                "matched_nodes": [],
                "matched_edges": [],
                "warnings": [],
                "notes": ["graph_context 仅代表案卷结构化背景，不构成法律依据。"],
                "truncated": False,
            },
        }

    class FakeLegalAgent:
        async def ask(self, *args, **kwargs):
            captured["dossier_context"] = kwargs.get("dossier_context")
            captured["dossier_graph_summary"] = kwargs.get("dossier_graph_summary")
            captured["dossier_graph_context"] = kwargs.get("dossier_graph_context")
            return {
                "response": "第八十七条规定违法解除时应支付赔偿金。",
                "sources": ["law-db"],
                "route_intent": "GENERAL_CONSULT",
                "case_summary": "",
            }

    async def fake_persist_dialog_turn_async(**kwargs):
        return {"user_message_id": 11, "assistant_message_id": 12}

    monkeypatch.setattr(celery_module, "_run_task_async", fake_run_task_async)
    monkeypatch.setattr(celery_module, "legal_agent", FakeLegalAgent())
    monkeypatch.setattr(celery_module, "_persist_dialog_turn_async", fake_persist_dialog_turn_async)

    result = celery_module.ask_agent_task.run(
        1,
        9,
        "session-1",
        "劳动合同法第八十七条规定了什么？",
        True,
        True,
    )

    assert result["status"] == "success"
    assert captured["dossier_context"] is None
    assert captured["dossier_graph_summary"] is None
    assert captured["dossier_graph_context"] is None


def test_ask_agent_task_keeps_dossier_context_for_case_applied_law_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    celery_module = _load_celery_module()
    captured: dict[str, object] = {}

    async def fake_run_task_async(conversation_id: int, user_id: int, current_question: str | None = None):
        return {
            "chat_history": "",
            "case_summary": "",
            "graph_memory": "",
            "saved_case_state": None,
            "persistence_consent": "tracked",
            "conversation_mode": "TRACKED_CASE",
            "dossier_id": 12,
            "dossier_context": {"id": 12, "title": "劳动争议案卷", "summary": "A公司解除劳动合同"},
            "dossier_graph_summary": "<dossier_graph_summary>A公司</dossier_graph_summary>",
            "dossier_graph_context": None,
        }

    class FakeLegalAgent:
        async def ask(self, *args, **kwargs):
            captured["dossier_context"] = kwargs.get("dossier_context")
            captured["dossier_graph_summary"] = kwargs.get("dossier_graph_summary")
            return {
                "response": "结合当前案情，可以主张违法解除赔偿。",
                "sources": ["law-db"],
                "route_intent": "GENERAL_CONSULT",
                "case_summary": "",
            }

    async def fake_persist_dialog_turn_async(**kwargs):
        return {"user_message_id": 11, "assistant_message_id": 12}

    monkeypatch.setattr(celery_module, "_run_task_async", fake_run_task_async)
    monkeypatch.setattr(celery_module, "legal_agent", FakeLegalAgent())
    monkeypatch.setattr(celery_module, "_persist_dialog_turn_async", fake_persist_dialog_turn_async)

    result = celery_module.ask_agent_task.run(
        1,
        9,
        "session-1",
        "结合我这个案子，劳动合同法第八十七条怎么适用？",
        True,
        True,
    )

    assert result["status"] == "success"
    assert isinstance(captured["dossier_context"], dict)
    assert captured["dossier_graph_summary"] == "<dossier_graph_summary>A公司</dossier_graph_summary>"
