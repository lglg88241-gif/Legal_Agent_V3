from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, inspect, select, text
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


def serialize_conversation(conversation: Conversation) -> dict[str, Any]:
    return {
        "id": conversation.id,
        "user_id": conversation.user_id,
        "session_id": conversation.session_id,
        "title": conversation.title,
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
