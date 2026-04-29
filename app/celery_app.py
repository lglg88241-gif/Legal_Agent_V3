from __future__ import annotations

import asyncio
import inspect
import logging
import time
from typing import Any

from celery import Celery
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.security import AuthenticatedUser
from app.services.conversation_store import (
    ensure_storage_schema,
    get_conversation_for_user,
    get_message_for_conversation,
    load_dossier_context_for_conversation,
    list_recent_conversation_messages,
    load_conversation_case_summary,
    maybe_update_conversation_title,
    store_message,
    update_conversation_case_state,
    update_conversation_case_summary,
    update_conversation_case_type,
)
from app.services.conversation_mode import (
    normalize_conversation_mode,
    normalize_persistence_consent,
)
from app.services.case_followup import (
    build_structured_case_summary,
    is_case_fact_update_message,
    is_dossier_followup_question,
    should_persist_structured_case_message,
)
from app.services.dossier_graph import build_dossier_graph_summary_for_conversation
from app.services.dossier_graph_tool import (
    load_dossier_graph_context_for_conversation,
    should_include_dossier_context_for_advisor,
)
from app.services.graph_extractor import format_graph_memory, refresh_case_graph
from app.services.legal_agent import legal_agent
from app.services.message_understanding_agent import understand_message_with_agent

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


def _build_message_understanding_shadow_fallback(
    *,
    reason: str,
    warning: str,
) -> dict[str, Any]:
    return {
        "source": "message_understanding_agent",
        "intent": "unknown",
        "route_hint": "UNKNOWN",
        "needs_legal_rag": False,
        "needs_dossier_graph_context": False,
        "needs_structured_persistence": False,
        "needs_graph_update": False,
        "subject_reference": None,
        "fact_update": None,
        "previous_fact_confirmation": None,
        "advice_question": None,
        "legal_question": None,
        "confidence": "low",
        "reason": reason,
        "provider": "fallback",
        "model": "message-understanding-shadow-fallback-v1",
        "fallback_reason": reason,
        "trace": {
            "steps": ["shadow_mode"],
            "warnings": [warning] if warning else [],
        },
    }


async def _build_message_understanding_shadow_async(
    *,
    question: str,
    recent_messages: list[dict[str, Any]] | None,
    conversation_mode: str | None,
    dossier_id: int | None,
    conversation_id: int,
    dossier_context: dict[str, Any] | None,
    case_summary: str | None,
) -> dict[str, Any]:
    shadow_dossier_context = (
        dossier_context
        if isinstance(dossier_context, dict)
        else {
            "has_dossier": bool(dossier_id),
            "dossier_id": dossier_id,
            "conversation_id": conversation_id,
        }
    )
    shadow_recent_messages = recent_messages if isinstance(recent_messages, list) else []

    try:
        result = await understand_message_with_agent(
            message=question,
            recent_messages=shadow_recent_messages,
            conversation_mode=conversation_mode,
            dossier_id=dossier_id,
            dossier_context=shadow_dossier_context,
            case_summary=case_summary,
            use_llm=True,
        )
    except Exception as exc:
        logger.warning(
            "Message understanding shadow call failed | conversation_id=%s dossier_id=%s error=%s",
            conversation_id,
            dossier_id,
            exc,
        )
        return _build_message_understanding_shadow_fallback(
            reason="shadow_call_failed",
            warning=f"shadow_call_failed:{exc}",
        )

    trace = result.get("trace")
    if not isinstance(trace, dict):
        trace = {"steps": [], "warnings": []}
        result["trace"] = trace
    warnings = trace.get("warnings")
    if not isinstance(warnings, list):
        warnings = []
        trace["warnings"] = warnings
    if not shadow_recent_messages:
        warnings.append("recent_messages_unavailable")
    return result


def _build_message_understanding_shadow_action(
    message_understanding: dict[str, Any],
) -> dict[str, Any]:
    fallback_reason = str(message_understanding.get("fallback_reason") or "").strip() or None
    provider = str(message_understanding.get("provider") or "").strip() or "fallback"
    status = "success"
    if provider in {"fallback", "rules"} or fallback_reason:
        status = "fallback"
    if fallback_reason == "shadow_call_failed":
        status = "failed"
    return {
        "tool": "message_understanding_shadow",
        "stage": "message_understanding_shadow",
        "status": status,
        "intent": str(message_understanding.get("intent") or "unknown"),
        "route_hint": str(message_understanding.get("route_hint") or "UNKNOWN"),
        "provider": provider,
        "model": str(message_understanding.get("model") or ""),
        "fallback_reason": fallback_reason,
        "result_summary": (
            f"Message understanding shadow result recorded: "
            f"{message_understanding.get('intent', 'unknown')} -> "
            f"{message_understanding.get('route_hint', 'UNKNOWN')}"
        ),
    }


