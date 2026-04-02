import logging

from celery.result import AsyncResult
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.celery_app import ask_agent_task
from app.core.security import AuthenticatedUser, get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


class DialogRequest(BaseModel):
    user_input: str
    session_id: str


@router.post("/dialog")
async def process_dialog(
    request: DialogRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        task = ask_agent_task.apply_async(
            args=[current_user.user_id, request.session_id, request.user_input]
        )
        logger.info(
            "Task queued | user_id=%s session_id=%s task_id=%s",
            current_user.user_id,
            request.session_id,
            task.id,
        )
        return {
            "task_id": task.id,
            "status": "processing",
            "user_id": current_user.user_id,
            "session_id": request.session_id,
            "message": "任务已受理，AI 正在后台分析。",
        }
    except Exception as exc:
        logger.error("Failed to enqueue dialog task: %s", exc)
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
