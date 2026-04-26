from __future__ import annotations

import asyncio
import importlib
import sys
from datetime import datetime
from types import ModuleType

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, inspect

from app.core.security import AuthenticatedUser
from app.models import Conversation, Dossier
from app.services.conversation_store import (
    ensure_storage_schema,
    load_dossier_context_for_conversation,
    serialize_conversation,
    serialize_dossier,
)


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
    _install_common_stubs()
    sys.modules.pop("app.api.dossiers", None)
    module = importlib.import_module("app.api.dossiers")
    sys.modules.pop("app.services.llm_service", None)
    return module


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


def test_serialize_dossier_includes_auto_summary_defaults() -> None:
    dossier = Dossier(
        id=13,
        user_id=7,
        title="劳动争议案卷",
        case_type="劳动争议",
        summary="手动摘要",
        status="active",
    )

    serialized = serialize_dossier(dossier)

    assert serialized["summary"] == "手动摘要"
    assert serialized["auto_summary"] == ""
    assert serialized["auto_summary_updated_at"] is None
    assert serialized["auto_summary_source_count"] == 0
    assert serialized["participant_aliases"] == {}


def test_serialize_dossier_includes_participant_aliases() -> None:
    dossier = Dossier(
        id=14,
        user_id=7,
        title="\u52b3\u52a8\u4e89\u8bae\u6848\u5377",
        case_type="\u52b3\u52a8\u4e89\u8bae",
        summary="\u624b\u52a8\u6458\u8981",
        status="active",
        participant_aliases_json='{"primary_party":{"label":"\\u6211","role":"\\u52b3\\u52a8\\u8005","party_side":"claimant","confirmed_by_user":true}}',
    )

    serialized = serialize_dossier(dossier)

    assert serialized["participant_aliases"]["primary_party"]["label"] == "\u6211"
    assert serialized["participant_aliases"]["primary_party"]["confirmed_by_user"] is True


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
                assert {
                    "title",
                    "case_type",
                    "summary",
                    "status",
                    "auto_summary",
                    "auto_summary_updated_at",
                    "auto_summary_source_count",
                    "participant_aliases_json",
                } <= dossier_columns
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


def test_get_dossier_detail_includes_bound_conversations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()
    dossier = Dossier(
        id=21,
        user_id=4,
        title="劳动争议案卷",
        case_type="劳动争议",
        summary="解除劳动合同争议",
        status="active",
    )
    conversations = [
        Conversation(
            id=101,
            user_id=4,
            dossier_id=21,
            session_id="session-101",
            title="第一次咨询",
            conversation_mode="TRACKED_CASE",
            persistence_consent="tracked",
            case_type="劳动争议",
        ),
        Conversation(
            id=102,
            user_id=4,
            dossier_id=21,
            session_id="session-102",
            title="补充证据",
            conversation_mode="CASE_ANALYSIS",
            persistence_consent="temporary",
            case_type="劳动争议",
        ),
    ]

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        return dossier

    async def fake_list_dossier_conversations(db, user_id: int, dossier_id: int):
        assert user_id == 4
        assert dossier_id == 21
        return conversations

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dossiers_api, "get_dossier_for_user", fake_get_dossier_for_user)
    monkeypatch.setattr(dossiers_api, "list_dossier_conversations", fake_list_dossier_conversations)

    result = asyncio.run(
        dossiers_api.get_dossier(
            dossier_id=21,
            current_user=AuthenticatedUser(user_id=4, username="owner"),
            db=DummyDB(),
        )
    )

    assert result["status"] == "success"
    assert result["dossier"]["conversation_count"] == 2
    assert [item["id"] for item in result["dossier"]["conversations"]] == [101, 102]
    assert result["dossier"]["conversations"][0]["conversation_mode"] == "TRACKED_CASE"
    assert result["dossier"]["conversations"][1]["persistence_consent"] == "temporary"


