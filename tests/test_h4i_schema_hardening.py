from __future__ import annotations

import asyncio

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from app.database.mysql_db import Base
from app.models import (
    Conversation,
    Dossier,
    DossierParticipant,
    DossierParticipantMention,
    Message,
    User,
)
from app.services.conversation_store import (
    archive_dossier_record,
    clear_conversation_messages,
    delete_conversation_record,
    ensure_storage_schema,
)


class FakeAsyncConnection:
    def __init__(self, sync_engine) -> None:
        self.sync_engine = sync_engine

    async def run_sync(self, fn):
        with self.sync_engine.begin() as sync_conn:
            return fn(sync_conn)


class FakeAsyncBegin:
    def __init__(self, sync_engine) -> None:
        self.sync_engine = sync_engine

    async def __aenter__(self):
        return FakeAsyncConnection(self.sync_engine)

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeAsyncEngine:
    def __init__(self, sync_engine) -> None:
        self.sync_engine = sync_engine

    def begin(self):
        return FakeAsyncBegin(self.sync_engine)


class SyncSessionAdapter:
    def __init__(self, session: Session) -> None:
        self.session = session

    async def execute(self, statement):
        return self.session.execute(statement)

    async def get(self, model, primary_key):
        return self.session.get(model, primary_key)

    def add(self, obj) -> None:
        self.session.add(obj)

    async def flush(self) -> None:
        self.session.flush()


def _count_rows(session: Session, model) -> int:
    return len(session.execute(select(model)).scalars().all())


def test_runtime_schema_ensure_is_idempotent_and_adds_workspace_indexes() -> None:
    async def _run() -> None:
        sync_engine = create_engine("sqlite:///:memory:")
        try:
            with sync_engine.begin() as sync_conn:
                sync_conn.execute(
                    text(
                        "CREATE TABLE users ("
                        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                        "username VARCHAR(255) NOT NULL, "
                        "hashed_password VARCHAR(255) NOT NULL, "
                        "created_at DATETIME)"
                    )
                )
                sync_conn.execute(
                    text(
                        "CREATE TABLE conversations ("
                        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                        "user_id INTEGER NOT NULL, "
                        "session_id VARCHAR(255) NOT NULL, "
                        "title VARCHAR(255) NOT NULL DEFAULT '', "
                        "created_at DATETIME)"
                    )
                )
                sync_conn.execute(
                    text(
                        "CREATE TABLE dossiers ("
                        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                        "user_id INTEGER NOT NULL, "
                        "title VARCHAR(255) NOT NULL DEFAULT '', "
                        "case_type VARCHAR(100), "
                        "summary TEXT, "
                        "status VARCHAR(32) NOT NULL DEFAULT 'active', "
                        "created_at DATETIME, "
                        "updated_at DATETIME)"
                    )
                )

            engine = FakeAsyncEngine(sync_engine)
            await ensure_storage_schema(bind_engine=engine)
            await ensure_storage_schema(bind_engine=engine)

            with sync_engine.begin() as sync_conn:
                inspector = inspect(sync_conn)
                tables = set(inspector.get_table_names())
                assert {
                    "conversations",
                    "dossiers",
                    "dossier_participants",
                    "dossier_participant_mentions",
                    "messages",
                } <= tables

                conversation_columns = {
                    column["name"] for column in inspector.get_columns("conversations")
                }
                dossier_columns = {
                    column["name"] for column in inspector.get_columns("dossiers")
                }
                assert {
                    "updated_at",
                    "case_summary",
                    "case_state_json",
                    "case_type",
                    "conversation_mode",
                    "persistence_consent",
                    "mode_confirmed_at",
                    "mode_reason",
                    "dossier_id",
                } <= conversation_columns
                assert {
                    "auto_summary",
                    "auto_summary_updated_at",
                    "auto_summary_source_count",
                    "participant_aliases_json",
                } <= dossier_columns

                conversation_indexes = {
                    index["name"] for index in inspector.get_indexes("conversations")
                }
                dossier_indexes = {
                    index["name"] for index in inspector.get_indexes("dossiers")
                }
                participant_indexes = {
                    index["name"] for index in inspector.get_indexes("dossier_participants")
                }
                mention_indexes = {
                    index["name"]
                    for index in inspector.get_indexes("dossier_participant_mentions")
                }
                message_indexes = {
                    index["name"] for index in inspector.get_indexes("messages")
                }

                assert {
                    "ix_dossiers_user_id",
                    "ix_dossiers_user_id_status",
                    "ix_dossiers_updated_at",
                } <= dossier_indexes
                assert {
                    "ix_conversations_user_id",
                    "ix_conversations_dossier_id",
                    "ix_conversations_session_id",
                    "ix_conversations_user_id_dossier_id",
                    "ix_conversations_user_id_updated_at",
                    "ix_conversations_conversation_mode",
                } <= conversation_indexes
                assert {
                    "ix_dossier_participants_dossier_id",
                    "ix_dossier_participants_user_id",
                    "ix_dossier_participants_dossier_id_label",
                } <= participant_indexes
                assert {
                    "ix_dossier_participant_mentions_dossier_id",
                    "ix_dossier_participant_mentions_conversation_id",
                    "ix_dossier_participant_mentions_participant_id",
                    "ix_dossier_participant_mentions_dossier_id_raw_label",
                    "ix_dossier_participant_mentions_dossier_id_conversation_id",
                } <= mention_indexes
                assert "ix_messages_conversation_id" in message_indexes
        finally:
            sync_engine.dispose()

    asyncio.run(_run())


