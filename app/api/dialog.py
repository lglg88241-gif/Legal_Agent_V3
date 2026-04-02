from __future__ import annotations

import logging

from celery.result import AsyncResult
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.celery_app import ask_agent_task
from app.core.security import AuthenticatedUser, get_current_user
from app.database.mysql_db import get_db
from app.services.case_boundary import detect_case_boundary
from app.services.conversation_store import (
    ensure_storage_schema,
    get_conversation_for_user,
    list_recent_conversation_messages,
    maybe_update_conversation_title,
    store_message,
)
from app.services.graph_extractor import format_graph_memory

logger = logging.getLogger(__name__)
router = APIRouter()


class DialogRequest(BaseModel):
    user_input: str = Field(..., min_length=1)
    conversation_id: int
    force_current_case: bool = False
    proactive_graph_mode: bool = True


@router.post("/dialog")
async def process_dialog(
    request: DialogRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_storage_schema()
    conversation = await get_conversation_for_user(
        db,
        current_user.user_id,
        request.conversation_id,
    )
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    try:
        clean_input = request.user_input.strip()
        recent_messages = await list_recent_conversation_messages(
            db,
            conversation_id=conversation.id,
            limit=6,
        )
        recent_history = "\n".join(
            f"{'User' if item.role == 'user' else 'Assistant'}: {' '.join((item.content or '').split())}"
            for item in recent_messages
            if (item.content or "").strip()
        )
        current_graph_summary = await format_graph_memory(conversation.id)

        if not request.force_current_case:
            boundary = await detect_case_boundary(
                current_graph_summary=current_graph_summary,
                recent_history=recent_history,
                user_input=clean_input,
            )
            if boundary == "NEW_CASE":
                return {
                    "status": "needs_confirmation",
                    "action": "suggest_new_case",
                    "user_id": current_user.user_id,
                    "conversation_id": conversation.id,
                    "session_id": conversation.session_id,
                    "message": (
                        "发现您似乎在咨询一个全新的法律问题。"
                        "为了保证案情分析与图谱记忆的准确性，是否为您建立一份新的独立卷宗？"
                    ),
                }

        await store_message(
            db,
            conversation_id=conversation.id,
            role="user",
            content=clean_input,
            metadata={},
        )
        await maybe_update_conversation_title(db, conversation, clean_input)
        await db.commit()

        task = ask_agent_task.apply_async(
            args=[
                current_user.user_id,
                conversation.id,
                conversation.session_id,
                clean_input,
                bool(request.proactive_graph_mode),
            ]
        )
        logger.info(
            "Task queued | user_id=%s conversation_id=%s session_id=%s task_id=%s",
            current_user.user_id,
            conversation.id,
            conversation.session_id,
            task.id,
        )
        return {
            "task_id": task.id,
            "status": "processing",
            "user_id": current_user.user_id,
            "conversation_id": conversation.id,
            "session_id": conversation.session_id,
            "message": "Dialog task accepted.",
        }
    except Exception as exc:
        await db.rollback()
        logger.exception("Failed to enqueue dialog task: %s", exc)
        raise HTTPException(status_code=500, detail="Queue system is busy")


@router.get("/dialog/status/{task_id}")
async def get_task_status(
    task_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    task_result = AsyncResult(task_id)

    if task_result.state in {"PENDING", "STARTED"}:
        return {"status": "processing", "user_id": current_user.user_id}

    if task_result.state == "SUCCESS":
        result = task_result.result or {}
        if isinstance(result, dict):
            owner_id = result.get("user_id")
            if owner_id is not None and int(owner_id) != current_user.user_id:
                raise HTTPException(status_code=403, detail="Task does not belong to current user")
        return {"status": "completed", "user_id": current_user.user_id, "data": result}

    return {"status": "failed", "user_id": current_user.user_id, "error": str(task_result.info)}
