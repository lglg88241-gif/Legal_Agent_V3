from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.services.conversation_store import (
    build_dossier_participant_lookup,
    get_dossier_for_user,
    list_dossier_conversations,
    list_dossier_participant_mentions,
    list_dossier_participants,
    serialize_dossier,
    serialize_dossier_participant,
    serialize_dossier_participant_mention,
)
from app.services.dossier_display_graph import build_dossier_display_graph
from app.services.dossier_graph import get_dossier_graph_payload

_STATUS_SUCCESS = "success"
_SOURCE_NAME = "dossier_intelligence"
_PROVIDER_NAME = "rules"
_MODEL_NAME = "dossier-intelligence-rules-v0"
_FALLBACK_REASON = "llm_not_enabled_in_skeleton"
_SUMMARY_FALLBACK_TEXT = "当前案卷暂无可用摘要。"


def _extract_user_id(current_user: User) -> int:
    raw_user_id = getattr(current_user, "id", None)
    if raw_user_id is None:
        raw_user_id = getattr(current_user, "user_id", None)
    try:
        user_id = int(raw_user_id or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("Current user is required") from exc
    if user_id <= 0:
        raise ValueError("Current user is required")
    return user_id


def _isoformat(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _step(step: str, status: str, **extra: Any) -> dict[str, Any]:
    payload = {"step": step, "status": status}
    payload.update(extra)
    return payload


def _build_summary_payload(dossier: Any) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []

    auto_summary = str(getattr(dossier, "auto_summary", "") or "").strip()
    if auto_summary:
        return (
            {
                "text": auto_summary,
                "source": "auto_summary",
                "updated_at": _isoformat(
                    getattr(dossier, "auto_summary_updated_at", None)
                    or getattr(dossier, "updated_at", None)
                ),
            },
            warnings,
        )

    manual_summary = str(getattr(dossier, "summary", "") or "").strip()
    if manual_summary:
        return (
            {
                "text": manual_summary,
                "source": "manual_summary",
                "updated_at": _isoformat(getattr(dossier, "updated_at", None)),
            },
            warnings,
        )

    warnings.append("No dossier summary available")
    return (
        {
            "text": _SUMMARY_FALLBACK_TEXT,
            "source": "rule_fallback",
            "updated_at": _isoformat(getattr(dossier, "updated_at", None)),
        },
        warnings,
    )


def _derive_conversation_count(
    display_graph: dict[str, Any] | None,
    raw_graph_payload: dict[str, Any] | None,
) -> int:
    raw_graph = raw_graph_payload.get("graph") if isinstance(raw_graph_payload, dict) else {}
    raw_stats = raw_graph.get("stats") if isinstance(raw_graph, dict) else {}
    try:
        conversation_count = int(raw_stats.get("conversation_count") or 0)
    except (TypeError, ValueError):
        conversation_count = 0
    if conversation_count > 0:
        return conversation_count

    nodes = display_graph.get("nodes") if isinstance(display_graph, dict) else []
    conversation_ids: set[int] = set()
    for node in nodes if isinstance(nodes, list) else []:
        if not isinstance(node, dict):
            continue
        source_ids = (
            node.get("source_conversation_ids")
            if isinstance(node.get("source_conversation_ids"), list)
            else []
        )
        for source_id in source_ids:
            try:
                normalized_id = int(source_id or 0)
            except (TypeError, ValueError):
                normalized_id = 0
            if normalized_id > 0:
                conversation_ids.add(normalized_id)
    return len(conversation_ids)


def _build_display_graph_notes(
    display_graph: dict[str, Any] | None,
    raw_graph_payload: dict[str, Any] | None,
) -> list[str]:
    stats = display_graph.get("stats") if isinstance(display_graph, dict) else {}
    try:
        merged_participant_count = int(stats.get("merged_participant_count") or 0)
    except (TypeError, ValueError):
        merged_participant_count = 0
    try:
        pending_confirmation_count = int(stats.get("pending_confirmation_count") or 0)
    except (TypeError, ValueError):
        pending_confirmation_count = 0

    return [
        f"已确认主体数量：{merged_participant_count}。",
        f"待确认原始实体数量：{pending_confirmation_count}。",
        f"涉及会话数量：{_derive_conversation_count(display_graph, raw_graph_payload)}。",
    ]


async def _load_display_graph_context(
    db: AsyncSession,
    *,
    user_id: int,
    dossier: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    conversations = await list_dossier_conversations(db, user_id, dossier.id)
    conversation_ids = [
        conversation.id
        for conversation in conversations
        if isinstance(getattr(conversation, "id", None), int) and conversation.id > 0
    ]
    participant_aliases = serialize_dossier(dossier).get("participant_aliases", {})
    participants = await list_dossier_participants(db, user_id, dossier.id)
    mentions = await list_dossier_participant_mentions(db, user_id, dossier.id)
    participant_lookup = build_dossier_participant_lookup(participants)
    raw_graph_payload = await get_dossier_graph_payload(
        user_id=user_id,
        dossier_id=dossier.id,
        conversation_ids=conversation_ids,
        participant_aliases=participant_aliases,
        confirmed_participants=[
            serialize_dossier_participant(participant)
            for participant in participants
        ],
        participant_mappings=[
            serialize_dossier_participant_mention(mention, participant_lookup)
            for mention in mentions
        ],
    )
    display_graph = build_dossier_display_graph(
        raw_graph=raw_graph_payload.get("graph"),
        confirmed_participants=raw_graph_payload.get("confirmed_participants"),
        participant_mappings=raw_graph_payload.get("participant_mappings"),
    )
    return raw_graph_payload, display_graph


async def build_dossier_intelligence(
    db: AsyncSession,
    *,
    current_user: User,
    dossier_id: int,
    conversation_id: int | None = None,
    include_display_graph: bool = True,
) -> dict[str, Any]:
    user_id = _extract_user_id(current_user)
    dossier = await get_dossier_for_user(db, user_id, dossier_id)
    if dossier is None:
        raise ValueError("Dossier not found")

    steps: list[dict[str, Any]] = [_step("load_dossier", "success", dossier_id=dossier.id)]
    warnings: list[str] = []
    display_graph_notes: list[str] = []

    if include_display_graph:
        try:
            raw_graph_payload, display_graph = await _load_display_graph_context(
                db,
                user_id=user_id,
                dossier=dossier,
            )
            display_graph_notes = _build_display_graph_notes(display_graph, raw_graph_payload)
            payload_warning = str(raw_graph_payload.get("warning") or "").strip()
            if payload_warning:
                warnings.append(payload_warning)
            display_stats = display_graph.get("stats") if isinstance(display_graph, dict) else {}
            steps.append(
                _step(
                    "load_display_graph",
                    "success",
                    display_node_count=int(display_stats.get("display_node_count") or 0),
                    merged_participant_count=int(
                        display_stats.get("merged_participant_count") or 0
                    ),
                )
            )
        except Exception as exc:
            warnings.append(f"display_graph_unavailable: {exc}")
            steps.append(
                _step(
                    "load_display_graph",
                    "warning",
                    reason=str(exc),
                )
            )
    else:
        steps.append(
            _step(
                "skip_display_graph",
                "skipped",
                reason="include_display_graph_false",
            )
        )

    summary_payload, summary_warnings = _build_summary_payload(dossier)
    warnings.extend(summary_warnings)
    steps.append(_step("build_summary", "success", source=summary_payload["source"]))

    confirmation_tasks: list[dict[str, Any]] = []
    steps.append(
        _step(
            "build_confirmation_tasks",
            "success",
            task_count=len(confirmation_tasks),
        )
    )

    return {
        "status": _STATUS_SUCCESS,
        "source": _SOURCE_NAME,
        "scope": {
            "user_id": user_id,
            "dossier_id": dossier.id,
            "conversation_id": conversation_id,
        },
        "summary": summary_payload,
        "confirmation_tasks": confirmation_tasks,
        "display_graph_notes": display_graph_notes,
        "trace": {
            "steps": steps,
            "warnings": warnings,
        },
        "provider": _PROVIDER_NAME,
        "model": _MODEL_NAME,
        "fallback_reason": _FALLBACK_REASON,
    }
