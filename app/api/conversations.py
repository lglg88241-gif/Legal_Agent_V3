from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthenticatedUser, get_current_user
from app.database.mysql_db import get_db
from app.services.conversation_store import (
    DEFAULT_CONVERSATION_TITLE,
    clear_conversation_messages,
    create_conversation_record,
    delete_conversation_record,
    ensure_storage_schema,
    get_conversation_for_user,
    list_conversation_messages,
    list_user_conversations,
    serialize_conversation,
    serialize_message,
)
from app.services.llm_service import llm_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/conversations", tags=["conversations"])


class ConversationCreateRequest(BaseModel):
    title: str = Field(default=DEFAULT_CONVERSATION_TITLE, max_length=255)


@router.post("")
async def create_conversation(
    payload: ConversationCreateRequest | None = None,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_storage_schema()
    conversation = await create_conversation_record(
        db,
        user_id=current_user.user_id,
        title=(payload.title if payload else DEFAULT_CONVERSATION_TITLE),
    )
    await db.commit()
    await db.refresh(conversation)
    return {"status": "success", "conversation": serialize_conversation(conversation)}


@router.get("")
async def get_conversations(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_storage_schema()
    conversations = await list_user_conversations(db, current_user.user_id)
    return {
        "status": "success",
        "conversations": [serialize_conversation(item) for item in conversations],
    }


@router.get("/{conversation_id}/messages")
async def get_conversation_messages(
    conversation_id: int,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_storage_schema()
    conversation = await get_conversation_for_user(db, current_user.user_id, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    messages = await list_conversation_messages(db, conversation.id)
    return {
        "status": "success",
        "conversation": serialize_conversation(conversation),
        "messages": [serialize_message(message) for message in messages],
    }


@router.delete("/{conversation_id}")
async def delete_conversation(
    conversation_id: int,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_storage_schema()
    conversation = await get_conversation_for_user(db, current_user.user_id, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    await delete_conversation_record(db, conversation.id)
    await db.commit()
    return {"status": "success"}


@router.delete("/{conversation_id}/messages")
async def clear_messages(
    conversation_id: int,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_storage_schema()
    conversation = await get_conversation_for_user(db, current_user.user_id, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    await clear_conversation_messages(db, conversation.id)
    await db.commit()
    return {"status": "success"}


@router.post("/{conversation_id}/summary")
async def summarize_conversation(
    conversation_id: int,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_storage_schema()
    conversation = await get_conversation_for_user(db, current_user.user_id, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    messages = await list_conversation_messages(db, conversation.id)
    user_messages = [message.content for message in messages if message.role == "user"]
    if not user_messages:
        return {"status": "success", "summary": "No user messages to summarize yet."}

    summary = await llm_service.async_generate(
        "Summarize the following user requests in under 50 words:\n" + "\n".join(user_messages[:20]),
        system_prompt="You are a concise legal assistant.",
    )
    return {"status": "success", "summary": summary}
