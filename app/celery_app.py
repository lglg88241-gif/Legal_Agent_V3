from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from celery import Celery
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.services.conversation_store import (
    ensure_storage_schema,
    list_recent_conversation_messages,
    store_message,
)
from app.services.graph_extractor import format_graph_memory, refresh_case_graph
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


def _create_celery_session_factory() -> tuple[Any, async_sessionmaker[AsyncSession]]:
    celery_engine = create_async_engine(
        settings.DATABASE_URL,
        echo=False,
        poolclass=NullPool,
        pool_pre_ping=True,
    )
    session_factory = async_sessionmaker(
        celery_engine,
        expire_on_commit=False,
    )
    return celery_engine, session_factory


async def load_chat_history(
    conversation_id: int,
    limit: int = 6,
) -> str:
    celery_engine, session_factory = _create_celery_session_factory()

    try:
        await ensure_storage_schema(bind_engine=celery_engine)
        async with session_factory() as db:
            messages = await list_recent_conversation_messages(
                db,
                conversation_id=conversation_id,
                limit=limit,
            )
    finally:
        await celery_engine.dispose()

    lines = []
    for message in messages:
        role = "User" if message.role == "user" else "Assistant"
        content = " ".join((message.content or "").strip().split())
        if not content:
            continue
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


async def load_latest_user_input(conversation_id: int, limit: int = 12) -> str:
    celery_engine, session_factory = _create_celery_session_factory()

    try:
        await ensure_storage_schema(bind_engine=celery_engine)
        async with session_factory() as db:
            messages = await list_recent_conversation_messages(
                db,
                conversation_id=conversation_id,
                limit=limit,
            )
    finally:
        await celery_engine.dispose()

    for message in reversed(messages):
        if message.role == "user":
            content = " ".join((message.content or "").strip().split())
            if content:
                return content
    return ""


async def load_graph_memory(conversation_id: int) -> str:
    return await format_graph_memory(conversation_id=conversation_id)


async def persist_assistant_message(
    conversation_id: int,
    content: str,
    metadata: dict[str, Any],
) -> None:
    celery_engine, session_factory = _create_celery_session_factory()

    try:
        await ensure_storage_schema(bind_engine=celery_engine)
        async with session_factory() as db:
            await store_message(
                db,
                conversation_id=conversation_id,
                role="assistant",
                content=content,
                metadata=metadata,
            )
            await db.commit()
    finally:
        await celery_engine.dispose()


@celery_app.task(name="update_graph_task")
def update_graph_task(conversation_id: int):
    try:
        chat_history = asyncio.run(load_chat_history(conversation_id=conversation_id, limit=12))
        latest_user_input = asyncio.run(load_latest_user_input(conversation_id=conversation_id, limit=12))
        update_result = asyncio.run(
            refresh_case_graph(
                conversation_id=conversation_id,
                user_input=latest_user_input,
                chat_history=chat_history,
            )
        )
        logger.info(
            "Graph update task completed | conversation_id=%s actions_applied=%s",
            conversation_id,
            update_result.get("actions_applied", 0),
        )
        return {
            "status": "success",
            "conversation_id": conversation_id,
            "actions_applied": update_result.get("actions_applied", 0),
            "actions": update_result.get("actions", []),
        }
    except Exception as exc:
        logger.exception("update_graph_task failed | conversation_id=%s", conversation_id)
        return {
            "status": "failed",
            "conversation_id": conversation_id,
            "error": str(exc),
        }


@celery_app.task(bind=True, name="ask_agent_task")
def ask_agent_task(
    self,
    user_id: int,
    conversation_id: int,
    session_id: str,
    question: str,
    proactive_graph_mode: bool = True,
):
    start_time = time.time()

    try:
        chat_history = asyncio.run(load_chat_history(conversation_id=conversation_id, limit=6))
        graph_memory = asyncio.run(load_graph_memory(conversation_id=conversation_id))
        answer = asyncio.run(
            legal_agent.ask(
                user_id,
                session_id,
                question,
                chat_history=chat_history,
                graph_memory=graph_memory,
                proactive_graph_mode=proactive_graph_mode,
            )
        )
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
        update_graph_task.delay(conversation_id)
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
