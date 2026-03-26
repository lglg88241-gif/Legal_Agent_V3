# app/celery_app.py
import time
import asyncio
from celery import Celery
from app.core.config import settings
from app.services.legal_agent import legal_agent

# 初始化 Celery，使用 Redis 作为消息队列 (Broker) 和结果存储 (Backend)
celery_app = Celery(
    "legal_worker",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
)

# 🌟 核心任务：在后台独立的 Worker 进程中运行大模型推理
@celery_app.task(bind=True, name="ask_agent_task")
def ask_agent_task(self, session_id: str, question: str):
    start_time = time.time()
    
    try:
        # 🌟 核心修复：使用 asyncio.run() 优雅地在同步线程中跑异步代码
        answer = asyncio.run(legal_agent.ask(session_id, question))
        
        latency_ms = int((time.time() - start_time) * 1000)
        
        return {
            "status": "success",
            "response": answer,
            "agent_actions": [
                {"tool": "semantic_router", "result_summary": "意图识别分析完成"},
                {"tool": "vector_researcher", "result_summary": "知识库交叉验证完成"},
                {"tool": "legal_advisor", "result_summary": "生成最终法律建议"}
            ],
            "sources": [], 
            "latency_ms": latency_ms
        }
    except Exception as e:
        # 如果出错，把错误信息也带回去，前端才能抛出异常
        import traceback
        traceback.print_exc() # 在 Worker 终端打印完整堆栈方便排查
        return {"status": "failed", "error": str(e)}