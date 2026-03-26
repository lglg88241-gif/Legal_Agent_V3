# app/api/dialog.py
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from celery.result import AsyncResult
# 引入刚写好的 Celery 任务
from app.celery_app import ask_agent_task

logger = logging.getLogger(__name__)
router = APIRouter()

class DialogRequest(BaseModel):
    user_input: str
    session_id: str

# 1. 提交问题，秒回 Task ID (取餐码)
@router.post("/dialog")
async def process_dialog(request: DialogRequest):
    try:
        # 🌟 核心转变：不再等待大模型，直接把任务扔进 Redis 队列
        task = ask_agent_task.apply_async(args=[request.session_id, request.user_input])
        logger.info(f"✅ 任务已投递至队列 | Session: {request.session_id} | TaskID: {task.id}")
        
        # 返回取餐码
        return {
            "task_id": task.id,
            "status": "processing",
            "message": "任务已受理，AI正在后台为您分析"
        }
    except Exception as e:
        logger.error(f"任务投递失败: {e}")
        raise HTTPException(status_code=500, detail="队列系统繁忙")

# 2. 前端拿取餐码来轮询结果的接口
@router.get("/dialog/status/{task_id}")
async def get_task_status(task_id: str):
    task_result = AsyncResult(task_id)
    
    if task_result.state == 'PENDING' or task_result.state == 'STARTED':
        return {"status": "processing"}
    elif task_result.state == 'SUCCESS':
        # 任务完成，返回 Celery 里拼装好的完整数据
        return {"status": "completed", "data": task_result.result}
    else:
        return {"status": "failed", "error": str(task_result.info)}