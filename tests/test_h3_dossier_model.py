from __future__ import annotations

import asyncio
import importlib
import sys
from types import ModuleType

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, inspect

from app.core.security import AuthenticatedUser
from app.models import Conversation, Dossier
from app.services.conversation_store import ensure_storage_schema, serialize_conversation


def _install_common_stubs() -> None:
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

    fake_graph_extractor.delete_case_graph = _delete_case_graph
    fake_graph_extractor.get_case_graph = _get_case_graph
    sys.modules["app.services.graph_extractor"] = fake_graph_extractor


def _load_conversations_api_module():
    _install_common_stubs()
    sys.modules.pop("app.api.conversations", None)
    module = importlib.import_module("app.api.conversations")
    sys.modules.pop("app.services.graph_extractor", None)
    sys.modules.pop("app.services.llm_service", None)
    return module


def _load_dossiers_api_module():
    sys.modules.pop("app.api.dossiers", None)
    return importlib.import_module("app.api.dossiers")


class DummyDB:
    def add(self, obj) -> None:
        return None

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def refresh(self, obj) -> None:
        return None


def test_serialize_conversation_includes_nullable_dossier_id() -> None:
    conversation = Conversation(
        id=11,
        user_id=7,
        dossier_id=None,
        session_id="session-11",
        title="test",
        conversation_mode="UNKNOWN",
        persistence_consent="unknown",
    )

    serialized = serialize_conversation(conversation)

    assert "dossier_id" in serialized
    assert serialized["dossier_id"] is None


def test_serialize_conversation_includes_bound_dossier_id() -> None:
    conversation = Conversation(
        id=12,
        user_id=7,
        dossier_id=5,
        session_id="session-12",
        title="tracked",
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )

    serialized = serialize_conversation(conversation)

    assert serialized["dossier_id"] == 5


def test_runtime_schema_creates_dossiers_table_and_conversation_dossier_id() -> None:
    async def _run() -> None:
        sync_engine = create_engine("sqlite:///:memory:")

        class FakeAsyncConnection:
            async def run_sync(self, fn):
                with sync_engine.begin() as sync_conn:
                    return fn(sync_conn)

        class FakeAsyncBegin:
            async def __aenter__(self):
                return FakeAsyncConnection()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class FakeAsyncEngine:
            def begin(self):
                return FakeAsyncBegin()

        engine = FakeAsyncEngine()
        try:
            await ensure_storage_schema(bind_engine=engine)

            with sync_engine.begin() as sync_conn:
                inspector = inspect(sync_conn)
                tables = set(inspector.get_table_names())
                assert "dossiers" in tables
                conversation_columns = {
                    column["name"] for column in inspector.get_columns("conversations")
                }
                dossier_columns = {
                    column["name"] for column in inspector.get_columns("dossiers")
                }
                assert "dossier_id" in conversation_columns
                assert {"title", "case_type", "summary", "status"} <= dossier_columns
        finally:
            sync_engine.dispose()

    asyncio.run(_run())


def test_create_dossier_returns_current_users_dossier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_create_dossier_record(db, user_id: int, *, title: str, case_type=None, summary=None, status="active"):
        return Dossier(
            id=3,
            user_id=user_id,
            title=title,
            case_type=case_type,
            summary=summary,
            status=status,
        )

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dossiers_api, "create_dossier_record", fake_create_dossier_record)

    result = asyncio.run(
        dossiers_api.create_dossier(
            payload=dossiers_api.DossierCreateRequest(
                title="劳动仲裁案卷",
                case_type="劳动争议",
                summary="违法解除与未签合同",
            ),
            current_user=AuthenticatedUser(user_id=9, username="alice"),
            db=DummyDB(),
        )
    )

    assert result["status"] == "success"
    assert result["dossier"]["user_id"] == 9
    assert result["dossier"]["title"] == "劳动仲裁案卷"
    assert result["dossier"]["status"] == "active"


def test_list_dossiers_uses_current_user_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()
    captured: dict[str, object] = {}

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_list_user_dossiers(db, user_id: int):
        captured["user_id"] = user_id
        return [
            Dossier(
                id=1,
                user_id=user_id,
                title="我的案卷",
                case_type="劳动争议",
                summary="summary",
                status="active",
            )
        ]

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dossiers_api, "list_user_dossiers", fake_list_user_dossiers)

    result = asyncio.run(
        dossiers_api.get_dossiers(
            current_user=AuthenticatedUser(user_id=5, username="owner"),
            db=DummyDB(),
        )
    )

    assert captured["user_id"] == 5
    assert len(result["dossiers"]) == 1
    assert result["dossiers"][0]["user_id"] == 5


