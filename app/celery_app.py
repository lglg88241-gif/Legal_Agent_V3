from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from celery import Celery

from app.core.config import settings
from app.database.mysql_db import AsyncSessionLocal
from app.services.conversation_store import ensure_storage_schema, store_message
from app.services.legal_agent import legal_agent

logger = logging.getLogger(__name__)

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


async def persist_assistant_message(
    conversation_id: int,
    content: str,
    metadata: dict[str, Any],
) -> None:
    await ensure_storage_schema()
    async with AsyncSessionLocal() as db:
        await store_message(
            db,
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            metadata=metadata,
        )
        await db.commit()


@celery_app.task(bind=True, name="ask_agent_task")
def ask_agent_task(
    self,
    user_id: int,
    conversation_id: int,
    session_id: str,
    question: str,
):
    start_time = time.time()

    try:
        answer = asyncio.run(legal_agent.ask(user_id, session_id, question))
        latency_ms = int((time.time() - start_time) * 1000)
        response_text = answer["response"] if isinstance(answer, dict) else str(answer)
        response_sources = answer.get("sources", []) if isinstance(answer, dict) else []
        agent_actions = [
            {"tool": "tool_router", "result_summary": "Tool routing completed"},
            {"tool": "retrieval_tools", "result_summary": "Retrieval pipeline completed"},
            {"tool": "legal_advisor", "result_summary": "Final legal response generated"},
        ]
        result = {
            "status": "success",
            "user_id": user_id,
            "conversation_id": conversation_id,
            "session_id": session_id,
            "response": response_text,
            "agent_actions": agent_actions,
            "sources": response_sources,
            "is_final": answer.get("is_final", False) if isinstance(answer, dict) else False,
            "confidence_score": answer.get("confidence_score", 0.0) if isinstance(answer, dict) else 0.0,
            "missing_info": answer.get("missing_info", []) if isinstance(answer, dict) else [],
            "latency_ms": latency_ms,
        }

        asyncio.run(
            persist_assistant_message(
                conversation_id=conversation_id,
                content=response_text,
                metadata={
                    "sources": response_sources,
                    "agent_actions": agent_actions,
                    "is_final": result["is_final"],
                    "confidence_score": result["confidence_score"],
                    "missing_info": result["missing_info"],
                    "latency_ms": latency_ms,
                },
            )
        )
        return result
    except Exception as exc:
        logger.exception("ask_agent_task failed")
        return {
            "status": "failed",
            "user_id": user_id,
            "conversation_id": conversation_id,
            "session_id": session_id,
            "error": str(exc),
        }
