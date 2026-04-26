from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthenticatedUser, get_current_user
from app.database.mysql_db import get_db
from app.services.conversation_store import (
    DEFAULT_CONVERSATION_TITLE,
    attach_conversation_to_dossier,
    clear_conversation_messages,
    create_conversation_record,
    delete_conversation_record,
    ensure_storage_schema,
    get_conversation_for_user,
    get_dossier_for_user,
    list_user_conversations_by_ids,
    list_conversation_messages,
    list_user_conversations,
    search_user_cases,
    serialize_conversation,
    serialize_message,
    update_conversation_mode_state,
    update_conversation_title,
)
from app.services.conversation_mode import (
    validate_conversation_mode,
    validate_persistence_consent,
)
from app.services.graph_extractor import delete_case_graph, get_case_graph
from app.services.llm_service import llm_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/conversations", tags=["conversations"])


class ConversationCreateRequest(BaseModel):
    title: str = Field(default=DEFAULT_CONVERSATION_TITLE, max_length=255)
    dossier_id: int | None = Field(default=None, gt=0)


class ConversationUpdateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)


class ConversationBatchDeleteRequest(BaseModel):
    conversation_ids: list[int] = Field(default_factory=list, min_length=1)


class ConversationModeConsentRequest(BaseModel):
    mode: str = Field(..., min_length=1, max_length=32)
    consent: str = Field(..., min_length=1, max_length=32)
    reason: str | None = Field(default=None, max_length=1000)


class ConversationAttachDossierRequest(BaseModel):
    dossier_id: int = Field(..., gt=0)


def _build_graph_cleanup_response(
    *,
    graph_cleanup_result: dict[str, object] | None,
    conversation_id: int,
    user_id: int,
    success_message: str,
) -> dict[str, object]:
    cleanup = graph_cleanup_result or {}
    graph_cleanup_success = bool(cleanup.get("success"))
    response: dict[str, object] = {
        "status": "success",
        "success": True,
        "graph_cleanup_success": graph_cleanup_success,
    }
    if graph_cleanup_success:
        return response

    error = str(cleanup.get("error") or "").strip() or "unknown error"
    logger.warning(
        "%s | conversation_id=%s user_id=%s error=%s",
        success_message,
        conversation_id,
        user_id,
        error,
    )
    response["warning"] = "SQL data cleared but graph cleanup failed"
    return response


async def _delete_conversation_with_graph_cleanup(
    *,
    db: AsyncSession,
    conversation_id: int,
    user_id: int,
    success_message: str,
) -> dict[str, object]:
    await delete_conversation_record(db, conversation_id)
    await db.commit()
    return _build_graph_cleanup_response(
        graph_cleanup_result=await delete_case_graph(conversation_id),
        conversation_id=conversation_id,
        user_id=user_id,
        success_message=success_message,
    )


