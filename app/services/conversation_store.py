from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.mysql_db import Base, engine
from app.models import Conversation, Message

DEFAULT_CONVERSATION_TITLE = "New Chat"
DEFAULT_FALLBACK_TITLE = "Default Conversation"


async def ensure_storage_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def build_conversation_title(
    content: str,
    fallback: str = DEFAULT_CONVERSATION_TITLE,
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
    conversation = Conversation(
        user_id=user_id,
        session_id=str(uuid4()),
        title=(title or DEFAULT_CONVERSATION_TITLE).strip() or DEFAULT_CONVERSATION_TITLE,
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
        .order_by(Conversation.created_at.desc(), Conversation.id.desc())
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
    }:
        return

    conversation.title = build_conversation_title(user_content)
    db.add(conversation)


def serialize_conversation(conversation: Conversation) -> dict[str, Any]:
    return {
        "id": conversation.id,
        "user_id": conversation.user_id,
        "session_id": conversation.session_id,
        "title": conversation.title,
        "created_at": conversation.created_at.isoformat() if conversation.created_at else None,
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
