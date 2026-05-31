from __future__ import annotations

import asyncio
import importlib
import sys
from types import ModuleType

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.security import AuthenticatedUser
from app.database.mysql_db import Base
from app.models import Conversation, Dossier, DossierParticipant, DossierParticipantMention, User
from app.services.conversation_store import hard_delete_dossier_record


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


def _load_dossiers_api_module():
    _install_common_stubs()
    sys.modules.pop("app.api.dossiers", None)
    module = importlib.import_module("app.api.dossiers")
    sys.modules.pop("app.services.llm_service", None)
    return module


class DummyDB:
    def __init__(self) -> None:
        self.commit_called = False

    async def commit(self) -> None:
        self.commit_called = True


class SyncSessionAdapter:
    def __init__(self, session: Session) -> None:
        self.session = session

    async def execute(self, statement):
        return self.session.execute(statement)


def _count_rows(session: Session, model) -> int:
    return len(session.execute(select(model)).scalars().all())


def test_hard_delete_dossier_route_returns_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()
    dossier = Dossier(id=12, user_id=7, title="Delete me", status="active")
    db = DummyDB()

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        return dossier

    async def fake_hard_delete_dossier_record(db, dossier_obj):
        assert dossier_obj is dossier
        return {
            "dossier_id": dossier_obj.id,
            "detached_conversations": 2,
            "deleted_participants": 3,
            "deleted_mentions": 5,
        }

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dossiers_api, "get_dossier_for_user", fake_get_dossier_for_user)
    monkeypatch.setattr(
        dossiers_api,
        "hard_delete_dossier_record",
        fake_hard_delete_dossier_record,
    )

    result = asyncio.run(
        dossiers_api.hard_delete_dossier(
            dossier_id=12,
            current_user=AuthenticatedUser(user_id=7, username="owner"),
            db=db,
        )
    )

    assert db.commit_called is True
    assert result == {
        "success": True,
        "deleted": True,
        "dossier_id": 12,
        "detached_conversations": 2,
        "deleted_participants": 3,
        "deleted_mentions": 5,
    }


def test_hard_delete_dossier_alias_route_returns_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()
    dossier = Dossier(id=13, user_id=7, title="Delete me via alias", status="active")
    db = DummyDB()

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        return dossier

    async def fake_hard_delete_dossier_record(db, dossier_obj):
        assert dossier_obj is dossier
        return {
            "dossier_id": dossier_obj.id,
            "detached_conversations": 1,
            "deleted_participants": 2,
            "deleted_mentions": 4,
        }

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dossiers_api, "get_dossier_for_user", fake_get_dossier_for_user)
    monkeypatch.setattr(
        dossiers_api,
        "hard_delete_dossier_record",
        fake_hard_delete_dossier_record,
    )

    result = asyncio.run(
        dossiers_api.hard_delete_dossier_alias(
            dossier_id=13,
            current_user=AuthenticatedUser(user_id=7, username="owner"),
            db=db,
        )
    )

    assert db.commit_called is True
    assert result == {
        "success": True,
        "deleted": True,
        "dossier_id": 13,
        "detached_conversations": 1,
        "deleted_participants": 2,
        "deleted_mentions": 4,
    }


def test_hard_delete_dossier_route_rejects_missing_or_other_user(
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
            dossiers_api.hard_delete_dossier(
                dossier_id=99,
                current_user=AuthenticatedUser(user_id=8, username="other"),
                db=DummyDB(),
            )
        )

    assert exc_info.value.status_code == 404


@pytest.mark.parametrize("dossier_status", ["active", "archived"])
def test_hard_delete_dossier_record_deletes_dossier_and_detaches_conversations(
    dossier_status: str,
) -> None:
    sync_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(sync_engine)
    try:
        dossier_id: int | None = None
        bound_conversation_ids: list[int] = []

        with Session(sync_engine) as session:
            user = User(username="owner", hashed_password="hashed")
            dossier = Dossier(user=user, title="Hard delete target", status=dossier_status)
            bound_conversation_1 = Conversation(
                user=user,
                dossier=dossier,
                session_id="session-delete-1",
                title="Bound conversation 1",
                conversation_mode="TRACKED_CASE",
                persistence_consent="tracked",
            )
            bound_conversation_2 = Conversation(
                user=user,
                dossier=dossier,
                session_id="session-delete-2",
                title="Bound conversation 2",
                conversation_mode="TRACKED_CASE",
                persistence_consent="tracked",
            )
            free_conversation = Conversation(
                user=user,
                session_id="session-delete-free",
                title="Unbound conversation",
                conversation_mode="UNKNOWN",
                persistence_consent="unknown",
            )
            session.add_all([user, dossier, bound_conversation_1, bound_conversation_2, free_conversation])
            session.flush()

            participant = DossierParticipant(
                dossier=dossier,
                user_id=user.id,
                label="A公司",
                role="用人单位",
                party_side="opponent",
                entity_type="organization",
                confirmed_by_user=True,
            )
            session.add(participant)
            session.flush()

            session.add_all(
                [
                    DossierParticipantMention(
                        dossier_id=dossier.id,
                        conversation_id=bound_conversation_1.id,
                        participant_id=participant.id,
                        raw_label="公司",
                        source="graph",
                        confirmed_by_user=True,
                    ),
                    DossierParticipantMention(
                        dossier_id=dossier.id,
                        conversation_id=bound_conversation_2.id,
                        participant_id=participant.id,
                        raw_label="用人单位",
                        source="conversation",
                        confirmed_by_user=True,
                    ),
                ]
            )
            session.commit()
            dossier_id = dossier.id
            bound_conversation_ids = [bound_conversation_1.id, bound_conversation_2.id]
            free_conversation_id = free_conversation.id

        with Session(sync_engine) as session:
            dossier = session.get(Dossier, dossier_id)
            assert dossier is not None

            db = SyncSessionAdapter(session)
            result = asyncio.run(hard_delete_dossier_record(db, dossier))
            session.commit()

            assert result == {
                "dossier_id": dossier_id,
                "detached_conversations": 2,
                "deleted_participants": 1,
                "deleted_mentions": 2,
            }

        with Session(sync_engine) as session:
            assert session.get(Dossier, dossier_id) is None
            for conversation_id in bound_conversation_ids:
                refreshed = session.get(Conversation, conversation_id)
                assert refreshed is not None
                assert refreshed.dossier_id is None
            free_conversation = session.get(Conversation, free_conversation_id)
            assert free_conversation is not None
            assert free_conversation.dossier_id is None
            assert _count_rows(session, DossierParticipant) == 0
            assert _count_rows(session, DossierParticipantMention) == 0
            assert _count_rows(session, Conversation) == 3
    finally:
        sync_engine.dispose()
