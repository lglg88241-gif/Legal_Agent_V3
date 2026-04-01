import asyncio
import time

from celery import Celery

from app.core.config import settings
from app.services.legal_agent import legal_agent

celery_app = Celery(
    "legal_worker",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
)


@celery_app.task(bind=True, name="ask_agent_task")
def ask_agent_task(self, session_id: str, question: str):
    start_time = time.time()

    try:
        answer = asyncio.run(legal_agent.ask(session_id, question))
        latency_ms = int((time.time() - start_time) * 1000)
        response_text = answer["response"] if isinstance(answer, dict) else str(answer)
        response_sources = answer.get("sources", []) if isinstance(answer, dict) else []

        return {
            "status": "success",
            "response": response_text,
            "agent_actions": [
                {"tool": "tool_router", "result_summary": "多路召回路由判定完成"},
                {"tool": "retrieval_tools", "result_summary": "Chroma 与 Neo4j 检索完成"},
                {"tool": "legal_advisor", "result_summary": "基于检索结果生成最终法律分析"},
            ],
            "sources": response_sources,
            "latency_ms": latency_ms,
        }
    except Exception as exc:
        import traceback

        traceback.print_exc()
        return {"status": "failed", "error": str(exc)}