def _build_message_understanding_active_action(
    message_understanding: dict[str, Any],
) -> dict[str, Any]:
    return {
        "tool": "message_understanding",
        "stage": "message_understanding",
        "status": "success",
        "intent": str(message_understanding.get("intent") or "unknown"),
        "route_hint": str(message_understanding.get("route_hint") or "UNKNOWN"),
        "provider": str(message_understanding.get("provider") or ""),
        "model": str(message_understanding.get("model") or ""),
        "fallback_reason": str(message_understanding.get("fallback_reason") or "").strip() or None,
        "result_summary": (
            "Message understanding selected "
            f"{message_understanding.get('intent', 'unknown')} -> "
            f"{message_understanding.get('route_hint', 'UNKNOWN')}"
        ),
    }


def _build_dossier_memory_action() -> dict[str, Any]:
    return {
        "tool": "dossier_memory",
        "stage": "dossier_memory",
        "status": "success",
        "result_summary": "Dossier subject reference answered",
    }


def _normalize_subject_reference_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split()).lower()


def _is_subject_reference_message_understanding(
    message_understanding: dict[str, Any] | None,
) -> bool:
    if not isinstance(message_understanding, dict):
        return False
    return (
        str(message_understanding.get("intent") or "").strip().lower() == "subject_reference"
        and str(message_understanding.get("route_hint") or "").strip().upper() == "DOSSIER_MEMORY"
        and bool(message_understanding.get("needs_legal_rag")) is False
    )


def _subject_reference_type_matches(entity_type: Any, target_type: str) -> bool:
    normalized_entity_type = _normalize_subject_reference_text(entity_type)
    if target_type == "organization":
        return normalized_entity_type in {"organization", "company", "employer"}
    if target_type == "person":
        return normalized_entity_type in {"person", "individual"}
    if target_type == "self":
        return normalized_entity_type in {"person", "individual", "party"}
    if target_type == "role":
        return normalized_entity_type in {"role", "person", "organization"}
    return bool(normalized_entity_type)


