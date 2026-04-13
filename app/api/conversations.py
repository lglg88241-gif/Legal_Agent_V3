from __future__ import annotations

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
    update_conversation_title,
)
from app.services.graph_extractor import delete_case_graph, get_case_graph
from app.services.llm_service import llm_service

router = APIRouter(prefix="/conversations", tags=["conversations"])


class ConversationCreateRequest(BaseModel):
    title: str = Field(default=DEFAULT_CONVERSATION_TITLE, max_length=255)


class ConversationUpdateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)


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


@router.put("/{conversation_id}")
async def rename_conversation(
    conversation_id: int,
    payload: ConversationUpdateRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_storage_schema()
    conversation = await get_conversation_for_user(db, current_user.user_id, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    await update_conversation_title(db, conversation, payload.title)
    await db.commit()
    await db.refresh(conversation)
    return {"status": "success", "conversation": serialize_conversation(conversation)}


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


@router.get("/{conversation_id}/graph")
async def get_conversation_graph(
    conversation_id: int,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_storage_schema()
    conversation = await get_conversation_for_user(db, current_user.user_id, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    graph = await get_case_graph(conversation.id)
    return {
        "status": "success",
        "conversation_id": conversation.id,
        **graph,
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
    await delete_case_graph(conversation.id)
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
    await delete_case_graph(conversation.id)
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
        return {"status": "success", "summary": "当前会话暂无可总结的内容。"}

    summary = await llm_service.async_generate(
        "请将以下用户问题总结为 50 字以内的中文摘要：\n" + "\n".join(user_messages[:20]),
        system_prompt="你是一个简洁的法律助理。",
    )
    return {"status": "success", "summary": summary}