def test_clear_conversation_messages_removes_participant_mentions() -> None:
    sync_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(sync_engine)
    try:
        conversation_id: int | None = None
        with Session(sync_engine) as session:
            user = User(username="owner", hashed_password="hashed")
            dossier = Dossier(user=user, title="Case dossier", status="active")
            conversation = Conversation(
                user=user,
                dossier=dossier,
                session_id="session-1",
                title="Tracked case",
                case_summary="old summary",
                case_state_json={"salary": 10000},
                case_type="labor",
                conversation_mode="TRACKED_CASE",
                persistence_consent="tracked",
                mode_reason="tracking",
            )
            session.add_all([user, dossier, conversation])
            session.flush()

            participant = DossierParticipant(
                dossier=dossier,
                user_id=user.id,
                label="Company",
                role="Employer",
                party_side="opponent",
                entity_type="organization",
                confirmed_by_user=True,
            )
            session.add(participant)
            session.flush()

            conversation_id = conversation.id
            mention = DossierParticipantMention(
                dossier_id=dossier.id,
                conversation_id=conversation_id,
                participant_id=participant.id,
                raw_label="Company",
                source="graph",
                confirmed_by_user=True,
            )
            message = Message(
                conversation_id=conversation_id,
                role="user",
                content="I was dismissed",
                message_metadata={},
            )
            session.add_all([mention, message])
            session.commit()

        with Session(sync_engine) as session:
            db = SyncSessionAdapter(session)
            asyncio.run(clear_conversation_messages(db, conversation_id))
            session.commit()

            refreshed = session.get(Conversation, conversation_id)
            assert refreshed is not None
            assert refreshed.case_summary == ""
            assert refreshed.case_state_json is None
            assert refreshed.case_type is None
            assert refreshed.conversation_mode == "UNKNOWN"
            assert refreshed.persistence_consent == "unknown"
            assert refreshed.mode_confirmed_at is None
            assert refreshed.mode_reason is None
            assert _count_rows(session, Message) == 0
            assert _count_rows(session, DossierParticipantMention) == 0
            assert _count_rows(session, DossierParticipant) == 1
    finally:
        sync_engine.dispose()


def test_delete_conversation_record_removes_messages_and_mentions() -> None:
    sync_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(sync_engine)
    try:
        conversation_id: int | None = None
        with Session(sync_engine) as session:
            user = User(username="owner", hashed_password="hashed")
            dossier = Dossier(user=user, title="Case dossier", status="active")
            conversation = Conversation(
                user=user,
                dossier=dossier,
                session_id="session-2",
                title="Tracked case",
                conversation_mode="TRACKED_CASE",
                persistence_consent="tracked",
            )
            session.add_all([user, dossier, conversation])
            session.flush()

            participant = DossierParticipant(
                dossier=dossier,
                user_id=user.id,
                label="Company",
                role="Employer",
                party_side="opponent",
                entity_type="organization",
                confirmed_by_user=True,
            )
            session.add(participant)
            session.flush()

            conversation_id = conversation.id
            session.add_all(
                [
                    DossierParticipantMention(
                        dossier_id=dossier.id,
                        conversation_id=conversation_id,
                        participant_id=participant.id,
                        raw_label="Company",
                        source="graph",
                        confirmed_by_user=True,
                    ),
                    Message(
                        conversation_id=conversation_id,
                        role="user",
                        content="First message",
                        message_metadata={},
                    ),
                    Message(
                        conversation_id=conversation_id,
                        role="assistant",
                        content="Reply",
                        message_metadata={},
                    ),
                ]
            )
            session.commit()

        with Session(sync_engine) as session:
            db = SyncSessionAdapter(session)
            asyncio.run(delete_conversation_record(db, conversation_id))
            session.commit()

            assert session.get(Conversation, conversation_id) is None
            assert _count_rows(session, Message) == 0
            assert _count_rows(session, DossierParticipantMention) == 0
            assert _count_rows(session, DossierParticipant) == 1
            assert _count_rows(session, Dossier) == 1
    finally:
        sync_engine.dispose()


def test_archive_dossier_record_keeps_participants_and_mentions() -> None:
    sync_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(sync_engine)
    try:
        with Session(sync_engine) as session:
            user = User(username="owner", hashed_password="hashed")
            dossier = Dossier(user=user, title="Archive target", status="active")
            conversation = Conversation(
                user=user,
                dossier=dossier,
                session_id="session-3",
                title="Tracked case",
                conversation_mode="TRACKED_CASE",
                persistence_consent="tracked",
            )
            session.add_all([user, dossier, conversation])
            session.flush()

            participant = DossierParticipant(
                dossier=dossier,
                user_id=user.id,
                label="Employee",
                role="Worker",
                party_side="claimant",
                entity_type="person",
                confirmed_by_user=True,
            )
            session.add(participant)
            session.flush()

            session.add(
                DossierParticipantMention(
                    dossier_id=dossier.id,
                    conversation_id=conversation.id,
                    participant_id=participant.id,
                    raw_label="I",
                    source="user",
                    confirmed_by_user=True,
                )
            )
            session.commit()

        with Session(sync_engine) as session:
            dossier = session.scalar(select(Dossier).where(Dossier.title == "Archive target"))
            assert dossier is not None

            db = SyncSessionAdapter(session)
            asyncio.run(archive_dossier_record(db, dossier))
            session.commit()

            archived = session.get(Dossier, dossier.id)
            assert archived is not None
            assert archived.status == "archived"
            assert _count_rows(session, DossierParticipant) == 1
            assert _count_rows(session, DossierParticipantMention) == 1
            assert _count_rows(session, Conversation) == 1
    finally:
        sync_engine.dispose()
