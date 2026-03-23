import time, logging, json
import redis.asyncio as redis
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.services.legal_agent import legal_agent
from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()
redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)

class DialogRequest(BaseModel):
    user_input: str
    session_id: str

@router.post("/dialog")
async def process_dialog(request: DialogRequest):
    start_time = time.time()
    try:
        answer = await legal_agent.ask(request.session_id, request.user_input)
        latency_ms = int((time.time() - start_time) * 1000)
        
        # 记录简易流，供前端 history 页面直接展示
        history_item = {"user_input": request.user_input, "system_response": answer, "timestamp": time.time()}
        await redis_client.rpush(f"frontend_history:{request.session_id}", json.dumps(history_item))
        
        logger.info(f"📊 [Metrics] session:{request.session_id} | latency:{latency_ms}ms")
        return {
            "response": answer,
            "agent_actions": [{"tool": "LangGraph", "status": "success"}], 
            "sources": [], "latency_ms": latency_ms
        }
    except Exception as e:
        logger.error(f"对话异常: {e}")
        raise HTTPException(status_code=500, detail="系统繁忙")

@router.delete("/dialog/{session_id}")
async def reset_dialog(session_id: str):
    await redis_client.delete(f"frontend_history:{session_id}")
    await redis_client.delete(f"checkpoint:{session_id}")
    return {"status": "success"}
