from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, inspect, or_, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.database.mysql_db import Base, engine
from app.models import Conversation, Dossier, Message
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
    await touch_dossier(db, dossier=dossier)
    return dossier


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


async def delete_conversation_record(
    db: AsyncSession,
    conversation_id: int,
) -> None:
    conversation = await db.get(Conversation, conversation_id)
    if conversation is not None:
        await db.delete(conversation)


async def clear_conversation_messages(
    db: AsyncSession,
    conversation_id: int,
) -> None:
    await db.execute(delete(Message).where(Message.conversation_id == conversation_id))
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
) -> dict[str, Any]:
    payload = serialize_dossier(dossier)
    payload["conversation_count"] = len(conversations)
    payload["conversations"] = [
        serialize_dossier_conversation_summary(conversation)
        for conversation in conversations
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
    payload = serialize_dossier_detail(dossier, conversations)
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