def test_get_dossier_detail_uses_current_user_scope_for_conversations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()
    dossier = Dossier(
        id=22,
        user_id=7,
        title="我的案卷",
        case_type="合同纠纷",
        summary="summary",
        status="active",
    )
    captured: dict[str, int] = {}

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        return dossier

    async def fake_list_dossier_conversations(db, user_id: int, dossier_id: int):
        captured["user_id"] = user_id
        captured["dossier_id"] = dossier_id
        return []

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dossiers_api, "get_dossier_for_user", fake_get_dossier_for_user)
    monkeypatch.setattr(dossiers_api, "list_dossier_conversations", fake_list_dossier_conversations)

    result = asyncio.run(
        dossiers_api.get_dossier(
            dossier_id=22,
            current_user=AuthenticatedUser(user_id=7, username="owner"),
            db=DummyDB(),
        )
    )

    assert captured == {"user_id": 7, "dossier_id": 22}
    assert result["dossier"]["conversation_count"] == 0
    assert result["dossier"]["conversations"] == []


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


def test_update_dossier_participants_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()
    dossier = Dossier(
        id=10,
        user_id=2,
        title="\u6848\u5377",
        case_type="\u52b3\u52a8\u4e89\u8bae",
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
        dossiers_api.update_dossier_participants(
            dossier_id=10,
            payload=dossiers_api.DossierParticipantsUpdateRequest(
                participant_aliases={
                    "primary_party": dossiers_api.DossierParticipantAliasRequest(
                        label="\u6211",
                        role="\u52b3\u52a8\u8005",
                        party_side="claimant",
                    ),
                    "opposing_party": dossiers_api.DossierParticipantAliasRequest(
                        label="\u516c\u53f8",
                        role="\u7528\u4eba\u5355\u4f4d",
                        party_side="opponent",
                    ),
                }
            ),
            current_user=AuthenticatedUser(user_id=2, username="owner"),
            db=DummyDB(),
        )
    )

    assert result["status"] == "success"
    assert result["dossier"]["participant_aliases"]["primary_party"]["label"] == "\u6211"
    assert result["dossier"]["participant_aliases"]["primary_party"]["confirmed_by_user"] is True
    assert result["dossier"]["participant_aliases"]["opposing_party"]["party_side"] == "opponent"


def test_update_dossier_participants_other_user_returns_not_found(
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
            dossiers_api.update_dossier_participants(
                dossier_id=11,
                payload=dossiers_api.DossierParticipantsUpdateRequest(
                    participant_aliases={
                        "primary_party": dossiers_api.DossierParticipantAliasRequest(
                            label="\u6211",
                            role="\u52b3\u52a8\u8005",
                            party_side="claimant",
                        )
                    }
                ),
                current_user=AuthenticatedUser(user_id=2, username="owner"),
                db=DummyDB(),
            )
        )

    assert exc_info.value.status_code == 404


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


