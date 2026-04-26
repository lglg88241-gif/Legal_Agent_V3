from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, inspect, or_, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.database.mysql_db import Base, engine
from app.models import (
    Conversation,
    Dossier,
    DossierParticipant,
    DossierParticipantMention,
    Message,
)
from app.services.conversation_mode import (
    normalize_conversation_mode,
    normalize_persistence_consent,
)

DEFAULT_CONVERSATION_TITLE = ""
DEFAULT_FALLBACK_TITLE = "默认会话"

DEFAULT_CONVERSATION_MODE = "UNKNOWN"
DEFAULT_PERSISTENCE_CONSENT = "unknown"
DEFAULT_DOSSIER_STATUS = "active"
VALID_DOSSIER_STATUSES = {"active", "archived", "closed"}
DEFAULT_PARTICIPANT_ALIASES: dict[str, dict[str, Any]] = {}
VALID_PARTY_SIDES = {"claimant", "opponent", "neutral", "unknown"}
VALID_ENTITY_TYPES = {"person", "organization", "authority", "unknown"}
VALID_PARTICIPANT_MENTION_SOURCES = {"graph", "conversation", "user"}
RUNTIME_INDEX_SPECS = {
    "dossiers": (
        ("ix_dossiers_user_id", ("user_id",)),
        ("ix_dossiers_user_id_status", ("user_id", "status")),
        ("ix_dossiers_updated_at", ("updated_at",)),
    ),
    "conversations": (
        ("ix_conversations_user_id", ("user_id",)),
        ("ix_conversations_dossier_id", ("dossier_id",)),
        ("ix_conversations_session_id", ("session_id",)),
        ("ix_conversations_user_id_dossier_id", ("user_id", "dossier_id")),
        ("ix_conversations_user_id_updated_at", ("user_id", "updated_at")),
        ("ix_conversations_conversation_mode", ("conversation_mode",)),
    ),
    "dossier_participants": (
        ("ix_dossier_participants_dossier_id", ("dossier_id",)),
        ("ix_dossier_participants_user_id", ("user_id",)),
        ("ix_dossier_participants_dossier_id_label", ("dossier_id", "label")),
    ),
    "dossier_participant_mentions": (
        ("ix_dossier_participant_mentions_dossier_id", ("dossier_id",)),
        ("ix_dossier_participant_mentions_conversation_id", ("conversation_id",)),
        ("ix_dossier_participant_mentions_participant_id", ("participant_id",)),
        (
            "ix_dossier_participant_mentions_dossier_id_raw_label",
            ("dossier_id", "raw_label"),
        ),
        (
            "ix_dossier_participant_mentions_dossier_id_conversation_id",
            ("dossier_id", "conversation_id"),
        ),
    ),
    "messages": (
        ("ix_messages_conversation_id", ("conversation_id",)),
    ),
}


def _ensure_runtime_indexes(sync_conn) -> None:
    inspector = inspect(sync_conn)
    tables = set(inspector.get_table_names())
    for table_name, index_specs in RUNTIME_INDEX_SPECS.items():
        if table_name not in tables:
            continue

        columns = {column["name"] for column in inspector.get_columns(table_name)}
        existing_index_names = {
            index.get("name")
            for index in inspector.get_indexes(table_name)
            if isinstance(index.get("name"), str)
        }
        for index_name, index_columns in index_specs:
            if index_name in existing_index_names:
                continue
            if any(column_name not in columns for column_name in index_columns):
                continue
            column_sql = ", ".join(index_columns)
            sync_conn.execute(text(f"CREATE INDEX {index_name} ON {table_name} ({column_sql})"))
            existing_index_names.add(index_name)


