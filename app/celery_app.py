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
from app.services.case_update_acknowledgement import build_case_update_acknowledgement
from app.services.dossier_graph import build_dossier_graph_summary_for_conversation
from app.services.dossier_graph_tool import (
    load_dossier_graph_context_for_conversation,
    should_include_dossier_context_for_advisor,
)
from app.services.execution_trace import ExecutionTrace
from app.services.graph_extractor import format_graph_memory, refresh_case_graph
from app.services.legal_agent import legal_agent
from app.services.message_orchestrator import build_execution_plan_from_message_understanding
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


def _build_execution_plan_action(
    execution_plan: dict[str, Any],
) -> dict[str, Any]:
    steps = execution_plan.get("steps") if isinstance(execution_plan.get("steps"), list) else []
    step_descriptions = [str(step.get("name") or "").strip() for step in steps if isinstance(step, dict)]
    step_summary = ", ".join([item for item in step_descriptions if item]) or "legacy_fallback"
    return {
        "tool": "message_orchestrator",
        "stage": "message_orchestrator",
        "status": "success" if execution_plan.get("is_actionable") else "fallback",
        "primary_route": str(execution_plan.get("primary_route") or "UNKNOWN"),
        "result_summary": (
            "Execution plan selected "
            f"{execution_plan.get('primary_route', 'UNKNOWN')}: {step_summary}"
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


def _subject_reference_scope_order(subject_reference: dict[str, Any]) -> list[str]:
    scope_order: list[str] = []
    for item in subject_reference.get("scope_order", []):
        normalized = _normalize_subject_reference_text(item)
        if normalized and normalized not in scope_order:
            scope_order.append(normalized)
    if scope_order:
        return scope_order
    return [
        "current_conversation_graph",
        "current_conversation_messages",
        "dossier_confirmed_entities",
        "dossier_cross_conversation_graph",
    ]


def _subject_reference_mention_tokens(subject_reference: dict[str, Any]) -> list[str]:
    tokens = [
        _normalize_subject_reference_text(subject_reference.get("target_mention")),
        _normalize_subject_reference_text(subject_reference.get("mention")),
        _normalize_subject_reference_text(subject_reference.get("target_span")),
    ]
    return [token for token in tokens if token]


def _build_subject_reference_candidates_for_scope(
    *,
    scope_name: str,
    subject_reference: dict[str, Any],
    dossier_context: dict[str, Any] | None,
    dossier_graph_context: dict[str, Any] | None,
    recent_messages: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    mention_tokens = _subject_reference_mention_tokens(subject_reference)
    target_type = _normalize_subject_reference_text(subject_reference.get("target_type")) or "unknown"
    active_conversation_id = 0
    if isinstance(dossier_context, dict):
        try:
            active_conversation_id = int(dossier_context.get("active_conversation_id") or 0)
        except (TypeError, ValueError):
            active_conversation_id = 0

    candidates: dict[str, dict[str, Any]] = {}

    def register_candidate(
        label: str,
        *,
        entity_type: str,
        score: int,
        reason: str,
        scope: str,
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
                "scope": scope,
            },
        )
        bucket["score"] += score
        if reason not in bucket["reasons"]:
            bucket["reasons"].append(reason)
        if bucket["entity_type"] == "unknown" and entity_type:
            bucket["entity_type"] = entity_type

    def matches_scope(conversation_id: Any) -> bool:
        try:
            normalized_conversation_id = int(conversation_id or 0)
        except (TypeError, ValueError):
            normalized_conversation_id = 0
        if scope_name == "current_conversation_graph":
            return active_conversation_id > 0 and normalized_conversation_id == active_conversation_id
        if scope_name == "dossier_cross_conversation_graph":
            return active_conversation_id > 0 and normalized_conversation_id > 0 and normalized_conversation_id != active_conversation_id
        return False

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
        if scope_name in {"current_conversation_graph", "dossier_cross_conversation_graph"} and not matches_scope(
            mention_item.get("conversation_id")
        ):
            continue
        raw_label = _normalize_subject_reference_text(
            mention_item.get("raw_label") or mention_item.get("graph_node_label")
        )
        participant_entity_type = str(
            mention_item.get("participant_entity_type")
            or mention_item.get("graph_node_type")
            or ""
        ).strip()
        if mention_tokens and any(token in raw_label for token in mention_tokens):
            register_candidate(
                participant_label,
                entity_type=participant_entity_type,
                score=5,
                reason="mention_mapping",
                scope=scope_name,
            )
        elif target_type != "unknown" and _subject_reference_type_matches(participant_entity_type, target_type):
            register_candidate(
                participant_label,
                entity_type=participant_entity_type,
                score=2,
                reason="mention_type_match",
                scope=scope_name,
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
        if scope_name not in {"dossier_confirmed_entities", "current_conversation_messages"}:
            continue
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
        if mention_tokens and any(
            token in normalized_label or token in role or token in description
            for token in mention_tokens
        ):
            score += 1
            reasons.append("participant_text_match")
        if scope_name == "current_conversation_messages" and recent_messages:
            recent_text = " ".join(
                _normalize_subject_reference_text(item.get("content"))
                for item in recent_messages
                if isinstance(item, dict)
                and str(item.get("role") or "").strip().lower() == "user"
            )
            if normalized_label and normalized_label in recent_text:
                score += 2
                reasons.append("message_context_match")
            elif mention_tokens and any(token in recent_text for token in mention_tokens):
                score += 1
                reasons.append("message_mention_match")
        if score <= 0:
            continue
        for reason in reasons:
            register_candidate(
                label,
                entity_type=entity_type,
                score=score if reason == reasons[0] else 0,
                reason=reason,
                scope=scope_name,
            )

    matched_nodes = (
        dossier_graph_context.get("matched_nodes", [])
        if isinstance(dossier_graph_context, dict)
        else []
    )
    for node in matched_nodes:
        if scope_name not in {"current_conversation_graph", "dossier_cross_conversation_graph"}:
            continue
        if not isinstance(node, dict):
            continue
        if not matches_scope(node.get("conversation_id")):
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
        if mention_tokens and any(token in normalized_label or token in raw_label for token in mention_tokens):
            score += 2
            reasons.append("matched_node_text")
        if score <= 0:
            continue
        for reason in reasons:
            register_candidate(
                label,
                entity_type=node_type,
                score=score if reason == reasons[0] else 0,
                reason=reason,
                scope=scope_name,
            )

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
    recent_messages: list[dict[str, Any]] | None,
) -> tuple[str, float, dict[str, Any]]:
    _ = question
    subject_reference = (
        message_understanding.get("subject_reference")
        if isinstance(message_understanding, dict)
        else None
    )
    subject_reference = subject_reference if isinstance(subject_reference, dict) else {}
    mention = (
        str(subject_reference.get("target_mention") or "").strip()
        or str(subject_reference.get("mention") or "").strip()
        or "这个主体"
    )
    target_type = _normalize_subject_reference_text(subject_reference.get("target_type")) or "unknown"
    scope_order = _subject_reference_scope_order(subject_reference)
    candidate_count_by_scope: dict[str, int] = {}
    selected_scope = ""
    ambiguity = False
    whether_escalated_to_dossier = False

    for scope_name in scope_order:
        candidates = _build_subject_reference_candidates_for_scope(
            scope_name=scope_name,
            subject_reference=subject_reference,
            dossier_context=dossier_context if isinstance(dossier_context, dict) else None,
            dossier_graph_context=dossier_graph_context if isinstance(dossier_graph_context, dict) else None,
            recent_messages=recent_messages,
        )
        candidate_count_by_scope[scope_name] = len(candidates)
        if not candidates:
            continue

        selected_scope = scope_name
        whether_escalated_to_dossier = scope_name.startswith("dossier_")
        top_candidate = candidates[0]
        second_score = int(candidates[1].get("score") or 0) if len(candidates) > 1 else -1
        top_score = int(top_candidate.get("score") or 0)
        ambiguity = len(candidates) > 1 and top_score < second_score + 2
        if ambiguity and bool(subject_reference.get("needs_clarification_if_ambiguous")):
            ambiguous_labels = "、".join(candidate["label"] for candidate in candidates[:3])
            return (
                f"我现在还不能直接确定“{mention}”指的是哪一个主体，当前可匹配的候选有：{ambiguous_labels}。"
                "需要你再补充一句上下文，或确认它具体对应哪一个主体。",
                0.42,
                {
                    "selected_scope": selected_scope,
                    "candidate_count_by_scope": candidate_count_by_scope,
                    "ambiguity": True,
                    "whether_escalated_to_dossier": whether_escalated_to_dossier,
                },
            )

        if scope_name == "current_conversation_graph":
            return (
                f"在当前会话里，这里的“{mention}”指 {top_candidate['label']}。"
                "当前会话图谱里的主体映射已经直接指向这个主体。"
                "如果后续又出现新的同类主体，仍需要你确认是否是同一主体。",
                0.92 if top_score >= 5 else 0.78,
                {
                    "selected_scope": selected_scope,
                    "candidate_count_by_scope": candidate_count_by_scope,
                    "ambiguity": False,
                    "whether_escalated_to_dossier": False,
                },
            )
        if scope_name == "current_conversation_messages":
            return (
                f"结合当前会话的上下文，这里的“{mention}”更可能指 {top_candidate['label']}。"
                "这个判断主要来自当前会话里已出现的主体称呼和消息上下文。"
                "如果你指的是别的主体，可以直接点明具体名字。",
                0.74,
                {
                    "selected_scope": selected_scope,
                    "candidate_count_by_scope": candidate_count_by_scope,
                    "ambiguity": False,
                    "whether_escalated_to_dossier": False,
                },
            )
        if scope_name in {"dossier_confirmed_entities", "dossier_cross_conversation_graph"}:
            return (
                f"当前会话里还没有直接确认“{mention}”对应的主体。"
                f"从案卷其他记录看，它更可能指 {top_candidate['label']}，但仍需要你确认这是否是同一主体。"
                "在你确认之前，系统不会自动把跨会话主体合并。",
                0.58,
                {
                    "selected_scope": selected_scope,
                    "candidate_count_by_scope": candidate_count_by_scope,
                    "ambiguity": False,
                    "whether_escalated_to_dossier": True,
                },
            )

    return (
        f"当前还不能稳定确定“{mention}”具体指向哪个主体。"
        "当前会话和案卷里的主体映射线索都还不够，需要你再补充一句上下文或直接给出主体名称。",
        0.28,
        {
            "selected_scope": "",
            "candidate_count_by_scope": candidate_count_by_scope,
            "ambiguity": False,
            "whether_escalated_to_dossier": False,
        },
    )


async def _answer_subject_reference_message_async(
    *,
    question: str,
    message_understanding: dict[str, Any],
    dossier_context: dict[str, Any] | None,
    dossier_graph_context: dict[str, Any] | None,
    case_summary: str | None,
    recent_messages: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    _ = case_summary
    response_text, confidence_score, resolution_trace = _build_subject_reference_response_text(
        question=question,
        message_understanding=message_understanding,
        dossier_context=dossier_context,
        dossier_graph_context=dossier_graph_context,
        recent_messages=recent_messages,
    )
    return {
        "response": response_text,
        "sources": [],
        "route_intent": "DOSSIER_MEMORY",
        "case_summary": case_summary or "",
        "is_final": True,
        "confidence_score": confidence_score,
        "missing_info": [],
        "resolution_trace": resolution_trace,
    }


def _looks_like_recent_factual_user_message(text: str) -> bool:
    normalized = " ".join((text or "").strip().split())
    if not normalized:
        return False
    if normalized in {"我确认这个事实", "确认这个事实", "这个事实属实", "上一条是真的"}:
        return False
    if "?" in normalized or "？" in normalized:
        return False
    return is_case_fact_update_message(normalized) or len(normalized) >= 8


def _resolve_confirm_previous_fact_text(
    *,
    question: str,
    message_understanding: dict[str, Any],
    recent_messages: list[dict[str, Any]] | None,
) -> str:
    previous_fact_confirmation = (
        message_understanding.get("previous_fact_confirmation")
        if isinstance(message_understanding.get("previous_fact_confirmation"), dict)
        else {}
    )
    target_text = " ".join(
        str(previous_fact_confirmation.get("target_text") or "").strip().split()
    )
    if target_text and target_text != " ".join((question or "").strip().split()):
        return target_text

    target_message_id = previous_fact_confirmation.get("target_message_id")
    normalized_target_message_id = None
    try:
        normalized_target_message_id = int(target_message_id)
    except (TypeError, ValueError):
        normalized_target_message_id = None

    candidates = list(recent_messages or [])
    for item in reversed(candidates):
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "").strip().lower() != "user":
            continue
        content = " ".join(str(item.get("content") or "").strip().split())
        if not content:
            continue
        item_message_id = item.get("message_id") or item.get("id")
        try:
            normalized_item_message_id = int(item_message_id)
        except (TypeError, ValueError):
            normalized_item_message_id = None
        if normalized_target_message_id is not None and normalized_item_message_id == normalized_target_message_id:
            return content
        if _looks_like_recent_factual_user_message(content):
            return content

    return ""


def _build_fact_confirmation_action(confirmed_fact_text: str) -> dict[str, Any]:
    preview = " ".join((confirmed_fact_text or "").strip().split())
    if len(preview) > 48:
        preview = preview[:45].rstrip() + "..."
    return {
        "tool": "fact_confirmation",
        "stage": "fact_confirmation",
        "status": "success",
        "result_summary": f"Previous fact confirmed for graph update: {preview}",
    }


def _build_graph_sync_action() -> dict[str, Any]:
    return {
        "tool": "graph_sync",
        "stage": "graph_sync",
        "status": "success",
        "result_summary": "Graph update scheduled from confirmed previous fact",
    }


def _build_case_update_acknowledgement_action(
    acknowledgement_result: dict[str, Any],
) -> dict[str, Any]:
    fallback_used = bool(acknowledgement_result.get("fallback_used"))
    return {
        "tool": "case_update_acknowledgement",
        "stage": "case_update_acknowledgement",
        "status": "fallback" if fallback_used else "success",
        "provider": str(acknowledgement_result.get("provider") or "rules"),
        "model": str(acknowledgement_result.get("model") or ""),
        "fallback_reason": str(acknowledgement_result.get("fallback_reason") or "").strip() or None,
        "result_summary": "Lightweight case update acknowledgement generated",
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
    user_metadata: dict[str, Any] | None = None,
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
                metadata=user_metadata or {},
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


async def _merge_message_metadata_async(
    *,
    conversation_id: int,
    message_id: int | None,
    metadata_patch: dict[str, Any] | None,
) -> None:
    if not message_id or not isinstance(metadata_patch, dict) or not metadata_patch:
        return
    celery_engine, session_factory = _create_celery_session_factory()
    try:
        await ensure_storage_schema(bind_engine=celery_engine)
        async with session_factory() as db:
            message = await get_message_for_conversation(
                db,
                conversation_id=conversation_id,
                message_id=message_id,
            )
            if message is None:
                return
            existing_metadata = message.metadata if isinstance(message.metadata, dict) else {}
            message.metadata = {
                **existing_metadata,
                **metadata_patch,
            }
            db.add(message)
            await db.commit()
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
                    metadata = (
                        user_message.message_metadata
                        if isinstance(getattr(user_message, "message_metadata", None), dict)
                        else {}
                    )
                    graph_update_input_text = " ".join(
                        str(metadata.get("graph_update_input_text") or "").strip().split()
                    )
                    user_input = graph_update_input_text or " ".join((user_message.content or "").strip().split())
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
            if graph_update_input_text:
                logger.info(
                    "Graph update task using override input text from message metadata | conversation_id=%s user_id=%s message_id=%s",
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
    request_id = str(getattr(getattr(self, "request", None), "id", "") or "").strip()
    if not request_id:
        request_id = f"{conversation_id}:{session_id}:{int(start_time * 1000)}"
    execution_trace = ExecutionTrace(request_id=request_id)

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
        message_understanding_started_at = time.time()
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
        message_understanding_latency_ms = int(
            max(0.0, (time.time() - message_understanding_started_at) * 1000)
        )
        message_understanding_fallback_used = bool(
            str(message_understanding.get("provider") or "").strip().lower() in {"fallback", "rules"}
            or str(message_understanding.get("fallback_reason") or "").strip()
        )
        execution_trace.set_message_understanding(
            intent=message_understanding.get("intent"),
            route_hint=message_understanding.get("route_hint"),
            provider=message_understanding.get("provider"),
            model=message_understanding.get("model"),
            fallback_used=message_understanding_fallback_used,
            fallback_reason=str(message_understanding.get("fallback_reason") or "").strip() or None,
            latency_ms=message_understanding_latency_ms,
        )
        logger.info(
            "Message understanding result | intent=%s route_hint=%s provider=%s model=%s fallback_reason=%s",
            message_understanding.get("intent"),
            message_understanding.get("route_hint"),
            message_understanding.get("provider"),
            message_understanding.get("model"),
            message_understanding.get("fallback_reason"),
        )
        execution_plan = build_execution_plan_from_message_understanding(
            message_understanding,
            original_message=question,
            recent_messages=context.get("recent_messages"),
            dossier_id=dossier_id,
            persistence_consent=persistence_consent,
        )
        execution_plan_steps = (
            execution_plan.get("steps") if isinstance(execution_plan.get("steps"), list) else []
        )
        execution_plan_step_names = {
            str(step.get("name") or "").strip()
            for step in execution_plan_steps
            if isinstance(step, dict) and str(step.get("name") or "").strip()
        }
        execution_plan_trace = (
            execution_plan.get("trace") if isinstance(execution_plan.get("trace"), dict) else {}
        )
        execution_plan_resolved_inputs = (
            execution_plan.get("resolved_inputs")
            if isinstance(execution_plan.get("resolved_inputs"), dict)
            else {}
        )
        execution_plan_uses_legacy_fallback = bool(execution_plan_trace.get("use_legacy_fallback"))
        execution_trace.set_execution_plan(
            primary_route=execution_plan.get("primary_route"),
            actionable=bool(execution_plan.get("is_actionable")),
            step_names=sorted(execution_plan_step_names),
        )
        subject_reference_short_circuit = (
            bool(execution_plan.get("is_actionable"))
            and not execution_plan_uses_legacy_fallback
            and str(execution_plan.get("primary_route") or "").strip().upper() == "DOSSIER_MEMORY"
            and "dossier_memory_answer" in execution_plan_step_names
        )
        case_update_plan_active = (
            bool(execution_plan.get("is_actionable"))
            and not execution_plan_uses_legacy_fallback
            and str(execution_plan.get("primary_route") or "").strip().upper() == "CASE_UPDATE"
            and "fact_persistence" in execution_plan_step_names
        )
        case_update_acknowledgement_active = (
            case_update_plan_active
            and str(execution_plan.get("intent") or "").strip().lower()
            in {"fact_update", "confirm_previous_fact"}
            and "assistant_acknowledgement" in execution_plan_step_names
        )
        confirm_previous_fact_active = (
            case_update_plan_active
            and "fact_confirmation" in execution_plan_step_names
            and bool(execution_plan_resolved_inputs.get("previous_fact_text"))
        )
        confirm_previous_fact_text = " ".join(
            str(execution_plan_resolved_inputs.get("previous_fact_text") or "").strip().split()
        )
        effective_persistence_text = " ".join(
            str(execution_plan_resolved_inputs.get("effective_persistence_text") or "").strip().split()
        ) or question
        graph_update_input_text = " ".join(
            str(execution_plan_resolved_inputs.get("graph_update_input_text") or "").strip().split()
        )
        case_update_blocked_by_legal_qa = (
            case_update_plan_active and conversation_mode == "LEGAL_QA"
        )
        case_update_graph_update_planned = (
            case_update_acknowledgement_active
            and persistence_consent != "temporary"
            and not case_update_blocked_by_legal_qa
        )
        case_update_acknowledgement_result: dict[str, Any] | None = None
        legacy_fallback_reason: str | None = None
        missing_previous_fact = bool(
            str(execution_plan.get("intent") or "").strip().lower() == "confirm_previous_fact"
            and str(execution_plan.get("primary_route") or "").strip().upper() == "CASE_UPDATE"
            and "fact_confirmation" in execution_plan_step_names
            and not confirm_previous_fact_active
        )
        execution_trace.add_guard(
            name="temporary_blocks_persistence",
            triggered=bool(case_update_plan_active and persistence_consent == "temporary"),
        )
        execution_trace.add_guard(
            name="legal_qa_blocks_graph",
            triggered=bool(case_update_blocked_by_legal_qa),
        )
        execution_trace.add_guard(
            name="missing_previous_fact",
            triggered=missing_previous_fact,
        )
        logger.info(
            "Message execution plan | primary_route=%s actionable=%s steps=%s fallback=%s",
            execution_plan.get("primary_route"),
            execution_plan.get("is_actionable"),
            sorted(execution_plan_step_names),
            execution_plan_uses_legacy_fallback,
        )

        if subject_reference_short_circuit:
            with execution_trace.step_context(
                name="dossier_memory_subject_reference",
                agent="dossier_memory_helper",
            ):
                answer = asyncio.run(
                    _answer_subject_reference_message_async(
                        question=question,
                        message_understanding=message_understanding,
                        dossier_context=raw_dossier_context if isinstance(raw_dossier_context, dict) else None,
                        dossier_graph_context=raw_dossier_graph_context if isinstance(raw_dossier_graph_context, dict) else None,
                        case_summary=case_summary,
                    )
                )
            advisor_dossier_context = None
            advisor_dossier_graph_summary = None
            advisor_dossier_graph_context = None
            execution_trace.mark_legacy_fallback(used=False, reason=None)
        else:
            dossier_scope_hint = dossier_id or (1 if raw_dossier_context else None)
            include_dossier_context_for_advisor = should_include_dossier_context_for_advisor(
                conversation_mode=conversation_mode,
                dossier_id=dossier_scope_hint,
                user_message=question,
                persistence_consent=persistence_consent,
            )
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

            if case_update_acknowledgement_active:
                try:
                    with execution_trace.step_context(
                        name="assistant_acknowledgement",
                        agent="case_update_acknowledgement",
                        provider="rules",
                        model="case-update-ack-rules-v1",
                    ):
                        case_update_acknowledgement_result = build_case_update_acknowledgement(
                            plan=execution_plan,
                            intent=str(execution_plan.get("intent") or ""),
                            user_message_text=question,
                            persisted_fact_text=effective_persistence_text,
                            graph_update_scheduled=case_update_graph_update_planned,
                            graph_update_actions=None,
                            dossier_id=dossier_id,
                            conversation_id=conversation_id,
                            persistence_consent=persistence_consent,
                            route_intent="CASE_UPDATE",
                            fallback_reason=(
                                str(execution_plan_trace.get("fallback_reason") or "").strip() or None
                            ),
                        )
                    answer = {
                        "response": str(case_update_acknowledgement_result.get("text") or ""),
                        "sources": [],
                        "route_intent": "CASE_UPDATE",
                        "case_summary": context.get("case_summary") or "",
                        "is_final": True,
                        "confidence_score": 0.92,
                        "missing_info": [],
                    }
                except Exception as exc:
                    logger.exception(
                        "Case update acknowledgement specialist failed, falling back to legacy advisor | conversation_id=%s user_id=%s error=%s",
                        conversation_id,
                        user_id,
                        exc,
                    )
                    case_update_acknowledgement_result = None
                    legacy_fallback_reason = "case_update_acknowledgement_failed"
                    execution_trace.mark_legacy_fallback(
                        used=True,
                        reason=legacy_fallback_reason,
                    )
                    with execution_trace.step_context(
                        name="legal_agent_answer",
                        agent="legal_agent",
                    ):
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
            else:
                if execution_plan_uses_legacy_fallback:
                    legacy_fallback_reason = (
                        str(execution_plan_trace.get("fallback_reason") or "").strip()
                        or "message_understanding_fallback"
                    )
                elif missing_previous_fact:
                    legacy_fallback_reason = "missing_previous_fact"
                else:
                    legacy_fallback_reason = "no_actionable_execution_plan"
                execution_trace.mark_legacy_fallback(
                    used=True,
                    reason=legacy_fallback_reason,
                )
                with execution_trace.step_context(
                    name="legal_agent_answer",
                    agent="legal_agent",
                ):
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
        if subject_reference_short_circuit:
            route_intent = "DOSSIER_MEMORY"
        elif case_update_plan_active:
            route_intent = "CASE_UPDATE"
        updated_case_summary = answer.get("case_summary", "") if isinstance(answer, dict) else ""
        case_state_dict = answer.get("case_state_snapshot") if isinstance(answer, dict) else None
        case_type = answer.get("case_type") if isinstance(answer, dict) else None
        has_dossier_context = bool(raw_dossier_context)
        dossier_followup_question = has_dossier_context and is_dossier_followup_question(question)
        if case_update_plan_active:
            updated_case_summary = context.get("case_summary") or ""
            case_state_dict = None
            case_type = None
        message_is_fact_update = case_update_plan_active or is_case_fact_update_message(question)
        if "fact_confirmation" in execution_plan_step_names:
            if confirm_previous_fact_active:
                execution_trace.add_step(
                    name="fact_confirmation",
                    agent="message_orchestrator",
                    status="success",
                    details={
                        "resolved_previous_fact_text": confirm_previous_fact_text,
                    },
                )
            else:
                execution_trace.add_step(
                    name="fact_confirmation",
                    agent="message_orchestrator",
                    status="skipped",
                    skip_reason="missing_previous_fact",
                )
        execution_trace.add_guard(
            name="dossier_followup_question_blocks_persistence",
            triggered=bool(
                route_intent == "CASE_UPDATE"
                and dossier_followup_question
                and not message_is_fact_update
                and not case_update_plan_active
            ),
        )
        if (
            route_intent == "CASE_UPDATE"
            and dossier_followup_question
            and not message_is_fact_update
            and not case_update_plan_active
        ):
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
        if case_update_plan_active:
            should_persist_structured_case = (
                persistence_consent != "temporary"
                and not case_update_blocked_by_legal_qa
            )
        else:
            should_persist_structured_case = should_persist_structured_case_message(
                text=effective_persistence_text,
                route_intent=route_intent,
                persistence_consent=persistence_consent,
                conversation_mode=conversation_mode,
                has_dossier_context=has_dossier_context,
            )
        execution_trace.add_guard(
            name="non_persistable_case_update",
            triggered=bool(
                route_intent == "CASE_UPDATE"
                and not should_persist_structured_case
                and persistence_consent != "temporary"
                and not case_update_blocked_by_legal_qa
                and not (
                    dossier_followup_question
                    and not message_is_fact_update
                    and not case_update_plan_active
                )
            ),
        )
        if should_persist_structured_case:
            updated_case_summary = build_structured_case_summary(
                current_summary=context.get("case_summary"),
                user_message=effective_persistence_text,
                proposed_summary=updated_case_summary,
            )
            if route_intent != "CASE_UPDATE":
                logger.info(
                    "Structured case persistence promoted for tracked factual message | conversation_id=%s user_id=%s original_route_intent=%s",
                    conversation_id,
                    user_id,
                    original_route_intent,
                )
        elif case_update_plan_active:
            skip_reason = "case_update_not_persisted"
            if persistence_consent == "temporary":
                skip_reason = "temporary"
            elif case_update_blocked_by_legal_qa:
                skip_reason = "legal_qa"
            execution_trace.add_step(
                name="fact_persistence",
                agent="conversation_store",
                status="skipped",
                skip_reason=skip_reason,
            )
            execution_trace.add_step(
                name="graph_update",
                agent="celery.update_graph_task",
                status="skipped",
                skip_reason=skip_reason,
            )
        elif missing_previous_fact:
            execution_trace.add_step(
                name="fact_persistence",
                agent="conversation_store",
                status="skipped",
                skip_reason="missing_previous_fact",
            )
            execution_trace.add_step(
                name="graph_update",
                agent="celery.update_graph_task",
                status="skipped",
                skip_reason="missing_previous_fact",
            )

        if route_intent == "GREETING":
            agent_actions = [
                {"tool": "tool_router", "result_summary": "Greeting route selected"},
                {"tool": "greeting", "result_summary": "Fast greeting response generated"},
            ]
        elif route_intent == "DOSSIER_MEMORY":
            agent_actions = [
                _build_message_understanding_active_action(message_understanding),
                _build_execution_plan_action(execution_plan),
                _build_dossier_memory_action(),
            ]
        elif case_update_plan_active:
            agent_actions = [
                _build_message_understanding_active_action(message_understanding),
                _build_execution_plan_action(execution_plan),
            ]
            if confirm_previous_fact_active:
                agent_actions.append(_build_fact_confirmation_action(confirm_previous_fact_text))
            if case_update_acknowledgement_result is not None:
                agent_actions.append(
                    _build_case_update_acknowledgement_action(case_update_acknowledgement_result)
                )
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
        if route_intent not in {"DOSSIER_MEMORY"} and not case_update_plan_active:
            agent_actions.append(_build_message_understanding_shadow_action(message_understanding))

        is_final = answer.get("is_final", False) if isinstance(answer, dict) else False
        confidence_score = answer.get("confidence_score", 0.0) if isinstance(answer, dict) else 0.0
        missing_info = answer.get("missing_info", []) if isinstance(answer, dict) else []
        assistant_metadata = {
            "sources": response_sources,
            "agent_actions": agent_actions,
            "is_final": is_final,
            "confidence_score": confidence_score,
            "missing_info": missing_info,
            "latency_ms": latency_ms,
            "route_intent": route_intent,
            "message_understanding": message_understanding,
            "execution_plan": execution_plan,
        }
        persist_result = {"user_message_id": None, "assistant_message_id": None}
        user_message_metadata: dict[str, Any] = {}
        if confirm_previous_fact_active and should_persist_structured_case:
            user_message_metadata = {
                "message_understanding": {
                    "intent": message_understanding.get("intent"),
                    "route_hint": message_understanding.get("route_hint"),
                },
                "execution_plan": {
                    "primary_route": execution_plan.get("primary_route"),
                    "steps": sorted(execution_plan_step_names),
                },
                "confirmed_fact_text": confirm_previous_fact_text,
                "graph_update_input_text": graph_update_input_text or confirm_previous_fact_text,
                "graph_update_source": "confirmed_previous_fact",
            }

        if route_intent != "GREETING":
            if case_update_plan_active and should_persist_structured_case:
                with execution_trace.step_context(
                    name="fact_persistence",
                    agent="conversation_store",
                ) as fact_persistence_step:
                    fact_persistence_step.set_details(
                        {
                            "persistence_mode": "structured_case",
                            "input_text": effective_persistence_text,
                        }
                    )
                    persist_result = asyncio.run(
                        _persist_dialog_turn_async(
                            user_id=user_id,
                            conversation_id=conversation_id,
                            user_content=question,
                            assistant_content=response_text,
                            metadata=assistant_metadata,
                            user_metadata=user_message_metadata,
                            case_summary=updated_case_summary if should_persist_structured_case else "",
                            case_state_dict=case_state_dict if should_persist_structured_case else None,
                            case_type=case_type if should_persist_structured_case else None,
                        )
                    )
            else:
                persist_result = asyncio.run(
                    _persist_dialog_turn_async(
                        user_id=user_id,
                        conversation_id=conversation_id,
                        user_content=question,
                        assistant_content=response_text,
                        metadata=assistant_metadata,
                        user_metadata=user_message_metadata,
                        case_summary=updated_case_summary if should_persist_structured_case else "",
                        case_state_dict=case_state_dict if should_persist_structured_case else None,
                        case_type=case_type if should_persist_structured_case else None,
                    )
                )

        if should_persist_structured_case:
            user_message_id = persist_result.get("user_message_id")
            if user_message_id:
                with execution_trace.step_context(
                    name="graph_update",
                    agent="celery.update_graph_task",
                ) as graph_update_step:
                    async_result = update_graph_task.delay(conversation_id, user_id, user_message_id)
                    graph_task_id = str(getattr(async_result, "id", "") or "").strip()
                    if not graph_task_id:
                        graph_task_id = f"scheduled:{conversation_id}:{user_message_id}"
                    graph_update_step.set_details(
                        {
                            "task_id": graph_task_id,
                            "graph_update_source": (
                                "confirmed_previous_fact"
                                if confirm_previous_fact_active
                                else "current_message"
                            ),
                        }
                    )
                if confirm_previous_fact_active:
                    agent_actions.append(_build_graph_sync_action())
            else:
                execution_trace.add_step(
                    name="graph_update",
                    agent="celery.update_graph_task",
                    status="skipped",
                    skip_reason="missing_user_message_id",
                )
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
            elif case_update_blocked_by_legal_qa:
                logger.info(
                    "Structured case persistence skipped because CASE_UPDATE execution plan arrived in LEGAL_QA mode | conversation_id=%s user_id=%s",
                    conversation_id,
                    user_id,
                )
                logger.info(
                    "Graph update skipped because CASE_UPDATE execution plan arrived in LEGAL_QA mode | conversation_id=%s user_id=%s",
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

        execution_trace_payload = execution_trace.finalize()
        assistant_metadata["execution_trace"] = execution_trace_payload
        assistant_message_id = persist_result.get("assistant_message_id")
        if route_intent != "GREETING" and assistant_message_id:
            try:
                asyncio.run(
                    _merge_message_metadata_async(
                        conversation_id=conversation_id,
                        message_id=assistant_message_id,
                        metadata_patch={"execution_trace": execution_trace_payload},
                    )
                )
            except Exception as exc:
                logger.warning(
                    "Failed to attach execution_trace to assistant metadata | conversation_id=%s assistant_message_id=%s error=%s",
                    conversation_id,
                    assistant_message_id,
                    exc,
                )

        result = {
            "status": "success",
            "user_id": user_id,
            "conversation_id": conversation_id,
            "session_id": session_id,
            "response": response_text,
            "agent_actions": agent_actions,
            "sources": response_sources,
            "is_final": is_final,
            "confidence_score": confidence_score,
            "missing_info": missing_info,
            "route_intent": route_intent,
            "message_understanding": message_understanding,
            "execution_plan": execution_plan,
            "execution_trace": execution_trace_payload,
            "case_summary": updated_case_summary,
            "latency_ms": latency_ms,
        }

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
