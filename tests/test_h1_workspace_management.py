from __future__ import annotations

import asyncio
import importlib
import sys
from types import ModuleType

import pytest

from app.core.security import AuthenticatedUser
from app.models import Conversation


def _install_backend_import_stubs() -> None:
    fake_openai = ModuleType("openai")

    class FakeAsyncOpenAI:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def close(self) -> None:
            return None

    fake_openai.AsyncOpenAI = FakeAsyncOpenAI
    sys.modules["openai"] = fake_openai

    fake_llm_service = ModuleType("app.services.llm_service")

    class FakeLLMService:
        async def async_generate(self, *args, **kwargs) -> str:
            return ""

    fake_llm_service.llm_service = FakeLLMService()
    sys.modules["app.services.llm_service"] = fake_llm_service

    fake_graph_extractor = ModuleType("app.services.graph_extractor")

    async def _delete_case_graph(*args, **kwargs):
        return {"success": True, "error": None}

    async def _get_case_graph(*args, **kwargs):
        return {}

    async def _format_graph_memory(*args, **kwargs) -> str:
        return ""

    async def _refresh_case_graph(*args, **kwargs):
        return {"actions_applied": 0, "actions": []}

    fake_graph_extractor.delete_case_graph = _delete_case_graph
    fake_graph_extractor.get_case_graph = _get_case_graph
    fake_graph_extractor.format_graph_memory = _format_graph_memory
    fake_graph_extractor.refresh_case_graph = _refresh_case_graph
    sys.modules["app.services.graph_extractor"] = fake_graph_extractor

    fake_legal_agent = ModuleType("app.services.legal_agent")

    class FakeLegalAgent:
        async def ask(self, *args, **kwargs):
            return {"response": "ok", "sources": [], "route_intent": "GENERAL_CONSULT"}

    fake_legal_agent.legal_agent = FakeLegalAgent()
    sys.modules["app.services.legal_agent"] = fake_legal_agent

    fake_redis_mgr = ModuleType("app.memory.redis_mgr")
    fake_redis_mgr.get_redis_saver_for_current_loop = lambda: None
    sys.modules["app.memory.redis_mgr"] = fake_redis_mgr


def _load_conversations_api_module():
    _install_backend_import_stubs()
    sys.modules.pop("app.api.conversations", None)
    return importlib.import_module("app.api.conversations")


