from __future__ import annotations

import inspect
import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.services.conversation_store import (
    ensure_storage_schema,
    get_conversation_for_user,
    update_conversation_case_state,
    update_conversation_case_summary,
    update_conversation_case_type,
)


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _preview_text(value: Any, *, limit: int = 120) -> str:
    normalized = _normalize_text(value)
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _write_result(
    *,
    write_type: str,
    step_name: str,
    agent: str,
    status: str,
    input_summary: str | None,
    output_summary: str | None,
    latency_ms: int,
    error: str | None = None,
    blocked_by: list[str] | None = None,
    skip_reason: str | None = None,
    applied: bool = False,
    source: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": _normalize_text(write_type),
        "write_type": _normalize_text(write_type),
        "step_name": _normalize_text(step_name),
        "agent": _normalize_text(agent),
        "status": _normalize_text(status).lower() or "success",
        "input_summary": _normalize_text(input_summary) or None,
        "output_summary": _normalize_text(output_summary) or None,
        "latency_ms": max(0, int(latency_ms)),
        "error": _normalize_text(error) or None,
        "blocked_by": [
            _normalize_text(item)
            for item in list(blocked_by or [])
            if _normalize_text(item)
        ],
        "skip_reason": _normalize_text(skip_reason) or None,
        "applied": bool(applied),
        "source": _normalize_text(source) or None,
        "details": details or {},
    }


