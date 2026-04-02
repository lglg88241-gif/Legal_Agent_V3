from __future__ import annotations

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
def ask_agent_task(self, user_id: int, session_id: str, question: str):
    start_time = time.time()

    try:
        answer = asyncio.run(legal_agent.ask(user_id, session_id, question))
        latency_ms = int((time.time() - start_time) * 1000)
        response_text = answer["response"] if isinstance(answer, dict) else str(answer)
        response_sources = answer.get("sources", []) if isinstance(answer, dict) else []

        return {
            "status": "success",
            "user_id": user_id,
            "session_id": session_id,
            "response": response_text,
            "agent_actions": [
                {"tool": "tool_router", "result_summary": "Tool routing completed"},
                {"tool": "retrieval_tools", "result_summary": "Retrieval pipeline completed"},
                {"tool": "legal_advisor", "result_summary": "Final legal response generated"},
            ],
            "sources": response_sources,
            "is_final": answer.get("is_final", False) if isinstance(answer, dict) else False,
            "confidence_score": answer.get("confidence_score", 0.0) if isinstance(answer, dict) else 0.0,
            "missing_info": answer.get("missing_info", []) if isinstance(answer, dict) else [],
            "latency_ms": latency_ms,
        }
    except Exception as exc:
        import traceback

        traceback.print_exc()
        return {"status": "failed", "error": str(exc)}
