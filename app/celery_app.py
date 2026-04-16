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
    get_conversation_for_user,
    list_recent_conversation_messages,
    load_conversation_case_summary,
    maybe_update_conversation_title,
    store_message,
    update_conversation_case_state,
    update_conversation_case_summary,
    update_conversation_case_type,
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
    # --- 内存与并发保护 ---
    # 线程模式下限制并发数，防止多任务同时加载 ML 模型撑爆内存
    worker_concurrency=2,
    # 每个 worker 子进程处理完 N 个任务后自动回收，防止内存泄漏累积
    worker_max_tasks_per_child=50,
    # 任务软/硬超时（秒），防止卡死的任务永久占用内存
    task_soft_time_limit=120,
    task_time_limit=180,
    # 结果后端写入失败时不抛出异常（Redis MISCONF 降级保护）
    task_ignore_result=False,
    result_backend_transport_options={
        "retry_policy": {
            "timeout": 5.0,
        }
    },
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


async def _run_task_async(conversation_id: int) -> dict[str, Any]:
    """
    将原本分散的多个 asyncio.run() 调用合并到单一协程中统一执行。

    原来的实现在每个 asyncio.run() 之间都会销毁并重建事件循环，
    高并发时会造成大量线程池资源竞争和不必要的开销。
    合并后全程共享同一个事件循环，IO 并发效率更高。
    """
    celery_engine, session_factory = _create_celery_session_factory()
    try:
        await ensure_storage_schema(bind_engine=celery_engine)
        async with session_factory() as db:
            messages = await list_recent_conversation_messages(
                db,
                conversation_id=conversation_id,
                limit=6,
            )
        lines = []
        for message in messages:
            role = "User" if message.role == "user" else "Assistant"
            content = " ".join((message.content or "").strip().split())
            if content:
                lines.append(f"{role}: {content}")
        chat_history = "\n".join(lines)

        await ensure_storage_schema(bind_engine=celery_engine)
        async with session_factory() as db:
            messages_12 = await list_recent_conversation_messages(
                db,
                conversation_id=conversation_id,
                limit=12,
            )
        latest_user_input = ""
        for message in reversed(messages_12):
            if message.role == "user":
                content = " ".join((message.content or "").strip().split())
                if content:
                    latest_user_input = content
                    break

        async with session_factory() as db:
            case_summary = await load_conversation_case_summary(db, conversation_id)
        from app.services.conversation_store import load_conversation_case_state

        async with session_factory() as db:
            saved_case_state = await load_conversation_case_state(db, conversation_id)

        graph_memory = await format_graph_memory(conversation_id=conversation_id)
        return {
            "chat_history": chat_history,
            "latest_user_input": latest_user_input,
            "case_summary": case_summary,
            "graph_memory": graph_memory,
            "saved_case_state": saved_case_state,
        }
    finally:
        await celery_engine.dispose()


async def _persist_dialog_turn_async(
    user_id: int,
    conversation_id: int,
    user_content: str,
    assistant_content: str,
    metadata: dict[str, Any],
    case_summary: str = "",
    case_state_dict: dict[str, Any] | None = None,
    case_type: str | None = None,
) -> None:
    celery_engine, session_factory = _create_celery_session_factory()
    try:
        await ensure_storage_schema(bind_engine=celery_engine)
        async with session_factory() as db:
            conversation = await get_conversation_for_user(db, user_id, conversation_id)
            if conversation is None:
                return
            await store_message(
                db,
                conversation_id=conversation_id,
                role="user",
                content=user_content,
                metadata={},
            )
            await maybe_update_conversation_title(db, conversation, user_content)
            if case_summary.strip():
                await update_conversation_case_summary(db, conversation, case_summary)
            if case_state_dict:
                await update_conversation_case_state(db, conversation, case_state_dict)
            if case_type:
                await update_conversation_case_type(db, conversation, case_type)
            await store_message(
                db,
                conversation_id=conversation_id,
                role="assistant",
                content=assistant_content,
                metadata=metadata,
            )
            await db.commit()
    finally:
        await celery_engine.dispose()