def _build_subject_reference_candidates(
    *,
    message_understanding: dict[str, Any],
    dossier_context: dict[str, Any] | None,
    dossier_graph_context: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    subject_reference = (
        message_understanding.get("subject_reference")
        if isinstance(message_understanding, dict)
        else None
    )
    subject_reference = subject_reference if isinstance(subject_reference, dict) else {}
    mention = _normalize_subject_reference_text(subject_reference.get("mention"))
    target_type = _normalize_subject_reference_text(subject_reference.get("target_type")) or "unknown"

    candidates: dict[str, dict[str, Any]] = {}

    def register_candidate(
        label: str,
        *,
        entity_type: str,
        score: int,
        reason: str,
    ) -> None:
        cleaned_label = " ".join((label or "").strip().split())
        if not cleaned_label:
            return
        key = cleaned_label.lower()
        bucket = candidates.setdefault(
            key,
            {
                "label": cleaned_label,
                "entity_type": entity_type or "unknown",
                "score": 0,
                "reasons": [],
            },
        )
        bucket["score"] += score
        if reason not in bucket["reasons"]:
            bucket["reasons"].append(reason)
        if bucket["entity_type"] == "unknown" and entity_type:
            bucket["entity_type"] = entity_type

    dossier_mentions = []
    if isinstance(dossier_context, dict):
        dossier_mentions.extend(
            item
            for item in dossier_context.get("participant_mentions", [])
            if isinstance(item, dict)
        )
    if isinstance(dossier_graph_context, dict):
        dossier_mentions.extend(
            item
            for item in dossier_graph_context.get("participant_mentions", [])
            if isinstance(item, dict)
        )

    for mention_item in dossier_mentions:
        participant_label = str(mention_item.get("participant_label") or "").strip()
        if not participant_label:
            continue
        raw_label = _normalize_subject_reference_text(
            mention_item.get("raw_label") or mention_item.get("graph_node_label")
        )
        participant_entity_type = str(
            mention_item.get("participant_entity_type")
            or mention_item.get("graph_node_type")
            or ""
        ).strip()
        if mention and mention in raw_label:
            register_candidate(
                participant_label,
                entity_type=participant_entity_type,
                score=5,
                reason="mention_mapping",
            )
        elif target_type != "unknown" and _subject_reference_type_matches(participant_entity_type, target_type):
            register_candidate(
                participant_label,
                entity_type=participant_entity_type,
                score=2,
                reason="mention_type_match",
            )

    confirmed_participants = []
    if isinstance(dossier_context, dict):
        confirmed_participants.extend(
            item
            for item in dossier_context.get("confirmed_participants", [])
            if isinstance(item, dict)
        )
    if isinstance(dossier_graph_context, dict):
        confirmed_participants.extend(
            item
            for item in dossier_graph_context.get("participants", [])
            if isinstance(item, dict)
        )

    for participant in confirmed_participants:
        label = str(participant.get("label") or participant.get("display_label") or "").strip()
        if not label:
            continue
        entity_type = str(participant.get("entity_type") or "").strip()
        role = _normalize_subject_reference_text(participant.get("role"))
        description = _normalize_subject_reference_text(participant.get("description"))
        normalized_label = _normalize_subject_reference_text(label)
        score = 0
        reasons: list[str] = []
        if target_type != "unknown" and _subject_reference_type_matches(entity_type, target_type):
            score += 2
            reasons.append("participant_type_match")
        if mention and (
            mention in normalized_label
            or mention in role
            or mention in description
        ):
            score += 1
            reasons.append("participant_text_match")
        if score <= 0:
            continue
        for reason in reasons:
            register_candidate(label, entity_type=entity_type, score=score if reason == reasons[0] else 0, reason=reason)

    matched_nodes = (
        dossier_graph_context.get("matched_nodes", [])
        if isinstance(dossier_graph_context, dict)
        else []
    )
    for node in matched_nodes:
        if not isinstance(node, dict):
            continue
        label = str(
            node.get("display_label")
            or node.get("participant_label")
            or node.get("raw_label")
            or node.get("label")
            or ""
        ).strip()
        if not label:
            continue
        node_type = str(node.get("type") or node.get("graph_node_type") or "").strip()
        normalized_label = _normalize_subject_reference_text(label)
        raw_label = _normalize_subject_reference_text(node.get("raw_label") or node.get("label"))
        score = 0
        reasons: list[str] = []
        if target_type != "unknown" and _subject_reference_type_matches(node_type, target_type):
            score += 1
            reasons.append("matched_node_type")
        if mention and (mention in normalized_label or mention in raw_label):
            score += 2
            reasons.append("matched_node_text")
        if score <= 0:
            continue
        for reason in reasons:
            register_candidate(label, entity_type=node_type, score=score if reason == reasons[0] else 0, reason=reason)

    return sorted(
        candidates.values(),
        key=lambda item: (-int(item.get("score") or 0), str(item.get("label") or "")),
    )


def _build_subject_reference_response_text(
    *,
    question: str,
    message_understanding: dict[str, Any],
    dossier_context: dict[str, Any] | None,
    dossier_graph_context: dict[str, Any] | None,
) -> tuple[str, float]:
    _ = question
    subject_reference = (
        message_understanding.get("subject_reference")
        if isinstance(message_understanding, dict)
        else None
    )
    subject_reference = subject_reference if isinstance(subject_reference, dict) else {}
    mention = str(subject_reference.get("mention") or "").strip() or "这个主体"
    target_type = _normalize_subject_reference_text(subject_reference.get("target_type")) or "unknown"
    candidates = _build_subject_reference_candidates(
        message_understanding=message_understanding,
        dossier_context=dossier_context if isinstance(dossier_context, dict) else None,
        dossier_graph_context=dossier_graph_context if isinstance(dossier_graph_context, dict) else None,
    )

    if candidates:
        top_candidate = candidates[0]
        second_score = int(candidates[1].get("score") or 0) if len(candidates) > 1 else -1
        top_score = int(top_candidate.get("score") or 0)
        if len(candidates) == 1 or top_score >= second_score + 2:
            evidence_bits: list[str] = []
            reasons = set(top_candidate.get("reasons", []))
            if "mention_mapping" in reasons:
                evidence_bits.append(f"案卷里“{mention}”这一原始称呼已经映射到 {top_candidate['label']}")
            if "participant_type_match" in reasons or "mention_type_match" in reasons:
                evidence_bits.append(f"当前案卷的已确认主体记录中，最匹配的主体也是 {top_candidate['label']}")
            if "matched_node_text" in reasons or "matched_node_type" in reasons:
                evidence_bits.append(f"当前案卷图谱里与该称呼相关的节点同样指向 {top_candidate['label']}")
            if not evidence_bits:
                evidence_bits.append(f"当前案卷里的主体映射和图谱线索都更接近 {top_candidate['label']}")
            return (
                f"这里的“{mention}”目前指 {top_candidate['label']}。"
                f"依据是：{'；'.join(evidence_bits)}。"
                f"如果后续案卷里出现其他同类主体，系统不会自动把它们合并，仍需要你确认。",
                0.92 if top_score >= 5 else 0.75,
            )

        ambiguous_labels = "、".join(
            candidate["label"]
            for candidate in candidates[:3]
        )
        subject_type_text = "主体"
        if target_type == "organization":
            subject_type_text = "公司主体"
        elif target_type == "person":
            subject_type_text = "人物主体"
        elif target_type == "self":
            subject_type_text = "当事人主体"
        return (
            f"当前案卷里与“{mention}”相关的{subject_type_text}不止一个：{ambiguous_labels}。"
            "现在还不能直接确定你问的是哪一个，需要结合具体会话或进一步确认。"
            f"系统不会自动把多个{subject_type_text}视为同一个主体。",
            0.42,
        )

    return (
        f"当前案卷里还不能稳定确定“{mention}”具体指向哪个主体。"
        "现有主体映射或图谱线索还不足，暂时不能直接下结论。"
        "如果后续补充了更明确的主体映射，我可以再按案卷记录继续确认。",
        0.28,
    )


async def _answer_subject_reference_message_async(
    *,
    question: str,
    message_understanding: dict[str, Any],
    dossier_context: dict[str, Any] | None,
    dossier_graph_context: dict[str, Any] | None,
    case_summary: str | None,
) -> dict[str, Any]:
    _ = case_summary
    response_text, confidence_score = _build_subject_reference_response_text(
        question=question,
        message_understanding=message_understanding,
        dossier_context=dossier_context,
        dossier_graph_context=dossier_graph_context,
    )
    return {
        "response": response_text,
        "sources": [],
        "route_intent": "DOSSIER_MEMORY",
        "case_summary": case_summary or "",
        "is_final": True,
        "confidence_score": confidence_score,
        "missing_info": [],
    }


async def _load_controlled_dossier_graph_context_async(
    db: AsyncSession,
    *,
    user_id: int,
    conversation_id: int,
    user_message: str,
) -> dict[str, Any] | None:
    try:
        return await load_dossier_graph_context_for_conversation(
            db,
            current_user=AuthenticatedUser(user_id=user_id, username="worker"),
            conversation_id=conversation_id,
            user_message=(user_message or "").strip(),
            limit=12,
        )
    except Exception as exc:
        logger.warning(
            "Failed to load controlled dossier graph context | conversation_id=%s user_id=%s error=%s",
            conversation_id,
            user_id,
            exc,
        )
        return None


async def _run_task_async(
    conversation_id: int,
    user_id: int,
    current_question: str | None = None,
) -> dict[str, Any]:
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
        recent_messages = []
        for message in messages_12:
            content = " ".join((message.content or "").strip().split())
            if not content:
                continue
            recent_messages.append(
                {
                    "message_id": getattr(message, "id", None),
                    "role": message.role,
                    "content": content,
                }
            )

        async with session_factory() as db:
            case_summary = await load_conversation_case_summary(db, conversation_id)
        from app.services.conversation_store import load_conversation_case_state

        async with session_factory() as db:
            saved_case_state = await load_conversation_case_state(db, conversation_id)

        async with session_factory() as db:
            conversation = await get_conversation_for_user(db, user_id, conversation_id)
            persistence_consent = normalize_persistence_consent(
                getattr(conversation, "persistence_consent", None)
            )
            conversation_mode = normalize_conversation_mode(
                getattr(conversation, "conversation_mode", None)
            )
            dossier_id = getattr(conversation, "dossier_id", None) if conversation is not None else None

        async with session_factory() as db:
            dossier_context = await load_dossier_context_for_conversation(
                db,
                user_id,
                conversation_id,
            )
            try:
                dossier_graph_summary = await build_dossier_graph_summary_for_conversation(
                    db,
                    user_id=user_id,
                    conversation_id=conversation_id,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to build dossier graph summary | conversation_id=%s user_id=%s error=%s",
                    conversation_id,
                    user_id,
                    exc,
                )
                dossier_graph_summary = None
            dossier_graph_context = await _load_controlled_dossier_graph_context_async(
                db,
                user_id=user_id,
                conversation_id=conversation_id,
                user_message=(current_question or latest_user_input or "").strip(),
            )

        graph_memory = await format_graph_memory(conversation_id=conversation_id)
        return {
            "chat_history": chat_history,
            "latest_user_input": latest_user_input,
            "recent_messages": recent_messages,
            "case_summary": case_summary,
            "graph_memory": graph_memory,
            "saved_case_state": saved_case_state,
            "persistence_consent": persistence_consent,
            "conversation_mode": conversation_mode,
            "dossier_id": dossier_id,
            "dossier_context": dossier_context,
            "dossier_graph_summary": dossier_graph_summary,
            "dossier_graph_context": dossier_graph_context,
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
) -> dict[str, int | None]:
    celery_engine, session_factory = _create_celery_session_factory()
    try:
        await ensure_storage_schema(bind_engine=celery_engine)
        async with session_factory() as db:
            conversation = await get_conversation_for_user(db, user_id, conversation_id)
            if conversation is None:
                return {"user_message_id": None, "assistant_message_id": None}
            user_message = await store_message(
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
            assistant_message = await store_message(
                db,
                conversation_id=conversation_id,
                role="assistant",
                content=assistant_content,
                metadata=metadata,
            )
            await db.commit()
            return {
                "user_message_id": user_message.id,
                "assistant_message_id": assistant_message.id,
            }
    finally:
        await celery_engine.dispose()


@celery_app.task(name="update_graph_task")
def update_graph_task(conversation_id: int, user_id: int = 0, message_id: int | None = None):
    try:
        from app.memory.redis_mgr import get_redis_saver_for_current_loop

        get_redis_saver_for_current_loop()

        async def _run():
            celery_engine, session_factory = _create_celery_session_factory()
            try:
                await ensure_storage_schema(bind_engine=celery_engine)
                async with session_factory() as db:
                    if user_id <= 0:
                        raise ValueError("user_id is required for graph updates")

                    conversation = await get_conversation_for_user(db, user_id, conversation_id)
                    if conversation is None:
                        raise ValueError("conversation not found for current user")

                    if message_id is None:
                        raise ValueError("message_id is required for graph updates")

                    user_message = await get_message_for_conversation(
                        db,
                        conversation_id=conversation_id,
                        message_id=message_id,
                    )
                    if user_message is None:
                        raise ValueError("message_id not found in conversation")
                    if user_message.role != "user":
                        raise ValueError("message_id must point to a user message")

                    case_summary = await load_conversation_case_summary(db, conversation_id)
                    user_input = " ".join((user_message.content or "").strip().split())
                    if not user_input:
                        raise ValueError("message content is empty")
            finally:
                await celery_engine.dispose()

            logger.info(
                "Graph update task using bound message_id | conversation_id=%s user_id=%s message_id=%s",
                conversation_id,
                user_id,
                message_id,
            )

            return await refresh_case_graph(
                conversation_id=conversation_id,
                user_input=user_input,
                case_summary=case_summary,
                user_id=user_id,
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
            "message_id": message_id,
            "actions_applied": update_result.get("actions_applied", 0),
            "actions": update_result.get("actions", []),
        }
    except Exception as exc:
        logger.exception(
            "update_graph_task failed | conversation_id=%s user_id=%s message_id=%s",
            conversation_id,
            user_id,
            message_id,
        )
        return {
            "status": "failed",
            "conversation_id": conversation_id,
            "message_id": message_id,
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
        from app.memory.redis_mgr import get_redis_saver_for_current_loop

        get_redis_saver_for_current_loop()
        # 所有 IO 合并到单一事件循环，避免多次 asyncio.run() 的开销
        run_task_signature = inspect.signature(_run_task_async)
        if "current_question" in run_task_signature.parameters:
            context = asyncio.run(
                _run_task_async(
                    conversation_id,
                    user_id,
                    current_question=question,
                )
            )
        else:
            context = asyncio.run(_run_task_async(conversation_id, user_id))
        chat_history = context["chat_history"]
        case_summary = context["case_summary"]
        graph_memory = context["graph_memory"]
        persistence_consent = normalize_persistence_consent(context.get("persistence_consent"))
        conversation_mode = normalize_conversation_mode(context.get("conversation_mode"))
        raw_dossier_context = context.get("dossier_context")
        raw_dossier_graph_summary = context.get("dossier_graph_summary")
        raw_dossier_graph_context = context.get("dossier_graph_context")
        dossier_id = context.get("dossier_id")
        if not dossier_id and isinstance(raw_dossier_context, dict):
            dossier_id = raw_dossier_context.get("id")
        message_understanding = asyncio.run(
            _build_message_understanding_shadow_async(
                question=question,
                recent_messages=context.get("recent_messages"),
                conversation_mode=conversation_mode,
                dossier_id=dossier_id,
                conversation_id=conversation_id,
                dossier_context=raw_dossier_context if isinstance(raw_dossier_context, dict) else None,
                case_summary=case_summary,
            )
        )
        logger.info(
            "Message understanding result | intent=%s route_hint=%s provider=%s model=%s fallback_reason=%s",
            message_understanding.get("intent"),
            message_understanding.get("route_hint"),
            message_understanding.get("provider"),
            message_understanding.get("model"),
            message_understanding.get("fallback_reason"),
        )

        if _is_subject_reference_message_understanding(message_understanding):
            answer = asyncio.run(
                _answer_subject_reference_message_async(
                    question=question,
                    message_understanding=message_understanding,
                    dossier_context=raw_dossier_context if isinstance(raw_dossier_context, dict) else None,
                    dossier_graph_context=raw_dossier_graph_context if isinstance(raw_dossier_graph_context, dict) else None,
                    case_summary=case_summary,
                )
            )
            subject_reference_short_circuit = True
            advisor_dossier_context = None
            advisor_dossier_graph_summary = None
            advisor_dossier_graph_context = None
        else:
            subject_reference_short_circuit = False
        dossier_scope_hint = dossier_id or (1 if raw_dossier_context else None)
        include_dossier_context_for_advisor = should_include_dossier_context_for_advisor(
            conversation_mode=conversation_mode,
            dossier_id=dossier_scope_hint,
            user_message=question,
            persistence_consent=persistence_consent,
        )
        if not subject_reference_short_circuit:
            advisor_dossier_context = raw_dossier_context if include_dossier_context_for_advisor else None
            advisor_dossier_graph_summary = (
                raw_dossier_graph_summary if include_dossier_context_for_advisor else None
            )
            advisor_dossier_graph_context = (
                raw_dossier_graph_context if include_dossier_context_for_advisor else None
            )
            if raw_dossier_context and not include_dossier_context_for_advisor:
                logger.info(
                    "Advisor dossier context skipped for pure legal query | conversation_id=%s user_id=%s",
                    conversation_id,
                    user_id,
                )

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
                    dossier_context=advisor_dossier_context,
                    dossier_graph_summary=advisor_dossier_graph_summary,
                    dossier_graph_context=advisor_dossier_graph_context,
                )
            )

        latency_ms = int((time.time() - start_time) * 1000)
        response_text = answer["response"] if isinstance(answer, dict) else str(answer)
        response_sources = answer.get("sources", []) if isinstance(answer, dict) else []
        route_intent = answer.get("route_intent", "GENERAL_CONSULT") if isinstance(answer, dict) else "GENERAL_CONSULT"
        original_route_intent = route_intent
        updated_case_summary = answer.get("case_summary", "") if isinstance(answer, dict) else ""
        case_state_dict = answer.get("case_state_snapshot") if isinstance(answer, dict) else None
        case_type = answer.get("case_type") if isinstance(answer, dict) else None
        has_dossier_context = bool(raw_dossier_context)
        dossier_followup_question = has_dossier_context and is_dossier_followup_question(question)
        message_is_fact_update = is_case_fact_update_message(question)
        if route_intent == "CASE_UPDATE" and dossier_followup_question and not message_is_fact_update:
            logger.info(
                "Structured case persistence skipped because message is dossier follow-up question | conversation_id=%s user_id=%s route_intent=%s message_is_fact_update=%s",
                conversation_id,
                user_id,
                original_route_intent,
                message_is_fact_update,
            )
            logger.info(
                "Graph update skipped because message is dossier follow-up question | conversation_id=%s user_id=%s message_is_fact_update=%s",
                conversation_id,
                user_id,
                message_is_fact_update,
            )
            route_intent = "GENERAL_CONSULT"
            updated_case_summary = context.get("case_summary") or ""
            case_state_dict = None
            case_type = None
        should_persist_structured_case = should_persist_structured_case_message(
            text=question,
            route_intent=route_intent,
            persistence_consent=persistence_consent,
            conversation_mode=conversation_mode,
            has_dossier_context=has_dossier_context,
        )
        if should_persist_structured_case:
            updated_case_summary = build_structured_case_summary(
                current_summary=context.get("case_summary"),
                user_message=question,
                proposed_summary=updated_case_summary,
            )
            if route_intent != "CASE_UPDATE":
                logger.info(
                    "Structured case persistence promoted for tracked factual message | conversation_id=%s user_id=%s original_route_intent=%s",
                    conversation_id,
                    user_id,
                    original_route_intent,
                )

        if route_intent == "GREETING":
            agent_actions = [
                {"tool": "tool_router", "result_summary": "Greeting route selected"},
                {"tool": "greeting", "result_summary": "Fast greeting response generated"},
            ]
        elif route_intent == "DOSSIER_MEMORY":
            agent_actions = [
                _build_message_understanding_active_action(message_understanding),
                _build_dossier_memory_action(),
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
        if route_intent != "DOSSIER_MEMORY":
            agent_actions.append(_build_message_understanding_shadow_action(message_understanding))

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
            "message_understanding": message_understanding,
            "case_summary": updated_case_summary,
            "latency_ms": latency_ms,
        }

        persist_result = {"user_message_id": None, "assistant_message_id": None}

        if route_intent != "GREETING":
            persist_result = asyncio.run(
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
                        "message_understanding": message_understanding,
                    },
                    case_summary=updated_case_summary if should_persist_structured_case else "",
                    case_state_dict=case_state_dict if should_persist_structured_case else None,
                    case_type=case_type if should_persist_structured_case else None,
                )
            )

        if should_persist_structured_case:
            user_message_id = persist_result.get("user_message_id")
            if user_message_id:
                update_graph_task.delay(conversation_id, user_id, user_message_id)
            else:
                logger.warning(
                    "Skip graph update because persisted user message id is missing | conversation_id=%s user_id=%s",
                    conversation_id,
                    user_id,
                )
        elif route_intent == "CASE_UPDATE":
            if persistence_consent == "temporary":
                logger.info(
                    "Structured case persistence skipped due to temporary consent | conversation_id=%s user_id=%s route_intent=%s",
                    conversation_id,
                    user_id,
                    route_intent,
                )
                logger.info(
                    "Graph update skipped due to temporary consent | conversation_id=%s user_id=%s",
                    conversation_id,
                    user_id,
                )
            elif dossier_followup_question and not message_is_fact_update:
                logger.info(
                    "Structured case persistence skipped because message is dossier follow-up question | conversation_id=%s user_id=%s route_intent=%s message_is_fact_update=%s",
                    conversation_id,
                    user_id,
                    route_intent,
                    message_is_fact_update,
                )
                logger.info(
                    "Graph update skipped because message is dossier follow-up question | conversation_id=%s user_id=%s message_is_fact_update=%s",
                    conversation_id,
                    user_id,
                    message_is_fact_update,
                )
            else:
                logger.info(
                    "Structured case persistence skipped because CASE_UPDATE message was not classified as a persistable factual update | conversation_id=%s user_id=%s route_intent=%s message_is_fact_update=%s dossier_followup_question=%s",
                    conversation_id,
                    user_id,
                    route_intent,
                    message_is_fact_update,
                    dossier_followup_question,
                )
                logger.info(
                    "Graph update skipped because CASE_UPDATE message was not classified as a persistable factual update | conversation_id=%s user_id=%s message_is_fact_update=%s dossier_followup_question=%s",
                    conversation_id,
                    user_id,
                    message_is_fact_update,
                    dossier_followup_question,
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
