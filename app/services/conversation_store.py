from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, inspect, or_, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.database.mysql_db import Base, engine
from app.models import Conversation, Message

DEFAULT_CONVERSATION_TITLE = ""
DEFAULT_FALLBACK_TITLE = "默认会话"


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


async def create_conversation_record(
    db: AsyncSession,
    user_id: int,
    title: str = DEFAULT_CONVERSATION_TITLE,
) -> Conversation:
    now = utcnow()
    conversation = Conversation(
        user_id=user_id,
        session_id=str(uuid4()),
        title=(title or "").strip(),
        updated_at=now,
    )
    db.add(conversation)
    await db.flush()
    return conversation


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


async def update_conversation_title(
    db: AsyncSession,
    conversation: Conversation,
    title: str,
) -> Conversation:
    conversation.title = (title or "").strip()
    await touch_conversation(db, conversation=conversation)
    return conversation


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


def serialize_conversation(conversation: Conversation) -> dict[str, Any]:
    return {
        "id": conversation.id,
        "user_id": conversation.user_id,
        "session_id": conversation.session_id,
        "title": conversation.title,
        "case_summary": conversation.case_summary or "",
        "case_type": getattr(conversation, "case_type", None) or "",
        "case_state": getattr(conversation, "case_state_json", None),
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