@celery_app.task(name="update_graph_task")
def update_graph_task(conversation_id: int):
    try:
        async def _run():
            celery_engine, session_factory = _create_celery_session_factory()
            try:
                await ensure_storage_schema(bind_engine=celery_engine)
                async with session_factory() as db:
                    case_summary = await load_conversation_case_summary(db, conversation_id)
                async with session_factory() as db:
                    messages = await list_recent_conversation_messages(
                        db, conversation_id=conversation_id, limit=12
                    )
            finally:
                await celery_engine.dispose()

            latest_user_input = ""
            for message in reversed(messages):
                if message.role == "user":
                    content = " ".join((message.content or "").strip().split())
                    if content:
                        latest_user_input = content
                        break

            return await refresh_case_graph(
                conversation_id=conversation_id,
                user_input=latest_user_input,
                case_summary=case_summary,
            )

        update_result = asyncio.run(_run())
        logger.info(
            "Graph update task completed | conversation_id=%s actions_applied=%s deleted_count=%s",
            conversation_id,
            update_result.get("actions_applied", 0),
            update_result.get("deleted_count", 0),
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
    is_precise_mode: bool = False,
    proactive_graph_mode: bool = True,
):
    start_time = time.time()

    try:
        # 所有 IO 合并到单一事件循环，避免多次 asyncio.run() 的开销
        context = asyncio.run(_run_task_async(conversation_id))
        chat_history = context["chat_history"]
        case_summary = context["case_summary"]
        graph_memory = context["graph_memory"]

        answer = asyncio.run(
            legal_agent.ask(
                user_id,
                session_id,
                question,
                chat_history=chat_history,
                case_summary=case_summary,
                graph_memory=graph_memory,
                is_precise_mode=is_precise_mode,
                proactive_graph_mode=proactive_graph_mode,
                conversation_id=conversation_id,
                saved_case_state=context.get("saved_case_state"),
            )
        )

        latency_ms = int((time.time() - start_time) * 1000)
        response_text = answer["response"] if isinstance(answer, dict) else str(answer)
        response_sources = answer.get("sources", []) if isinstance(answer, dict) else []
        route_intent = answer.get("route_intent", "GENERAL_CONSULT") if isinstance(answer, dict) else "GENERAL_CONSULT"
        updated_case_summary = answer.get("case_summary", "") if isinstance(answer, dict) else ""
        case_state_dict = answer.get("case_state_snapshot") if isinstance(answer, dict) else None
        case_type = answer.get("case_type") if isinstance(answer, dict) else None

        if route_intent == "GREETING":
            agent_actions = [
                {"tool": "tool_router", "result_summary": "Greeting route selected"},
                {"tool": "greeting", "result_summary": "Fast greeting response generated"},
            ]
        elif route_intent == "OUT_OF_SCOPE":
            agent_actions = [
                {"tool": "tool_router", "result_summary": "Out-of-scope route selected"},
                {"tool": "out_of_scope", "result_summary": "Fixed out-of-scope response generated"},
            ]
        elif route_intent == "CASE_UPDATE":
            agent_actions = [
                {"tool": "tool_router", "result_summary": "Case update route selected"},
                {"tool": "fact_updater", "result_summary": "Case summary refreshed"},
                {"tool": "retrieval_tools", "result_summary": "Retrieval pipeline completed"},
                {"tool": "reflector", "result_summary": "Case elements reviewed"},
            ]
        else:
            agent_actions = [
                {"tool": "tool_router", "result_summary": "General consult route selected"},
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
            "route_intent": route_intent,
            "case_summary": updated_case_summary,
            "latency_ms": latency_ms,
        }

        if route_intent != "GREETING":
            asyncio.run(
                _persist_dialog_turn_async(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    user_content=question,
                    assistant_content=response_text,
                    metadata={
                        "sources": response_sources,
                        "agent_actions": agent_actions,
                        "is_final": result["is_final"],
                        "confidence_score": result["confidence_score"],
                        "missing_info": result["missing_info"],
                        "latency_ms": latency_ms,
                        "route_intent": route_intent,
                    },
                    case_summary=updated_case_summary if route_intent == "CASE_UPDATE" else "",
                    case_state_dict=case_state_dict,
                    case_type=case_type,
                )
            )

        if route_intent == "CASE_UPDATE":
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