def _ensure_conversation_updated_at(sync_conn) -> None:
    inspector = inspect(sync_conn)
    tables = set(inspector.get_table_names())
    if "conversations" not in tables:
        return

    columns = {column["name"] for column in inspector.get_columns("conversations")}
    if "updated_at" not in columns:
        dialect = sync_conn.dialect.name
        if dialect == "sqlite":
            sync_conn.execute(text("ALTER TABLE conversations ADD COLUMN updated_at DATETIME"))
        elif dialect == "postgresql":
            sync_conn.execute(
                text(
                    "ALTER TABLE conversations "
                    "ADD COLUMN updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
                )
            )
        else:
            sync_conn.execute(
                text(
                    "ALTER TABLE conversations "
                    "ADD COLUMN updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
                )
            )

    sync_conn.execute(
        text(
            "UPDATE conversations "
            "SET updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP) "
            "WHERE updated_at IS NULL"
        )
    )

    columns = {column["name"] for column in inspector.get_columns("conversations")}
    if "case_summary" not in columns:
        dialect = sync_conn.dialect.name
        if dialect == "sqlite":
            sync_conn.execute(text("ALTER TABLE conversations ADD COLUMN case_summary TEXT"))
        elif dialect == "postgresql":
            sync_conn.execute(
                text("ALTER TABLE conversations ADD COLUMN case_summary TEXT DEFAULT NULL")
            )
        else:
            sync_conn.execute(
                text("ALTER TABLE conversations ADD COLUMN case_summary TEXT DEFAULT NULL")
            )

    sync_conn.execute(
        text(
            "UPDATE conversations "
            "SET case_summary = COALESCE(case_summary, '') "
            "WHERE case_summary IS NULL"
        )
    )

    columns = {column["name"] for column in inspector.get_columns("conversations")}
    if "case_state_json" not in columns:
        dialect = sync_conn.dialect.name
        if dialect == "sqlite":
            sync_conn.execute(text("ALTER TABLE conversations ADD COLUMN case_state_json TEXT"))
        elif dialect == "postgresql":
            sync_conn.execute(
                text("ALTER TABLE conversations ADD COLUMN case_state_json JSONB DEFAULT NULL")
            )
        else:
            sync_conn.execute(
                text("ALTER TABLE conversations ADD COLUMN case_state_json JSON DEFAULT NULL")
            )

    columns = {column["name"] for column in inspector.get_columns("conversations")}
    if "case_type" not in columns:
        sync_conn.execute(
            text("ALTER TABLE conversations ADD COLUMN case_type VARCHAR(100) DEFAULT NULL")
        )

    columns = {column["name"] for column in inspector.get_columns("conversations")}
    if "conversation_mode" not in columns:
        sync_conn.execute(
            text(
                "ALTER TABLE conversations "
                "ADD COLUMN conversation_mode VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN'"
            )
        )

    sync_conn.execute(
        text(
            "UPDATE conversations "
            "SET conversation_mode = 'UNKNOWN' "
            "WHERE conversation_mode IS NULL OR conversation_mode = ''"
        )
    )

    columns = {column["name"] for column in inspector.get_columns("conversations")}
    if "persistence_consent" not in columns:
        sync_conn.execute(
            text(
                "ALTER TABLE conversations "
                "ADD COLUMN persistence_consent VARCHAR(32) NOT NULL DEFAULT 'unknown'"
            )
        )

    sync_conn.execute(
        text(
            "UPDATE conversations "
            "SET persistence_consent = 'unknown' "
            "WHERE persistence_consent IS NULL OR persistence_consent = ''"
        )
    )

    columns = {column["name"] for column in inspector.get_columns("conversations")}
    if "mode_confirmed_at" not in columns:
        sync_conn.execute(text("ALTER TABLE conversations ADD COLUMN mode_confirmed_at DATETIME"))

    columns = {column["name"] for column in inspector.get_columns("conversations")}
    if "mode_reason" not in columns:
        sync_conn.execute(text("ALTER TABLE conversations ADD COLUMN mode_reason TEXT"))

    columns = {column["name"] for column in inspector.get_columns("conversations")}
    if "dossier_id" not in columns:
        sync_conn.execute(text("ALTER TABLE conversations ADD COLUMN dossier_id INTEGER"))

    inspector = inspect(sync_conn)
    tables = set(inspector.get_table_names())
    if "dossiers" not in tables:
        return

    dossier_columns = {column["name"] for column in inspector.get_columns("dossiers")}
    if "auto_summary" not in dossier_columns:
        sync_conn.execute(text("ALTER TABLE dossiers ADD COLUMN auto_summary TEXT"))

    dossier_columns = {column["name"] for column in inspector.get_columns("dossiers")}
    if "auto_summary_updated_at" not in dossier_columns:
        sync_conn.execute(text("ALTER TABLE dossiers ADD COLUMN auto_summary_updated_at DATETIME"))

    dossier_columns = {column["name"] for column in inspector.get_columns("dossiers")}
    if "auto_summary_source_count" not in dossier_columns:
        if sync_conn.dialect.name == "postgresql":
            sync_conn.execute(
                text(
                    "ALTER TABLE dossiers "
                    "ADD COLUMN auto_summary_source_count INTEGER NOT NULL DEFAULT 0"
                )
            )
        else:
            sync_conn.execute(
                text(
                    "ALTER TABLE dossiers "
                    "ADD COLUMN auto_summary_source_count INTEGER NOT NULL DEFAULT 0"
                )
            )

    sync_conn.execute(
        text(
            "UPDATE dossiers "
            "SET auto_summary_source_count = COALESCE(auto_summary_source_count, 0) "
            "WHERE auto_summary_source_count IS NULL"
        )
    )

    dossier_columns = {column["name"] for column in inspector.get_columns("dossiers")}
    if "participant_aliases_json" not in dossier_columns:
        sync_conn.execute(text("ALTER TABLE dossiers ADD COLUMN participant_aliases_json TEXT"))

    _ensure_runtime_indexes(sync_conn)


async def ensure_storage_schema(bind_engine: AsyncEngine | None = None) -> None:
    target_engine = bind_engine or engine
    async with target_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_conversation_updated_at)


def utcnow() -> datetime:
    return datetime.utcnow()


def build_conversation_title(
    content: str,
    fallback: str = "新会话",
) -> str:
    cleaned = " ".join((content or "").strip().split())
    if not cleaned:
        return fallback
    return cleaned[:24]


def normalize_dossier_status(status: str | None) -> str:
    cleaned = " ".join((status or "").strip().split()).lower()
    if cleaned not in VALID_DOSSIER_STATUSES:
        raise ValueError(f"Unsupported dossier status: {status}")
    return cleaned


def normalize_party_side(party_side: str | None) -> str:
    cleaned = " ".join((party_side or "").strip().split()).lower()
    if cleaned not in VALID_PARTY_SIDES:
        raise ValueError(f"Unsupported participant party_side: {party_side}")
    return cleaned


def normalize_entity_type(entity_type: str | None) -> str:
    cleaned = " ".join((entity_type or "").strip().split()).lower()
    if cleaned not in VALID_ENTITY_TYPES:
        raise ValueError(f"Unsupported participant entity_type: {entity_type}")
    return cleaned


def normalize_participant_mention_source(source: str | None) -> str:
    cleaned = " ".join((source or "").strip().split()).lower()
    if cleaned not in VALID_PARTICIPANT_MENTION_SOURCES:
        raise ValueError(f"Unsupported participant mention source: {source}")
    return cleaned


def _normalize_optional_text(value: str | None) -> str | None:
    cleaned = " ".join((value or "").strip().split())
    return cleaned or None


def _normalize_participant_label(label: str | None) -> str:
    cleaned = " ".join((label or "").strip().split())
    if not cleaned:
        raise ValueError("Participant label is required")
    return cleaned


def _normalize_participant_role(role: str | None) -> str | None:
    return _normalize_optional_text(role)


def _normalize_participant_description(description: str | None) -> str | None:
    return _normalize_optional_text(description)