@router.post("")
async def create_conversation(
    payload: ConversationCreateRequest | None = None,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_storage_schema()
    dossier_id = payload.dossier_id if payload else None
    if dossier_id is not None:
        dossier = await get_dossier_for_user(db, current_user.user_id, dossier_id)
        if dossier is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dossier not found")
    conversation = await create_conversation_record(
        db,
        user_id=current_user.user_id,
        title=(payload.title if payload else DEFAULT_CONVERSATION_TITLE),
        dossier_id=dossier_id,
    )
    if dossier_id is not None:
        await update_conversation_mode_state(
            db,
            conversation,
            conversation_mode="TRACKED_CASE",
            persistence_consent="tracked",
            mode_reason="created_from_dossier",
            mark_confirmed=True,
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


@router.get("/search")
async def search_cases(
    case_type: str | None = None,
    keyword: str | None = None,
    limit: int = 10,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Surgery #13: 按案件类型或关键词搜索用户的历史案件。"""
    await ensure_storage_schema()
    results = await search_user_cases(
        db,
        user_id=current_user.user_id,
        case_type=case_type,
        keyword=keyword,
        limit=min(limit, 50),
    )
    return {
        "status": "success",
        "conversations": [serialize_conversation(conv) for conv in results],
        "cases": [serialize_conversation(conv) for conv in results],
    }


@router.delete("/batch")
async def batch_delete_conversations(
    payload: ConversationBatchDeleteRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_storage_schema()

    normalized_ids: list[int] = []
    seen: set[int] = set()
    for conversation_id in payload.conversation_ids:
        if conversation_id <= 0 or conversation_id in seen:
            continue
        seen.add(conversation_id)
        normalized_ids.append(conversation_id)

    if not normalized_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No valid conversation ids provided")

    owned_conversations = await list_user_conversations_by_ids(db, current_user.user_id, normalized_ids)
    owned_map = {conversation.id: conversation for conversation in owned_conversations}

    deleted: list[int] = []
    failed: list[int] = []
    skipped: list[int] = []
    graph_warnings: list[dict[str, object]] = []

    for conversation_id in normalized_ids:
        conversation = owned_map.get(conversation_id)
        if conversation is None:
            skipped.append(conversation_id)
            continue

        try:
            delete_result = await _delete_conversation_with_graph_cleanup(
                db=db,
                conversation_id=conversation_id,
                user_id=current_user.user_id,
                success_message="Conversation batch delete graph cleanup failed",
            )
            deleted.append(conversation_id)
            if not bool(delete_result.get("graph_cleanup_success")):
                graph_warnings.append(
                    {
                        "conversation_id": conversation_id,
                        "warning": str(delete_result.get("warning") or "").strip() or "Graph cleanup failed",
                    }
                )
        except Exception as exc:
            await db.rollback()
            failed.append(conversation_id)
            logger.exception(
                "Conversation batch delete failed | conversation_id=%s user_id=%s",
                conversation_id,
                current_user.user_id,
            )
            graph_warnings.append(
                {
                    "conversation_id": conversation_id,
                    "warning": f"Conversation delete failed: {exc}",
                }
            )

    return {
        "status": "success",
        "success": True,
        "deleted": deleted,
        "failed": failed,
        "skipped": skipped,
        "graph_warnings": graph_warnings,
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


@router.post("/{conversation_id}/mode-consent")
async def set_conversation_mode_consent(
    conversation_id: int,
    payload: ConversationModeConsentRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_storage_schema()
    conversation = await get_conversation_for_user(db, current_user.user_id, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    try:
        normalized_mode = validate_conversation_mode(payload.mode)
        normalized_consent = validate_persistence_consent(payload.consent)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if normalized_consent == "tracked" and normalized_mode != "TRACKED_CASE":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tracked consent requires TRACKED_CASE mode",
        )

    if normalized_consent == "temporary" and normalized_mode != "CASE_ANALYSIS":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="temporary consent requires CASE_ANALYSIS mode",
        )

    await update_conversation_mode_state(
        db,
        conversation,
        conversation_mode=normalized_mode,
        persistence_consent=normalized_consent,
        mode_reason=payload.reason,
        mark_confirmed=True,
    )
    await db.commit()
    await db.refresh(conversation)
    return {"status": "success", "conversation": serialize_conversation(conversation)}


@router.post("/{conversation_id}/attach-dossier")
async def attach_dossier(
    conversation_id: int,
    payload: ConversationAttachDossierRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_storage_schema()
    conversation = await get_conversation_for_user(db, current_user.user_id, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    dossier = await get_dossier_for_user(db, current_user.user_id, payload.dossier_id)
    if dossier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dossier not found")

    await attach_conversation_to_dossier(db, conversation, dossier.id)
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

    return await _delete_conversation_with_graph_cleanup(
        db=db,
        conversation_id=conversation.id,
        user_id=current_user.user_id,
        success_message="Conversation delete graph cleanup failed",
    )


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
    graph_cleanup_result = await delete_case_graph(conversation.id)
    return _build_graph_cleanup_response(
        graph_cleanup_result=graph_cleanup_result,
        conversation_id=conversation.id,
        user_id=current_user.user_id,
        success_message="Conversation clear graph cleanup failed",
    )


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