def test_get_other_users_dossier_returns_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        return None

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dossiers_api, "get_dossier_for_user", fake_get_dossier_for_user)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            dossiers_api.get_dossier(
                dossier_id=7,
                current_user=AuthenticatedUser(user_id=1, username="owner"),
                db=DummyDB(),
            )
        )

    assert exc_info.value.status_code == 404


def test_update_own_dossier_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()
    dossier = Dossier(
        id=8,
        user_id=2,
        title="旧标题",
        case_type="劳动争议",
        summary="旧摘要",
        status="active",
    )

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        return dossier

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dossiers_api, "get_dossier_for_user", fake_get_dossier_for_user)

    result = asyncio.run(
        dossiers_api.update_dossier(
            dossier_id=8,
            payload=dossiers_api.DossierUpdateRequest(
                title="新标题",
                summary="新摘要",
                status="closed",
            ),
            current_user=AuthenticatedUser(user_id=2, username="owner"),
            db=DummyDB(),
        )
    )

    assert result["status"] == "success"
    assert result["dossier"]["title"] == "新标题"
    assert result["dossier"]["summary"] == "新摘要"
    assert result["dossier"]["status"] == "closed"


def test_delete_dossier_archives_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()
    dossier = Dossier(
        id=9,
        user_id=2,
        title="待归档案卷",
        case_type="劳动争议",
        summary="summary",
        status="active",
    )

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        return dossier

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dossiers_api, "get_dossier_for_user", fake_get_dossier_for_user)

    result = asyncio.run(
        dossiers_api.archive_dossier(
            dossier_id=9,
            current_user=AuthenticatedUser(user_id=2, username="owner"),
            db=DummyDB(),
        )
    )

    assert result["status"] == "success"
    assert result["dossier"]["status"] == "archived"


def test_attach_conversation_to_owned_dossier_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversations_api = _load_conversations_api_module()
    conversation = Conversation(
        id=4,
        user_id=3,
        dossier_id=None,
        session_id="session-4",
        title="tracked",
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )
    dossier = Dossier(
        id=12,
        user_id=3,
        title="案卷",
        case_type="劳动争议",
        summary="summary",
        status="active",
    )

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_conversation_for_user(db, user_id: int, conversation_id: int):
        return conversation

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        return dossier

    monkeypatch.setattr(conversations_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(conversations_api, "get_conversation_for_user", fake_get_conversation_for_user)
    monkeypatch.setattr(conversations_api, "get_dossier_for_user", fake_get_dossier_for_user)

    result = asyncio.run(
        conversations_api.attach_dossier(
            conversation_id=4,
            payload=conversations_api.ConversationAttachDossierRequest(dossier_id=12),
            current_user=AuthenticatedUser(user_id=3, username="owner"),
            db=DummyDB(),
        )
    )

    assert result["status"] == "success"
    assert result["conversation"]["dossier_id"] == 12


def test_attach_conversation_to_other_users_dossier_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversations_api = _load_conversations_api_module()
    conversation = Conversation(
        id=5,
        user_id=3,
        dossier_id=None,
        session_id="session-5",
        title="tracked",
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_conversation_for_user(db, user_id: int, conversation_id: int):
        return conversation

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        return None

    monkeypatch.setattr(conversations_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(conversations_api, "get_conversation_for_user", fake_get_conversation_for_user)
    monkeypatch.setattr(conversations_api, "get_dossier_for_user", fake_get_dossier_for_user)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            conversations_api.attach_dossier(
                conversation_id=5,
                payload=conversations_api.ConversationAttachDossierRequest(dossier_id=99),
                current_user=AuthenticatedUser(user_id=3, username="owner"),
                db=DummyDB(),
            )
        )

    assert exc_info.value.status_code == 404


def test_attach_other_users_conversation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversations_api = _load_conversations_api_module()

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_conversation_for_user(db, user_id: int, conversation_id: int):
        return None

    monkeypatch.setattr(conversations_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(conversations_api, "get_conversation_for_user", fake_get_conversation_for_user)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            conversations_api.attach_dossier(
                conversation_id=6,
                payload=conversations_api.ConversationAttachDossierRequest(dossier_id=12),
                current_user=AuthenticatedUser(user_id=3, username="owner"),
                db=DummyDB(),
            )
        )

    assert exc_info.value.status_code == 404