def test_create_conversation_without_dossier_keeps_legacy_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversations_api = _load_conversations_api_module()
    captured: dict[str, object] = {}

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_create_conversation_record(db, user_id: int, title: str = "", dossier_id=None):
        captured["user_id"] = user_id
        captured["title"] = title
        captured["dossier_id"] = dossier_id
        return Conversation(
            id=31,
            user_id=user_id,
            dossier_id=dossier_id,
            session_id="session-31",
            title=title,
            conversation_mode="UNKNOWN",
            persistence_consent="unknown",
        )

    monkeypatch.setattr(conversations_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(conversations_api, "create_conversation_record", fake_create_conversation_record)

    result = asyncio.run(
        conversations_api.create_conversation(
            payload=conversations_api.ConversationCreateRequest(title="新会话"),
            current_user=AuthenticatedUser(user_id=8, username="owner"),
            db=DummyDB(),
        )
    )

    assert captured == {"user_id": 8, "title": "新会话", "dossier_id": None}
    assert result["conversation"]["dossier_id"] is None


def test_create_conversation_with_owned_dossier_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversations_api = _load_conversations_api_module()
    captured: dict[str, object] = {}

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        return Dossier(
            id=dossier_id,
            user_id=user_id,
            title="劳动案卷",
            case_type="劳动争议",
            summary="summary",
            status="active",
        )

    async def fake_create_conversation_record(db, user_id: int, title: str = "", dossier_id=None):
        captured["user_id"] = user_id
        captured["title"] = title
        captured["dossier_id"] = dossier_id
        return Conversation(
            id=32,
            user_id=user_id,
            dossier_id=dossier_id,
            session_id="session-32",
            title=title,
            conversation_mode="UNKNOWN",
            persistence_consent="unknown",
        )

    async def fake_update_conversation_mode_state(
        db,
        conversation: Conversation,
        *,
        conversation_mode: str | None = None,
        persistence_consent: str | None = None,
        mode_reason: str | None = None,
        mark_confirmed: bool = False,
    ):
        captured["conversation_mode"] = conversation_mode
        captured["persistence_consent"] = persistence_consent
        captured["mode_reason"] = mode_reason
        captured["mark_confirmed"] = mark_confirmed
        conversation.conversation_mode = conversation_mode or conversation.conversation_mode
        conversation.persistence_consent = persistence_consent or conversation.persistence_consent
        conversation.mode_reason = mode_reason
        conversation.mode_confirmed_at = datetime(2026, 4, 26, 12, 0, 0) if mark_confirmed else None
        return conversation

    monkeypatch.setattr(conversations_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(conversations_api, "get_dossier_for_user", fake_get_dossier_for_user)
    monkeypatch.setattr(conversations_api, "create_conversation_record", fake_create_conversation_record)
    monkeypatch.setattr(
        conversations_api,
        "update_conversation_mode_state",
        fake_update_conversation_mode_state,
    )

    result = asyncio.run(
        conversations_api.create_conversation(
            payload=conversations_api.ConversationCreateRequest(title="基于案卷的新会话", dossier_id=15),
            current_user=AuthenticatedUser(user_id=8, username="owner"),
            db=DummyDB(),
        )
    )

    assert captured == {
        "user_id": 8,
        "title": "基于案卷的新会话",
        "dossier_id": 15,
        "conversation_mode": "TRACKED_CASE",
        "persistence_consent": "tracked",
        "mode_reason": "created_from_dossier",
        "mark_confirmed": True,
    }
    assert result["conversation"]["dossier_id"] == 15
    assert result["conversation"]["conversation_mode"] == "TRACKED_CASE"
    assert result["conversation"]["persistence_consent"] == "tracked"
    assert result["conversation"]["mode_reason"] == "created_from_dossier"
    assert result["conversation"]["mode_confirmed_at"] == "2026-04-26T12:00:00"


def test_create_conversation_with_other_users_dossier_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversations_api = _load_conversations_api_module()

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        return None

    monkeypatch.setattr(conversations_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(conversations_api, "get_dossier_for_user", fake_get_dossier_for_user)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            conversations_api.create_conversation(
                payload=conversations_api.ConversationCreateRequest(title="new", dossier_id=99),
                current_user=AuthenticatedUser(user_id=8, username="owner"),
                db=DummyDB(),
            )
        )

    assert exc_info.value.status_code == 404


def test_load_dossier_context_for_conversation_returns_none_without_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_conversation_for_user(db, user_id: int, conversation_id: int):
        return Conversation(
            id=41,
            user_id=user_id,
            dossier_id=None,
            session_id="session-41",
            title="普通会话",
            conversation_mode="UNKNOWN",
            persistence_consent="unknown",
        )

    monkeypatch.setattr(
        "app.services.conversation_store.get_conversation_for_user",
        fake_get_conversation_for_user,
    )

    result = asyncio.run(
        load_dossier_context_for_conversation(DummyDB(), user_id=7, conversation_id=41)
    )

    assert result is None


def test_load_dossier_context_for_conversation_returns_owned_dossier_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossier = Dossier(
        id=52,
        user_id=7,
        title="劳动争议案卷",
        case_type="劳动争议",
        summary="解除劳动合同争议",
        status="active",
    )
    active_conversation = Conversation(
        id=61,
        user_id=7,
        dossier_id=52,
        session_id="session-61",
        title="当前会话",
        case_summary="第一轮案情摘要",
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
        case_type="劳动争议",
    )
    related_conversation = Conversation(
        id=62,
        user_id=7,
        dossier_id=52,
        session_id="session-62",
        title="补充证据",
        case_summary="第二轮案情摘要",
        conversation_mode="CASE_ANALYSIS",
        persistence_consent="temporary",
        case_type="劳动争议",
    )

    async def fake_get_conversation_for_user(db, user_id: int, conversation_id: int):
        assert user_id == 7
        assert conversation_id == 61
        return active_conversation

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        assert user_id == 7
        assert dossier_id == 52
        return dossier

    async def fake_list_dossier_conversations(db, user_id: int, dossier_id: int):
        assert user_id == 7
        assert dossier_id == 52
        return [active_conversation, related_conversation]

    monkeypatch.setattr(
        "app.services.conversation_store.get_conversation_for_user",
        fake_get_conversation_for_user,
    )
    monkeypatch.setattr(
        "app.services.conversation_store.get_dossier_for_user",
        fake_get_dossier_for_user,
    )
    monkeypatch.setattr(
        "app.services.conversation_store.list_dossier_conversations",
        fake_list_dossier_conversations,
    )

    result = asyncio.run(
        load_dossier_context_for_conversation(DummyDB(), user_id=7, conversation_id=61)
    )

    assert result is not None
    assert result["id"] == 52
    assert result["active_conversation_id"] == 61
    assert result["conversation_count"] == 2
    assert result["conversations"][0]["id"] == 61
    assert result["conversations"][0]["case_summary"] == "第一轮案情摘要"

def test_load_dossier_context_for_conversation_includes_participant_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossier = Dossier(
        id=72,
        user_id=7,
        title="\u52b3\u52a8\u4e89\u8bae\u6848\u5377",
        case_type="\u52b3\u52a8\u4e89\u8bae",
        summary="\u89e3\u9664\u52b3\u52a8\u5408\u540c\u4e89\u8bae",
        status="active",
        participant_aliases_json='{"primary_party":{"label":"\\u6211","role":"\\u52b3\\u52a8\\u8005","party_side":"claimant","confirmed_by_user":true},"opposing_party":{"label":"\\u516c\\u53f8","role":"\\u7528\\u4eba\\u5355\\u4f4d","party_side":"opponent","confirmed_by_user":true}}',
    )
    active_conversation = Conversation(
        id=81,
        user_id=7,
        dossier_id=72,
        session_id="session-81",
        title="\u5f53\u524d\u4f1a\u8bdd",
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )

    async def fake_get_conversation_for_user(db, user_id: int, conversation_id: int):
        return active_conversation

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        return dossier

    async def fake_list_dossier_conversations(db, user_id: int, dossier_id: int):
        return [active_conversation]

    monkeypatch.setattr(
        "app.services.conversation_store.get_conversation_for_user",
        fake_get_conversation_for_user,
    )
    monkeypatch.setattr(
        "app.services.conversation_store.get_dossier_for_user",
        fake_get_dossier_for_user,
    )
    monkeypatch.setattr(
        "app.services.conversation_store.list_dossier_conversations",
        fake_list_dossier_conversations,
    )

    result = asyncio.run(
        load_dossier_context_for_conversation(DummyDB(), user_id=7, conversation_id=81)
    )

    assert result is not None
    assert result["participant_aliases"]["primary_party"]["label"] == "\u6211"
    assert result["participant_aliases"]["opposing_party"]["role"] == "\u7528\u4eba\u5355\u4f4d"


def test_load_dossier_context_for_conversation_respects_user_isolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_conversation_for_user(db, user_id: int, conversation_id: int):
        return Conversation(
            id=71,
            user_id=user_id,
            dossier_id=88,
            session_id="session-71",
            title="当前会话",
            conversation_mode="TRACKED_CASE",
            persistence_consent="tracked",
        )

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        return None

    monkeypatch.setattr(
        "app.services.conversation_store.get_conversation_for_user",
        fake_get_conversation_for_user,
    )
    monkeypatch.setattr(
        "app.services.conversation_store.get_dossier_for_user",
        fake_get_dossier_for_user,
    )

    result = asyncio.run(
        load_dossier_context_for_conversation(DummyDB(), user_id=7, conversation_id=71)
    )

    assert result is None