async def create_conversation_record(
    db: AsyncSession,
    user_id: int,
    title: str = DEFAULT_CONVERSATION_TITLE,
    dossier_id: int | None = None,
) -> Conversation:
    now = utcnow()
    conversation = Conversation(
        user_id=user_id,
        dossier_id=dossier_id,
        session_id=str(uuid4()),
        title=(title or "").strip(),
        conversation_mode=DEFAULT_CONVERSATION_MODE,
        persistence_consent=DEFAULT_PERSISTENCE_CONSENT,
        updated_at=now,
    )
    db.add(conversation)
    await db.flush()
    return conversation


async def create_dossier_record(
    db: AsyncSession,
    user_id: int,
    *,
    title: str,
    case_type: str | None = None,
    summary: str | None = None,
    status: str = DEFAULT_DOSSIER_STATUS,
) -> Dossier:
    dossier = Dossier(
        user_id=user_id,
        title=(title or "").strip(),
        case_type=(case_type or "").strip() or None,
        summary=(summary or "").strip() or None,
        status=normalize_dossier_status(status),
        updated_at=utcnow(),
    )
    db.add(dossier)
    await db.flush()
    return dossier


async def get_conversation_for_user(
    db: AsyncSession,
    user_id: int,
    conversation_id: int,
) -> Conversation | None:
    return await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
    )


async def get_dossier_for_user(
    db: AsyncSession,
    user_id: int,
    dossier_id: int,
) -> Dossier | None:
    return await db.scalar(
        select(Dossier).where(
            Dossier.id == dossier_id,
            Dossier.user_id == user_id,
        )
    )


async def list_user_conversations(
    db: AsyncSession,
    user_id: int,
) -> list[Conversation]:
    result = await db.scalars(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
    )
    return list(result)


async def list_user_dossiers(
    db: AsyncSession,
    user_id: int,
) -> list[Dossier]:
    result = await db.scalars(
        select(Dossier)
        .where(Dossier.user_id == user_id)
        .order_by(Dossier.updated_at.desc(), Dossier.id.desc())
    )
    return list(result)


async def list_dossier_conversations(
    db: AsyncSession,
    user_id: int,
    dossier_id: int,
) -> list[Conversation]:
    result = await db.scalars(
        select(Conversation)
        .where(
            Conversation.user_id == user_id,
            Conversation.dossier_id == dossier_id,
        )
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
    )
    return list(result)


async def list_user_conversations_by_ids(
    db: AsyncSession,
    user_id: int,
    conversation_ids: list[int],
) -> list[Conversation]:
    normalized_ids = [conversation_id for conversation_id in conversation_ids if conversation_id > 0]
    if not normalized_ids:
        return []

    result = await db.scalars(
        select(Conversation)
        .where(
            Conversation.user_id == user_id,
            Conversation.id.in_(normalized_ids),
        )
        .order_by(Conversation.id.asc())
    )
    return list(result)


async def list_conversation_messages(
    db: AsyncSession,
    conversation_id: int,
) -> list[Message]:
    result = await db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc(), Message.id.asc())
    )
    return list(result)


async def list_recent_conversation_messages(
    db: AsyncSession,
    conversation_id: int,
    limit: int = 6,
) -> list[Message]:
    result = await db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit)
    )
    return list(reversed(list(result)))


async def get_message_for_conversation(
    db: AsyncSession,
    conversation_id: int,
    message_id: int,
) -> Message | None:
    return await db.scalar(
        select(Message).where(
            Message.id == message_id,
            Message.conversation_id == conversation_id,
        )
    )


async def touch_conversation(
    db: AsyncSession,
    conversation: Conversation | None = None,
    conversation_id: int | None = None,
) -> Conversation | None:
    target = conversation
    if target is None and conversation_id is not None:
        target = await db.get(Conversation, conversation_id)

    if target is None:
        return None

    target.updated_at = utcnow()
    db.add(target)
    await db.flush()
    return target


async def touch_dossier(
    db: AsyncSession,
    dossier: Dossier | None = None,
    dossier_id: int | None = None,
) -> Dossier | None:
    target = dossier
    if target is None and dossier_id is not None:
        target = await db.get(Dossier, dossier_id)

    if target is None:
        return None

    target.updated_at = utcnow()
    db.add(target)
    await db.flush()
    return target


async def update_conversation_title(
    db: AsyncSession,
    conversation: Conversation,
    title: str,
) -> Conversation:
    conversation.title = (title or "").strip()
    await touch_conversation(db, conversation=conversation)
    return conversation


async def update_dossier_fields(
    db: AsyncSession,
    dossier: Dossier,
    *,
    title: str | None = None,
    case_type: str | None = None,
    summary: str | None = None,
    status: str | None = None,
) -> Dossier:
    if title is not None:
        dossier.title = (title or "").strip()
    if case_type is not None:
        dossier.case_type = (case_type or "").strip() or None
    if summary is not None:
        dossier.summary = (summary or "").strip() or None
    if status is not None:
        dossier.status = normalize_dossier_status(status)
    await touch_dossier(db, dossier=dossier)
    return dossier


async def archive_dossier_record(
    db: AsyncSession,
    dossier: Dossier,
) -> Dossier:
    dossier.status = "archived"
    await touch_dossier(db, dossier=dossier)
    return dossier


async def update_dossier_auto_summary(
    db: AsyncSession,
    dossier: Dossier,
    *,
    auto_summary: str,
    auto_summary_source_count: int,
) -> Dossier:
    dossier.auto_summary = (auto_summary or "").strip() or None
    dossier.auto_summary_updated_at = utcnow() if dossier.auto_summary else None
    dossier.auto_summary_source_count = max(int(auto_summary_source_count or 0), 0)
    await touch_dossier(db, dossier=dossier)
    return dossier