def test_search_cases_uses_current_user_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    conversations_api = _load_conversations_api_module()
    captured: dict[str, object] = {}

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_search_user_cases(db, user_id: int, case_type=None, keyword=None, limit=10):
        captured["user_id"] = user_id
        captured["case_type"] = case_type
        captured["keyword"] = keyword
        captured["limit"] = limit
        return [
            Conversation(
                id=31,
                user_id=user_id,
                session_id="session-31",
                title="salary dispute",
                case_summary="unpaid wages for three months",
                case_type="labor dispute",
            )
        ]

    monkeypatch.setattr(conversations_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(conversations_api, "search_user_cases", fake_search_user_cases)

    result = asyncio.run(
        conversations_api.search_cases(
            case_type=None,
            keyword="salary",
            limit=10,
            current_user=AuthenticatedUser(user_id=9, username="alice"),
            db=object(),
        )
    )

    assert captured == {
        "user_id": 9,
        "case_type": None,
        "keyword": "salary",
        "limit": 10,
    }
    assert result["status"] == "success"
    assert result["conversations"][0]["user_id"] == 9
    assert result["cases"][0]["id"] == 31


def test_batch_delete_skips_non_owned_conversations(monkeypatch: pytest.MonkeyPatch) -> None:
    conversations_api = _load_conversations_api_module()
    deleted_ids: list[int] = []

    class DummyDB:
        async def commit(self) -> None:
            return None

        async def rollback(self) -> None:
            return None

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_list_user_conversations_by_ids(db, user_id: int, conversation_ids: list[int]):
        assert user_id == 5
        assert conversation_ids == [1, 2]
        return [
            Conversation(
                id=1,
                user_id=user_id,
                session_id="session-1",
                title="owned",
            )
        ]

    async def fake_delete_conversation_record(db, conversation_id: int) -> None:
        deleted_ids.append(conversation_id)

    async def fake_delete_case_graph(conversation_id: int):
        return {"success": True, "error": None}

    monkeypatch.setattr(conversations_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(conversations_api, "list_user_conversations_by_ids", fake_list_user_conversations_by_ids)
    monkeypatch.setattr(conversations_api, "delete_conversation_record", fake_delete_conversation_record)
    monkeypatch.setattr(conversations_api, "delete_case_graph", fake_delete_case_graph)

    result = asyncio.run(
        conversations_api.batch_delete_conversations(
            payload=conversations_api.ConversationBatchDeleteRequest(conversation_ids=[1, 2]),
            current_user=AuthenticatedUser(user_id=5, username="owner"),
            db=DummyDB(),
        )
    )

    assert deleted_ids == [1]
    assert result == {
        "status": "success",
        "success": True,
        "deleted": [1],
        "failed": [],
        "skipped": [2],
        "graph_warnings": [],
    }


def test_batch_delete_successfully_deletes_multiple_conversations(monkeypatch: pytest.MonkeyPatch) -> None:
    conversations_api = _load_conversations_api_module()
    deleted_ids: list[int] = []

    class DummyDB:
        async def commit(self) -> None:
            return None

        async def rollback(self) -> None:
            return None

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_list_user_conversations_by_ids(db, user_id: int, conversation_ids: list[int]):
        return [
            Conversation(id=1, user_id=user_id, session_id="session-1", title="one"),
            Conversation(id=2, user_id=user_id, session_id="session-2", title="two"),
        ]

    async def fake_delete_conversation_record(db, conversation_id: int) -> None:
        deleted_ids.append(conversation_id)

    async def fake_delete_case_graph(conversation_id: int):
        return {"success": True, "error": None}

    monkeypatch.setattr(conversations_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(conversations_api, "list_user_conversations_by_ids", fake_list_user_conversations_by_ids)
    monkeypatch.setattr(conversations_api, "delete_conversation_record", fake_delete_conversation_record)
    monkeypatch.setattr(conversations_api, "delete_case_graph", fake_delete_case_graph)

    result = asyncio.run(
        conversations_api.batch_delete_conversations(
            payload=conversations_api.ConversationBatchDeleteRequest(conversation_ids=[1, 2]),
            current_user=AuthenticatedUser(user_id=7, username="owner"),
            db=DummyDB(),
        )
    )

    assert deleted_ids == [1, 2]
    assert result["deleted"] == [1, 2]
    assert result["failed"] == []
    assert result["skipped"] == []
    assert result["graph_warnings"] == []


def test_batch_delete_returns_graph_cleanup_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    conversations_api = _load_conversations_api_module()

    class DummyDB:
        async def commit(self) -> None:
            return None

        async def rollback(self) -> None:
            return None

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_list_user_conversations_by_ids(db, user_id: int, conversation_ids: list[int]):
        return [
            Conversation(id=8, user_id=user_id, session_id="session-8", title="owned"),
        ]

    async def fake_delete_conversation_record(db, conversation_id: int) -> None:
        return None

    async def fake_delete_case_graph(conversation_id: int):
        return {"success": False, "error": "neo4j down"}

    monkeypatch.setattr(conversations_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(conversations_api, "list_user_conversations_by_ids", fake_list_user_conversations_by_ids)
    monkeypatch.setattr(conversations_api, "delete_conversation_record", fake_delete_conversation_record)
    monkeypatch.setattr(conversations_api, "delete_case_graph", fake_delete_case_graph)

    result = asyncio.run(
        conversations_api.batch_delete_conversations(
            payload=conversations_api.ConversationBatchDeleteRequest(conversation_ids=[8]),
            current_user=AuthenticatedUser(user_id=6, username="owner"),
            db=DummyDB(),
        )
    )

    assert result["deleted"] == [8]
    assert result["failed"] == []
    assert result["skipped"] == []
    assert result["graph_warnings"] == [
        {
            "conversation_id": 8,
            "warning": "SQL data cleared but graph cleanup failed",
        }
    ]
