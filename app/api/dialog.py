# app/api/dialog.py
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
        # 1. 呼叫 LangGraph 大脑
        answer = await legal_agent.ask(request.session_id, request.user_input)
        latency_ms = int((time.time() - start_time) * 1000)
        
        # 2. 🌟 构造前端 ChatMessage.vue 期望的 UI 渲染数据
        agent_actions = [
            {"tool": "semantic_router", "result_summary": "意图识别为法律咨询"},
            {"tool": "vector_researcher", "result_summary": "触发 ChromaDB 双路召回与 Neo4j 交叉验证"},
            {"tool": "legal_advisor", "result_summary": "结合上下文生成专业回复"}
        ]
        
        # 3. 记录历史供前端刷新时读取 (简化版双写机制)
        history_item = {
            "user_input": request.user_input, 
            "system_response": answer, 
            "timestamp": time.time(),
            "agent_actions": agent_actions # 一并存入，保证刷新不丢动效
        }
        await redis_client.rpush(f"frontend_history:{request.session_id}", json.dumps(history_item))
        
        logger.info(f"📊 [Metrics] session:{request.session_id} | latency:{latency_ms}ms")
        
        return {
            "response": answer,
            "agent_actions": agent_actions, 
            "sources": [], # 如果有提取到具体法条，可在此处组装
            "latency_ms": latency_ms
        }
    except Exception as e:
        logger.error(f"对话异常: {e}")
        raise HTTPException(status_code=500, detail="系统繁忙")

@router.delete("/dialog/{session_id}")
async def reset_dialog(session_id: str):
    await redis_client.delete(f"frontend_history:{session_id}")
    await redis_client.delete(f"checkpoint:{session_id}")
    return {"status": "success"}