def _applied_write_entry(result: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    entry = {
        "name": _normalize_text(result.get("write_type") or request.get("name")),
        "applied": True,
        "input_text": _normalize_text(request.get("input_text")) or None,
        "source": _normalize_text(result.get("source")) or None,
    }
    details = result.get("details") if isinstance(result.get("details"), dict) else {}
    task_id = _normalize_text(details.get("task_id"))
    if task_id:
        entry["task_id"] = task_id
    return entry


def _structured_persistence_kwargs(runtime: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": int(runtime.get("user_id") or 0),
        "conversation_id": int(runtime.get("conversation_id") or 0),
        "user_content": runtime.get("user_content") or "",
        "assistant_content": runtime.get("assistant_content") or "",
        "metadata": runtime.get("assistant_metadata") or {},
        "user_metadata": runtime.get("user_metadata") if isinstance(runtime.get("user_metadata"), dict) else {},
        "case_summary": runtime.get("updated_case_summary") or "",
        "case_state_dict": (
            runtime.get("case_state_dict")
            if isinstance(runtime.get("case_state_dict"), dict)
            else None
        ),
        "case_type": runtime.get("case_type"),
    }


async def _apply_structured_case_persistence(
    request: dict[str, Any],
    runtime: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    started_at = time.perf_counter()
    input_summary = _preview_text(request.get("input_text"))
    handler = runtime.get("structured_persistence_handler")
    details: dict[str, Any] = {}

    if callable(handler):
        persist_result = await _maybe_await(
            handler(**_structured_persistence_kwargs(runtime))
        )
        normalized_persist_result = (
            dict(persist_result) if isinstance(persist_result, dict) else {}
        )
        details = {
            "user_message_id": normalized_persist_result.get("user_message_id"),
            "assistant_message_id": normalized_persist_result.get("assistant_message_id"),
        }
        return (
            _write_result(
                write_type="structured_case_persistence",
                step_name="fact_persistence",
                agent="conversation_store",
                status="success",
                input_summary=input_summary,
                output_summary="Structured case persistence saved",
                latency_ms=int((time.perf_counter() - started_at) * 1000),
                applied=True,
                source="conversation_store",
                details=details,
            ),
            normalized_persist_result,
        )

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
    try:
        await ensure_storage_schema(bind_engine=celery_engine)
        async with session_factory() as db:
            conversation = await get_conversation_for_user(
                db,
                int(runtime.get("user_id") or 0),
                int(runtime.get("conversation_id") or 0),
            )
            if conversation is None:
                return (
                    _write_result(
                        write_type="structured_case_persistence",
                        step_name="fact_persistence",
                        agent="conversation_store",
                        status="failed",
                        input_summary=input_summary,
                        output_summary="conversation_not_found",
                        latency_ms=int((time.perf_counter() - started_at) * 1000),
                        error="conversation_not_found",
                        source="conversation_store",
                    ),
                    {},
                )
            if _normalize_text(runtime.get("updated_case_summary")):
                await update_conversation_case_summary(
                    db,
                    conversation,
                    runtime.get("updated_case_summary") or "",
                )
            if isinstance(runtime.get("case_state_dict"), dict):
                await update_conversation_case_state(
                    db,
                    conversation,
                    runtime.get("case_state_dict"),
                )
            if _normalize_text(runtime.get("case_type")):
                await update_conversation_case_type(
                    db,
                    conversation,
                    runtime.get("case_type") or "",
                )
            await db.commit()
        return (
            _write_result(
                write_type="structured_case_persistence",
                step_name="fact_persistence",
                agent="conversation_store",
                status="success",
                input_summary=input_summary,
                output_summary="Conversation case fields updated",
                latency_ms=int((time.perf_counter() - started_at) * 1000),
                applied=True,
                source="conversation_store",
                details={},
            ),
            dict(runtime.get("persist_result") or {}),
        )
    finally:
        await celery_engine.dispose()


async def _apply_graph_update_schedule(
    request: dict[str, Any],
    runtime: dict[str, Any],
) -> dict[str, Any]:
    started_at = time.perf_counter()
    input_summary = _preview_text(request.get("input_text"))
    persist_result = (
        runtime.get("persist_result")
        if isinstance(runtime.get("persist_result"), dict)
        else {}
    )
    user_message_id = persist_result.get("user_message_id")
    try:
        normalized_user_message_id = int(user_message_id)
    except (TypeError, ValueError):
        normalized_user_message_id = None

    if normalized_user_message_id is None:
        return _write_result(
            write_type="graph_update_schedule",
            step_name="graph_update",
            agent="celery.update_graph_task",
            status="skipped",
            input_summary=input_summary,
            output_summary="missing_user_message_id",
            latency_ms=int((time.perf_counter() - started_at) * 1000),
            skip_reason="missing_user_message_id",
            source="celery.update_graph_task",
            details={},
        )

    scheduler = runtime.get("graph_update_scheduler")
    if scheduler is None:
        return _write_result(
            write_type="graph_update_schedule",
            step_name="graph_update",
            agent="celery.update_graph_task",
            status="failed",
            input_summary=input_summary,
            output_summary="graph_scheduler_missing",
            latency_ms=int((time.perf_counter() - started_at) * 1000),
            error="graph_scheduler_missing",
            source="celery.update_graph_task",
        )

    if hasattr(scheduler, "delay"):
        async_result = scheduler.delay(
            int(runtime.get("conversation_id") or 0),
            int(runtime.get("user_id") or 0),
            normalized_user_message_id,
        )
    else:
        async_result = scheduler(
            int(runtime.get("conversation_id") or 0),
            int(runtime.get("user_id") or 0),
            normalized_user_message_id,
        )
    async_result = await _maybe_await(async_result)
    task_id = _normalize_text(getattr(async_result, "id", "") or "")
    if not task_id:
        task_id = (
            f"scheduled:{int(runtime.get('conversation_id') or 0)}:{normalized_user_message_id}"
        )
    return _write_result(
        write_type="graph_update_schedule",
        step_name="graph_update",
        agent="celery.update_graph_task",
        status="success",
        input_summary=input_summary,
        output_summary=f"Graph update scheduled ({task_id})",
        latency_ms=int((time.perf_counter() - started_at) * 1000),
        applied=True,
        source="celery.update_graph_task",
        details={
            "task_id": task_id,
            "graph_update_source": _normalize_text(runtime.get("graph_update_source")) or "current_message",
        },
    )


def _blocked_write_result(request: dict[str, Any]) -> dict[str, Any]:
    write_type = _normalize_text(request.get("name")) or "unknown_write"
    blocked_by = [
        _normalize_text(item)
        for item in list(request.get("blocked_by") or [])
        if _normalize_text(item)
    ]
    step_name = "graph_update" if write_type == "graph_update_schedule" else "fact_persistence"
    agent = "celery.update_graph_task" if write_type == "graph_update_schedule" else "conversation_store"
    primary_reason = blocked_by[0] if blocked_by else "blocked"
    return _write_result(
        write_type=write_type,
        step_name=step_name,
        agent=agent,
        status="blocked",
        input_summary=_preview_text(request.get("input_text")),
        output_summary=", ".join(blocked_by) or "blocked",
        latency_ms=0,
        blocked_by=blocked_by,
        skip_reason=primary_reason,
        source=agent,
        details={"reason": _normalize_text(request.get("reason")) or None},
    )


async def execute_agentic_writes(
    *,
    writes_requested: list[dict[str, Any]] | None,
    writes_allowed: list[dict[str, Any]] | None,
    writes_blocked: list[dict[str, Any]] | None,
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    runtime = dict(context or {})
    ordered_writes = [
        dict(item)
        for item in list(writes_requested or [])
        if isinstance(item, dict)
    ]
    allowed_lookup = {
        (_normalize_text(item.get("name")), _normalize_text(item.get("input_text"))): dict(item)
        for item in list(writes_allowed or [])
        if isinstance(item, dict)
    }
    blocked_lookup = {
        (_normalize_text(item.get("name")), _normalize_text(item.get("input_text"))): dict(item)
        for item in list(writes_blocked or [])
        if isinstance(item, dict)
    }

    write_results: list[dict[str, Any]] = []
    applied_writes: list[dict[str, Any]] = []
    persist_result = (
        dict(runtime.get("persist_result"))
        if isinstance(runtime.get("persist_result"), dict)
        else {"user_message_id": None, "assistant_message_id": None}
    )
    runtime["persist_result"] = persist_result

    for request in ordered_writes:
        key = (_normalize_text(request.get("name")), _normalize_text(request.get("input_text")))
        if key in blocked_lookup or request.get("allowed") is False:
            blocked_request = blocked_lookup.get(key, request)
            blocked_result = _blocked_write_result(blocked_request)
            write_results.append(blocked_result)
            continue

        allowed_request = allowed_lookup.get(key, request)
        write_type = _normalize_text(allowed_request.get("name"))
        if write_type == "structured_case_persistence":
            started_at = time.perf_counter()
            try:
                write_result, persist_result = await _apply_structured_case_persistence(
                    allowed_request,
                    runtime,
                )
            except Exception as exc:
                write_result = _write_result(
                    write_type="structured_case_persistence",
                    step_name="fact_persistence",
                    agent="conversation_store",
                    status="failed",
                    input_summary=_preview_text(allowed_request.get("input_text")),
                    output_summary="structured_case_persistence_failed",
                    latency_ms=int((time.perf_counter() - started_at) * 1000),
                    error=str(exc),
                    source="conversation_store",
                    details={"error": str(exc)},
                )
            runtime["persist_result"] = persist_result
            write_results.append(write_result)
            if write_result.get("applied"):
                applied_writes.append(_applied_write_entry(write_result, allowed_request))
            continue

        if write_type == "graph_update_schedule":
            started_at = time.perf_counter()
            try:
                write_result = await _apply_graph_update_schedule(allowed_request, runtime)
            except Exception as exc:
                write_result = _write_result(
                    write_type="graph_update_schedule",
                    step_name="graph_update",
                    agent="celery.update_graph_task",
                    status="failed",
                    input_summary=_preview_text(allowed_request.get("input_text")),
                    output_summary="graph_update_schedule_failed",
                    latency_ms=int((time.perf_counter() - started_at) * 1000),
                    error=str(exc),
                    source="celery.update_graph_task",
                    details={"error": str(exc)},
                )
            write_results.append(write_result)
            if write_result.get("applied"):
                applied_writes.append(_applied_write_entry(write_result, allowed_request))
            continue

        skipped_result = _write_result(
            write_type=write_type or "unknown_write",
            step_name="unsupported_write",
            agent="agentic_write_gateway",
            status="skipped",
            input_summary=_preview_text(allowed_request.get("input_text")),
            output_summary="unsupported_write_type",
            latency_ms=0,
            skip_reason="unsupported_write_type",
            source="agentic_write_gateway",
            details={"reason": _normalize_text(allowed_request.get("reason")) or None},
        )
        write_results.append(skipped_result)

    return {
        "writes_requested": ordered_writes,
        "write_results": write_results,
        "applied_writes": applied_writes,
        "persist_result": runtime.get("persist_result") if isinstance(runtime.get("persist_result"), dict) else persist_result,
    }


__all__ = ["execute_agentic_writes"]