def _load_participant_aliases(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return dict(DEFAULT_PARTICIPANT_ALIASES)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return dict(DEFAULT_PARTICIPANT_ALIASES)
    return parsed if isinstance(parsed, dict) else dict(DEFAULT_PARTICIPANT_ALIASES)


def _normalize_participant_aliases(
    participant_aliases: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    if not isinstance(participant_aliases, dict):
        return {}

    normalized: dict[str, dict[str, Any]] = {}
    for key, value in participant_aliases.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        label = str(value.get("label") or "").strip()
        role = str(value.get("role") or "").strip()
        party_side = str(value.get("party_side") or "").strip()
        if not label and not role:
            continue
        normalized[key] = {
            "label": label,
            "role": role,
            "party_side": party_side or key,
            "confirmed_by_user": True,
        }
    return normalized


async def update_dossier_participant_aliases(
    db: AsyncSession,
    dossier: Dossier,
    participant_aliases: dict[str, Any] | None,
) -> Dossier:
    normalized = _normalize_participant_aliases(participant_aliases)
    dossier.participant_aliases_json = json.dumps(normalized, ensure_ascii=False) if normalized else None
    await _sync_aliases_to_confirmed_participants(db, dossier, normalized)
    await touch_dossier(db, dossier=dossier)
    return dossier


def serialize_dossier_participant(participant: DossierParticipant) -> dict[str, Any]:
    return {
        "id": participant.id,
        "dossier_id": participant.dossier_id,
        "user_id": participant.user_id,
        "label": participant.label,
        "role": participant.role or "",
        "party_side": normalize_party_side(getattr(participant, "party_side", "unknown")),
        "entity_type": normalize_entity_type(getattr(participant, "entity_type", "unknown")),
        "description": participant.description or "",
        "confirmed_by_user": bool(getattr(participant, "confirmed_by_user", False)),
        "created_at": participant.created_at.isoformat() if participant.created_at else None,
        "updated_at": participant.updated_at.isoformat() if participant.updated_at else None,
    }


def serialize_dossier_participant_mention(
    mention: DossierParticipantMention,
    participant_lookup: dict[int, DossierParticipant] | None = None,
) -> dict[str, Any]:
    participant = None
    if participant_lookup is not None:
        participant = participant_lookup.get(getattr(mention, "participant_id", 0))

    payload = {
        "id": mention.id,
        "dossier_id": mention.dossier_id,
        "conversation_id": mention.conversation_id,
        "participant_id": mention.participant_id,
        "raw_label": mention.raw_label,
        "source": normalize_participant_mention_source(getattr(mention, "source", "graph")),
        "confidence": mention.confidence,
        "confirmed_by_user": bool(getattr(mention, "confirmed_by_user", False)),
        "created_at": mention.created_at.isoformat() if mention.created_at else None,
    }
    if participant is not None:
        payload["participant_label"] = participant.label
        payload["participant_role"] = participant.role or ""
        payload["participant_party_side"] = normalize_party_side(
            getattr(participant, "party_side", "unknown")
        )
        payload["participant_entity_type"] = normalize_entity_type(
            getattr(participant, "entity_type", "unknown")
        )
    return payload


async def list_dossier_participants(
    db: AsyncSession,
    user_id: int,
    dossier_id: int,
) -> list[DossierParticipant]:
    result = await db.scalars(
        select(DossierParticipant)
        .where(
            DossierParticipant.user_id == user_id,
            DossierParticipant.dossier_id == dossier_id,
        )
        .order_by(DossierParticipant.id.asc())
    )
    return list(result)


async def get_dossier_participant_for_user(
    db: AsyncSession,
    user_id: int,
    dossier_id: int,
    participant_id: int,
) -> DossierParticipant | None:
    return await db.scalar(
        select(DossierParticipant).where(
            DossierParticipant.id == participant_id,
            DossierParticipant.user_id == user_id,
            DossierParticipant.dossier_id == dossier_id,
        )
    )


async def list_dossier_participant_mentions(
    db: AsyncSession,
    user_id: int,
    dossier_id: int,
) -> list[DossierParticipantMention]:
    result = await db.scalars(
        select(DossierParticipantMention)
        .join(
            DossierParticipant,
            DossierParticipant.id == DossierParticipantMention.participant_id,
        )
        .where(
            DossierParticipant.user_id == user_id,
            DossierParticipantMention.dossier_id == dossier_id,
        )
        .order_by(
            DossierParticipantMention.conversation_id.asc(),
            DossierParticipantMention.id.asc(),
        )
    )
    return list(result)


async def create_dossier_participant_record(
    db: AsyncSession,
    *,
    dossier: Dossier,
    user_id: int,
    label: str,
    role: str | None = None,
    party_side: str = "unknown",
    entity_type: str = "unknown",
    description: str | None = None,
    confirmed_by_user: bool = True,
) -> DossierParticipant:
    participant = DossierParticipant(
        dossier_id=dossier.id,
        user_id=user_id,
        label=_normalize_participant_label(label),
        role=_normalize_participant_role(role),
        party_side=normalize_party_side(party_side),
        entity_type=normalize_entity_type(entity_type),
        description=_normalize_participant_description(description),
        confirmed_by_user=bool(confirmed_by_user),
        updated_at=utcnow(),
    )
    db.add(participant)
    await db.flush()
    await touch_dossier(db, dossier=dossier)
    return participant


async def update_dossier_participant_record(
    db: AsyncSession,
    participant: DossierParticipant,
    *,
    label: str | None = None,
    role: str | None = None,
    party_side: str | None = None,
    entity_type: str | None = None,
    description: str | None = None,
) -> DossierParticipant:
    if label is not None:
        participant.label = _normalize_participant_label(label)
    if role is not None:
        participant.role = _normalize_participant_role(role)
    if party_side is not None:
        participant.party_side = normalize_party_side(party_side)
    if entity_type is not None:
        participant.entity_type = normalize_entity_type(entity_type)
    if description is not None:
        participant.description = _normalize_participant_description(description)
    participant.updated_at = utcnow()
    db.add(participant)
    await db.flush()
    return participant


def build_dossier_participant_lookup(
    participants: list[DossierParticipant],
) -> dict[int, DossierParticipant]:
    return {
        participant.id: participant
        for participant in participants
        if isinstance(getattr(participant, "id", None), int)
    }


def _mention_key(
    *,
    conversation_id: int,
    raw_label: str,
    source: str,
) -> tuple[int, str, str]:
    return (
        int(conversation_id),
        " ".join((raw_label or "").strip().split()).lower(),
        normalize_participant_mention_source(source),
    )


async def resolve_dossier_participant_mentions(
    db: AsyncSession,
    *,
    dossier: Dossier,
    user_id: int,
    participant: DossierParticipant,
    mentions: list[dict[str, Any]],
) -> list[DossierParticipantMention]:
    normalized_mentions: list[dict[str, Any]] = []
    required_conversation_ids: list[int] = []
    seen_keys: set[tuple[int, str, str]] = set()

    for item in mentions:
        if not isinstance(item, dict):
            continue
        conversation_id = int(item.get("conversation_id") or 0)
        raw_label = _normalize_participant_label(item.get("raw_label"))
        source = normalize_participant_mention_source(item.get("source") or "graph")
        key = _mention_key(
            conversation_id=conversation_id,
            raw_label=raw_label,
            source=source,
        )
        if conversation_id <= 0 or key in seen_keys:
            continue
        seen_keys.add(key)
        required_conversation_ids.append(conversation_id)
        confidence_value = item.get("confidence")
        try:
            confidence = float(confidence_value) if confidence_value is not None else None
        except (TypeError, ValueError):
            confidence = None
        normalized_mentions.append(
            {
                "conversation_id": conversation_id,
                "raw_label": raw_label,
                "source": source,
                "confidence": confidence,
            }
        )

    conversations = await list_user_conversations_by_ids(db, user_id, required_conversation_ids)
    conversation_lookup = {conversation.id: conversation for conversation in conversations}
    for mention in normalized_mentions:
        conversation = conversation_lookup.get(mention["conversation_id"])
        if conversation is None or getattr(conversation, "dossier_id", None) != dossier.id:
            raise ValueError("Mention conversation must belong to the current user's dossier")

    existing_mentions = await list_dossier_participant_mentions(db, user_id, dossier.id)
    existing_lookup = {
        _mention_key(
            conversation_id=mention.conversation_id,
            raw_label=mention.raw_label,
            source=mention.source,
        ): mention
        for mention in existing_mentions
    }

    resolved_mentions: list[DossierParticipantMention] = []
    for mention_payload in normalized_mentions:
        key = _mention_key(
            conversation_id=mention_payload["conversation_id"],
            raw_label=mention_payload["raw_label"],
            source=mention_payload["source"],
        )
        existing = existing_lookup.get(key)
        if existing is None:
            existing = DossierParticipantMention(
                dossier_id=dossier.id,
                conversation_id=mention_payload["conversation_id"],
                participant_id=participant.id,
                raw_label=mention_payload["raw_label"],
                source=mention_payload["source"],
                confidence=mention_payload["confidence"],
                confirmed_by_user=True,
            )
            db.add(existing)
        else:
            existing.participant_id = participant.id
            existing.confidence = mention_payload["confidence"]
            existing.confirmed_by_user = True
            db.add(existing)
        resolved_mentions.append(existing)

    if resolved_mentions:
        await db.flush()
        await touch_dossier(db, dossier=dossier)
    return resolved_mentions


def _seed_alias_participant_defaults(alias_key: str) -> tuple[str, str]:
    if alias_key == "primary_party":
        return ("claimant", "person")
    if alias_key == "opposing_party":
        return ("opponent", "organization")
    return ("unknown", "unknown")


async def _sync_aliases_to_confirmed_participants(
    db: AsyncSession,
    dossier: Dossier,
    participant_aliases: dict[str, dict[str, Any]],
) -> None:
    if not participant_aliases:
        return

    participants = await list_dossier_participants(db, dossier.user_id, dossier.id)
    for alias_key, alias_value in participant_aliases.items():
        if not isinstance(alias_value, dict):
            continue
        label = str(alias_value.get("label") or "").strip()
        role = str(alias_value.get("role") or "").strip() or None
        if not label:
            continue

        default_party_side, default_entity_type = _seed_alias_participant_defaults(alias_key)
        target = next(
            (
                participant
                for participant in participants
                if participant.party_side == default_party_side and participant.label == label
            ),
            None,
        )
        if target is None:
            same_side_matches = [
                participant
                for participant in participants
                if participant.party_side == default_party_side
            ]
            if len(same_side_matches) == 1:
                target = same_side_matches[0]

        if target is None:
            target = await create_dossier_participant_record(
                db,
                dossier=dossier,
                user_id=dossier.user_id,
                label=label,
                role=role,
                party_side=default_party_side,
                entity_type=default_entity_type,
                description=None,
                confirmed_by_user=True,
            )
            participants.append(target)
            continue

        await update_dossier_participant_record(
            db,
            target,
            label=label,
            role=role,
            party_side=default_party_side,
            entity_type=default_entity_type,
        )


async def update_conversation_case_summary(
    db: AsyncSession,
    conversation: Conversation,
    case_summary: str,
) -> Conversation:
    conversation.case_summary = (case_summary or "").strip()
    await touch_conversation(db, conversation=conversation)
    return conversation


async def update_conversation_case_state(
    db: AsyncSession,
    conversation: Conversation,
    case_state_dict: dict[str, Any],
) -> Conversation:
    """Surgery #13: 持久化 CaseState 的完整 JSON 快照。"""
    conversation.case_state_json = case_state_dict
    await touch_conversation(db, conversation=conversation)
    return conversation


async def update_conversation_case_type(
    db: AsyncSession,
    conversation: Conversation,
    case_type: str,
) -> Conversation:
    """Surgery #13: 更新案件类型标签。"""
    conversation.case_type = (case_type or "").strip() or None
    await touch_conversation(db, conversation=conversation)
    return conversation


async def update_conversation_mode_state(
    db: AsyncSession,
    conversation: Conversation,
    *,
    conversation_mode: str | None = None,
    persistence_consent: str | None = None,
    mode_reason: str | None = None,
    mark_confirmed: bool = False,
) -> Conversation:
    if conversation_mode is not None:
        conversation.conversation_mode = normalize_conversation_mode(conversation_mode)
    if persistence_consent is not None:
        conversation.persistence_consent = normalize_persistence_consent(persistence_consent)
    if mode_reason is not None:
        conversation.mode_reason = (mode_reason or "").strip() or None
    if mark_confirmed:
        conversation.mode_confirmed_at = utcnow()
    await touch_conversation(db, conversation=conversation)
    return conversation


async def attach_conversation_to_dossier(
    db: AsyncSession,
    conversation: Conversation,
    dossier_id: int | None,
) -> Conversation:
    conversation.dossier_id = dossier_id
    await touch_conversation(db, conversation=conversation)
    return conversation


async def store_message(
    db: AsyncSession,
    conversation_id: int,
    role: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> Message:
    message = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        message_metadata=metadata or {},
    )
    db.add(message)
    await db.flush()
    await touch_conversation(db, conversation_id=conversation_id)
    return message


async def _delete_conversation_messages(db: AsyncSession, conversation_id: int) -> None:
    await db.execute(delete(Message).where(Message.conversation_id == conversation_id))


async def _delete_conversation_participant_mentions(
    db: AsyncSession,
    conversation_id: int,
) -> None:
    await db.execute(
        delete(DossierParticipantMention).where(
            DossierParticipantMention.conversation_id == conversation_id
        )
    )


async def delete_conversation_record(
    db: AsyncSession,
    conversation_id: int,
) -> None:
    await _delete_conversation_participant_mentions(db, conversation_id)
    await _delete_conversation_messages(db, conversation_id)
    await db.execute(delete(Conversation).where(Conversation.id == conversation_id))


async def clear_conversation_messages(
    db: AsyncSession,
    conversation_id: int,
) -> None:
    await _delete_conversation_participant_mentions(db, conversation_id)
    await _delete_conversation_messages(db, conversation_id)
    conversation = await db.get(Conversation, conversation_id)
    if conversation is not None:
        conversation.case_summary = ""
        conversation.case_state_json = None
        conversation.case_type = None
        conversation.conversation_mode = DEFAULT_CONVERSATION_MODE
        conversation.persistence_consent = DEFAULT_PERSISTENCE_CONSENT
        conversation.mode_confirmed_at = None
        conversation.mode_reason = None
        await touch_conversation(db, conversation=conversation)


async def maybe_update_conversation_title(
    db: AsyncSession,
    conversation: Conversation,
    user_content: str,
) -> None:
    if conversation.title and conversation.title not in {
        DEFAULT_CONVERSATION_TITLE,
        DEFAULT_FALLBACK_TITLE,
        "新会话",
    }:
        return

    conversation.title = build_conversation_title(user_content)
    db.add(conversation)
    await db.flush()


async def load_conversation_case_summary(
    db: AsyncSession,
    conversation_id: int,
) -> str:
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None:
        return ""
    return (conversation.case_summary or "").strip()


async def load_conversation_case_state(
    db: AsyncSession,
    conversation_id: int,
) -> dict[str, Any] | None:
    """Surgery #13: 加载上次保存的 CaseState JSON 快照。"""
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None:
        return None
    return conversation.case_state_json


async def load_conversation_persistence_consent(
    db: AsyncSession,
    conversation_id: int,
) -> str:
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None:
        return DEFAULT_PERSISTENCE_CONSENT
    return normalize_persistence_consent(
        getattr(conversation, "persistence_consent", DEFAULT_PERSISTENCE_CONSENT)
    )


async def search_user_cases(
    db: AsyncSession,
    user_id: int,
    case_type: str | None = None,
    keyword: str | None = None,
    limit: int = 10,
) -> list[Conversation]:
    """Surgery #13: 按类型或关键词检索用户的历史案件。"""
    query = select(Conversation).where(Conversation.user_id == user_id)
    if case_type:
        query = query.where(Conversation.case_type == case_type)
    if keyword:
        query = query.where(
            or_(
                Conversation.case_summary.contains(keyword),
                Conversation.title.contains(keyword),
            )
        )
    query = query.order_by(Conversation.updated_at.desc()).limit(limit)
    result = await db.scalars(query)
    return list(result)


CLAIMANT_PARTICIPANT_LABELS = {"我", "本人", "劳动者", "员工"}
OPPONENT_ORGANIZATION_LABELS = {"公司", "用人单位", "单位", "对方公司"}
KNOWN_SINGLE_PARTICIPANT_HINTS: dict[str, dict[str, str]] = {
    "老板": {"role": "老板", "party_side": "opponent", "entity_type": "person"},
    "hr": {"role": "HR", "party_side": "opponent", "entity_type": "person"},
    "人事": {"role": "人事", "party_side": "opponent", "entity_type": "person"},
    "主管": {"role": "主管", "party_side": "opponent", "entity_type": "person"},
    "经理": {"role": "经理", "party_side": "opponent", "entity_type": "person"},
    "对方": {"role": "对方当事人", "party_side": "opponent", "entity_type": "unknown"},
    "法院": {"role": "法院", "party_side": "neutral", "entity_type": "authority"},
    "仲裁委": {"role": "仲裁委", "party_side": "neutral", "entity_type": "authority"},
    "仲裁委员会": {"role": "仲裁委", "party_side": "neutral", "entity_type": "authority"},
}


def _normalized_label_key(label: str | None) -> str:
    return " ".join((label or "").strip().split()).lower()


def _display_label(value: str | None, fallback: str) -> str:
    cleaned = " ".join((value or "").strip().split())
    return cleaned or fallback


def _select_existing_candidate_participant(
    participants: list[DossierParticipant],
    *,
    raw_labels: list[str],
    party_side: str,
    entity_type: str,
) -> DossierParticipant | None:
    normalized_raw_labels = {_normalized_label_key(label) for label in raw_labels if label}
    for participant in participants:
        if _normalized_label_key(participant.label) in normalized_raw_labels:
            return participant

    side_matches = [
        participant
        for participant in participants
        if participant.party_side == party_side and participant.entity_type == entity_type
    ]
    if len(side_matches) == 1:
        return side_matches[0]
    return None


def _build_candidate_payload(
    *,
    candidate_key: str,
    raw_labels: list[str],
    mentions: list[dict[str, Any]],
    participants: list[DossierParticipant],
    suggested_label: str,
    role: str | None,
    party_side: str,
    entity_type: str,
) -> dict[str, Any] | None:
    if not mentions:
        return None

    existing_participant = _select_existing_candidate_participant(
        participants,
        raw_labels=raw_labels,
        party_side=party_side,
        entity_type=entity_type,
    )
    return {
        "candidate_key": candidate_key,
        "suggested_label": suggested_label,
        "suggested_role": role or "",
        "party_side": party_side,
        "entity_type": entity_type,
        "raw_labels": raw_labels,
        "mentions": mentions,
        "existing_participant_id": existing_participant.id if existing_participant is not None else None,
        "existing_participant_label": existing_participant.label if existing_participant is not None else "",
    }


def build_dossier_participant_candidates(
    *,
    graph_payload: dict[str, Any] | None,
    participants: list[DossierParticipant],
    confirmed_mentions: list[DossierParticipantMention],
    participant_aliases: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    graph = graph_payload.get("graph") if isinstance(graph_payload, dict) else {}
    nodes = graph.get("nodes") if isinstance(graph, dict) else []
    if not isinstance(nodes, list):
        return []

    confirmed_keys = {
        _mention_key(
            conversation_id=mention.conversation_id,
            raw_label=mention.raw_label,
            source=mention.source,
        )
        for mention in confirmed_mentions
    }

    unresolved_mentions: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        raw_label = _display_label(node.get("raw_label") or node.get("label"), "")
        conversation_id = node.get("conversation_id")
        if conversation_id is None and isinstance(node.get("properties"), dict):
            conversation_id = node["properties"].get("conversation_id")
        try:
            conversation_id = int(conversation_id or 0)
        except (TypeError, ValueError):
            conversation_id = 0
        if not raw_label or conversation_id <= 0:
            continue
        key = _mention_key(
            conversation_id=conversation_id,
            raw_label=raw_label,
            source="graph",
        )
        if key in confirmed_keys:
            continue
        unresolved_mentions.append(
            {
                "conversation_id": conversation_id,
                "raw_label": raw_label,
                "source": "graph",
                "confidence": None,
                "node_type": str(node.get("type") or "").strip(),
            }
        )

    if not unresolved_mentions:
        return []

    normalized_aliases = _normalize_participant_aliases(
        participant_aliases if isinstance(participant_aliases, dict) else None
    )
    if normalized_aliases:
        participants = list(participants)

    mentions_by_label: dict[str, list[dict[str, Any]]] = {}
    for mention in unresolved_mentions:
        mentions_by_label.setdefault(_normalized_label_key(mention["raw_label"]), []).append(mention)

    candidates: list[dict[str, Any]] = []
    consumed_keys: set[tuple[int, str, str]] = set()

    def consume(mentions: list[dict[str, Any]]) -> None:
        for item in mentions:
            consumed_keys.add(
                _mention_key(
                    conversation_id=item["conversation_id"],
                    raw_label=item["raw_label"],
                    source=item["source"],
                )
            )

    claimant_mentions = [
        mention
        for mention in unresolved_mentions
        if mention["raw_label"] in CLAIMANT_PARTICIPANT_LABELS
    ]
    if claimant_mentions:
        candidate = _build_candidate_payload(
            candidate_key="claimant_cluster",
            raw_labels=list(dict.fromkeys(item["raw_label"] for item in claimant_mentions)),
            mentions=claimant_mentions,
            participants=participants,
            suggested_label=(
                str(normalized_aliases.get("primary_party", {}).get("label") or "").strip()
                or "我"
            ),
            role=(
                str(normalized_aliases.get("primary_party", {}).get("role") or "").strip()
                or "劳动者"
            ),
            party_side="claimant",
            entity_type="person",
        )
        if candidate is not None:
            candidates.append(candidate)
            consume(claimant_mentions)

    opponent_org_mentions = [
        mention
        for mention in unresolved_mentions
        if mention["raw_label"] in OPPONENT_ORGANIZATION_LABELS
        and _mention_key(
            conversation_id=mention["conversation_id"],
            raw_label=mention["raw_label"],
            source=mention["source"],
        )
        not in consumed_keys
    ]
    if opponent_org_mentions:
        candidate = _build_candidate_payload(
            candidate_key="opponent_organization_cluster",
            raw_labels=list(dict.fromkeys(item["raw_label"] for item in opponent_org_mentions)),
            mentions=opponent_org_mentions,
            participants=participants,
            suggested_label=(
                str(normalized_aliases.get("opposing_party", {}).get("label") or "").strip()
                or "公司"
            ),
            role=(
                str(normalized_aliases.get("opposing_party", {}).get("role") or "").strip()
                or "用人单位"
            ),
            party_side="opponent",
            entity_type="organization",
        )
        if candidate is not None:
            candidates.append(candidate)
            consume(opponent_org_mentions)

    for normalized_label, mentions in mentions_by_label.items():
        unresolved_group = [
            mention
            for mention in mentions
            if _mention_key(
                conversation_id=mention["conversation_id"],
                raw_label=mention["raw_label"],
                source=mention["source"],
            )
            not in consumed_keys
        ]
        if not unresolved_group:
            continue

        raw_labels = list(dict.fromkeys(item["raw_label"] for item in unresolved_group))
        raw_label = raw_labels[0]
        hint = KNOWN_SINGLE_PARTICIPANT_HINTS.get(normalized_label)
        if len(unresolved_group) < 2 and hint is None:
            continue

        candidate = _build_candidate_payload(
            candidate_key=f"label:{normalized_label}",
            raw_labels=raw_labels,
            mentions=unresolved_group,
            participants=participants,
            suggested_label=raw_label,
            role=hint["role"] if hint else "",
            party_side=hint["party_side"] if hint else "unknown",
            entity_type=hint["entity_type"] if hint else "unknown",
        )
        if candidate is not None:
            candidates.append(candidate)
            consume(unresolved_group)

    return candidates


def serialize_dossier(dossier: Dossier) -> dict[str, Any]:
    participant_aliases = _load_participant_aliases(
        getattr(dossier, "participant_aliases_json", None)
    )
    return {
        "id": dossier.id,
        "user_id": dossier.user_id,
        "title": dossier.title,
        "case_type": dossier.case_type or "",
        "summary": dossier.summary or "",
        "auto_summary": getattr(dossier, "auto_summary", None) or "",
        "auto_summary_updated_at": (
            dossier.auto_summary_updated_at.isoformat()
            if getattr(dossier, "auto_summary_updated_at", None)
            else None
        ),
        "auto_summary_source_count": int(getattr(dossier, "auto_summary_source_count", 0) or 0),
        "participant_aliases": participant_aliases,
        "status": normalize_dossier_status(getattr(dossier, "status", DEFAULT_DOSSIER_STATUS)),
        "created_at": dossier.created_at.isoformat() if dossier.created_at else None,
        "updated_at": dossier.updated_at.isoformat() if dossier.updated_at else None,
    }


def serialize_dossier_conversation_summary(conversation: Conversation) -> dict[str, Any]:
    return {
        "id": conversation.id,
        "title": conversation.title,
        "conversation_mode": normalize_conversation_mode(
            getattr(conversation, "conversation_mode", DEFAULT_CONVERSATION_MODE)
        ),
        "persistence_consent": normalize_persistence_consent(
            getattr(conversation, "persistence_consent", DEFAULT_PERSISTENCE_CONSENT)
        ),
        "case_type": getattr(conversation, "case_type", None) or "",
        "case_summary": (getattr(conversation, "case_summary", None) or "").strip(),
        "updated_at": conversation.updated_at.isoformat() if conversation.updated_at else None,
    }


def serialize_dossier_detail(
    dossier: Dossier,
    conversations: list[Conversation],
    confirmed_participants: list[DossierParticipant] | None = None,
    participant_mentions: list[DossierParticipantMention] | None = None,
) -> dict[str, Any]:
    payload = serialize_dossier(dossier)
    payload["conversation_count"] = len(conversations)
    payload["conversations"] = [
        serialize_dossier_conversation_summary(conversation)
        for conversation in conversations
    ]
    participant_list = confirmed_participants or []
    mention_list = participant_mentions or []
    participant_lookup = build_dossier_participant_lookup(participant_list)
    payload["confirmed_participants"] = [
        serialize_dossier_participant(participant)
        for participant in participant_list
    ]
    payload["participant_mentions"] = [
        serialize_dossier_participant_mention(mention, participant_lookup)
        for mention in mention_list
    ]
    return payload


async def load_dossier_context_for_conversation(
    db: AsyncSession,
    user_id: int,
    conversation_id: int,
) -> dict[str, Any] | None:
    conversation = await get_conversation_for_user(db, user_id, conversation_id)
    if conversation is None:
        return None

    dossier_id = getattr(conversation, "dossier_id", None)
    if dossier_id is None:
        return None

    dossier = await get_dossier_for_user(db, user_id, dossier_id)
    if dossier is None:
        return None

    conversations = await list_dossier_conversations(db, user_id, dossier.id)
    participants = await list_dossier_participants(db, user_id, dossier.id)
    mentions = await list_dossier_participant_mentions(db, user_id, dossier.id)
    payload = serialize_dossier_detail(
        dossier,
        conversations,
        confirmed_participants=participants,
        participant_mentions=mentions,
    )
    payload["active_conversation_id"] = conversation.id
    return payload


def serialize_conversation(conversation: Conversation) -> dict[str, Any]:
    conversation_mode = normalize_conversation_mode(
        getattr(conversation, "conversation_mode", DEFAULT_CONVERSATION_MODE)
    )
    persistence_consent = normalize_persistence_consent(
        getattr(conversation, "persistence_consent", DEFAULT_PERSISTENCE_CONSENT)
    )
    return {
        "id": conversation.id,
        "user_id": conversation.user_id,
        "dossier_id": getattr(conversation, "dossier_id", None),
        "session_id": conversation.session_id,
        "title": conversation.title,
        "case_summary": conversation.case_summary or "",
        "case_type": getattr(conversation, "case_type", None) or "",
        "case_state": getattr(conversation, "case_state_json", None),
        "conversation_mode": conversation_mode,
        "persistence_consent": persistence_consent,
        "mode_confirmed_at": (
            conversation.mode_confirmed_at.isoformat()
            if getattr(conversation, "mode_confirmed_at", None)
            else None
        ),
        "mode_reason": getattr(conversation, "mode_reason", None),
        "created_at": conversation.created_at.isoformat() if conversation.created_at else None,
        "updated_at": conversation.updated_at.isoformat() if conversation.updated_at else None,
    }


def serialize_message(message: Message) -> dict[str, Any]:
    metadata = message.message_metadata or {}
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "role": message.role,
        "content": message.content,
        "metadata": metadata,
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }
