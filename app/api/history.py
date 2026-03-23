from fastapi import APIRouter
import redis.asyncio as redis
import json
from app.core.config import settings

router = APIRouter()
redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)

@router.get("/dialog/{session_id}/history")
async def get_history(session_id: str, limit: int = 50):
    items = await redis_client.lrange(f"frontend_history:{session_id}", 0, limit - 1)
    return {"status": "success", "session_id": session_id, "history": [json.loads(i) for i in items]}
