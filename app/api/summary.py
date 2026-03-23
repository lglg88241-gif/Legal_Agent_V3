from fastapi import APIRouter
import redis.asyncio as redis
import json
from app.core.config import settings
from app.services.llm_service import llm_service

router = APIRouter()
redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)

@router.post("/dialog/{session_id}/summary")
async def generate_summary(session_id: str):
    items = await redis_client.lrange(f"frontend_history:{session_id}", 0, 10)
    if not items: return {"status": "success", "summary": "暂无内容", "session_id": session_id}
        
    history_text = "\n".join([json.loads(i)["user_input"] for i in items])
    summary = await llm_service.async_generate(f"请简要总结以下用户的核心诉求(50字内)：\n{history_text}")
    return {"status": "success", "summary": summary, "session_id": session_id}
