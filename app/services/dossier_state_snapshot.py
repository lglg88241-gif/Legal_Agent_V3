from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.conversation_entity_state import build_conversation_entity_state
from app.services.conversation_store import (
    list_dossier_conversations,
    list_dossier_entity_resolution_decisions,
    list_recent_conversation_messages,
    serialize_dossier_entity_resolution_decision,
)
from app.services.dossier_entity_state import (
    build_dossier_entity_state,
    coordination_graph_from_dossier_entity_state,
)


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _normalize_key(value: Any) -> str:
    return _normalize_text(value).lower()


def _positive_int(value: Any) -> int:
    try:
        normalized = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return normalized if normalized > 0 else 0


def _conversation_summary(conversation: Any) -> dict[str, Any] | None:
    conversation_id = _positive_int(getattr(conversation, "id", None))
    if conversation_id <= 0:
        return None
    return {
        "id": conversation_id,
        "title": getattr(conversation, "title", "") or f"conversation {conversation_id}",
    }


async def _conversation_onboarding_message_text(
    db: AsyncSession,
    *,
    conversation_id: int,
) -> str:
    messages = await list_recent_conversation_messages(db, conversation_id=conversation_id, limit=8)
    return " ".join(
        _normalize_text(getattr(message, "content", ""))
        for message in messages
        if _normalize_key(getattr(message, "role", "")) == "user"
        and _normalize_text(getattr(message, "content", ""))
    )


def format_h9_canonical_entity_summary(
    dossier_entity_state: dict[str, Any] | None,
) -> str | None:
    state = dossier_entity_state if isinstance(dossier_entity_state, dict) else {}
    entities = state.get("entities") if isinstance(state.get("entities"), list) else []
    relations = state.get("relations") if isinstance(state.get("relations"), list) else []
    entity_label_by_id = {
        _normalize_text(entity.get("id")): _normalize_text(entity.get("canonical_label") or entity.get("label"))
        for entity in entities
        if isinstance(entity, dict) and _normalize_text(entity.get("id"))
    }

    entity_parts: list[str] = []
    current_party = None
    employers: list[str] = []
    organizations: list[str] = []
    relation_parts: list[tuple[str, str, str]] = []

    for entity in entities:
        if not isinstance(entity, dict):
            continue
        label = _normalize_text(entity.get("canonical_label") or entity.get("label"))
        entity_type = _normalize_text(entity.get("entity_type"))
        role = _normalize_text(entity.get("role"))
        confidence = _normalize_text(entity.get("confidence"))
        if not label or confidence == "low":
            continue
        if role == "current_party" and entity_type == "person":
            current_party = label
        elif role == "employer" and entity_type == "organization":
            if label not in employers:
                employers.append(label)
        elif entity_type == "organization" and label not in employers and label not in organizations:
            organizations.append(label)

    for relation in relations:
        if not isinstance(relation, dict):
            continue
        source_id = _normalize_text(relation.get("source"))
        target_id = _normalize_text(relation.get("target"))
        source_label = _normalize_text(relation.get("source_label")) or entity_label_by_id.get(source_id, "")
        target_label = _normalize_text(relation.get("target_label")) or entity_label_by_id.get(target_id, "")
        relation_label = _normalize_text(relation.get("label") or relation.get("relation"))
        if source_label and target_label and relation_label:
            relation_parts.append((source_label, relation_label, target_label))

    if current_party:
        entity_parts.append(f"当事人是{current_party}")
    for employer in employers[:2]:
        entity_parts.append(f"用人单位是{employer}")
    for organization in organizations[:2]:
        entity_parts.append(f"涉及组织{organization}")
    for source_label, relation_label, target_label in relation_parts[:3]:
        entity_parts.append(f"{source_label}{relation_label}{target_label}")

    if not entity_parts:
        return None
    return "当前案卷已确认：" + "；".join(entity_parts) + "。"


def build_dossier_state_snapshot(
    *,
    conversation_entity_states: list[dict[str, Any]] | None,
    dossier_id: int | None = None,
    user_id: int | None = None,
    resolution_decisions: list[dict[str, Any]] | None = None,
    conversation_summaries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized_conversation_entity_states = [
        item for item in (conversation_entity_states or []) if isinstance(item, dict)
    ]
    normalized_resolution_decisions = [
        item for item in (resolution_decisions or []) if isinstance(item, dict)
    ]
    normalized_conversation_summaries = [
        item for item in (conversation_summaries or []) if isinstance(item, dict)
    ]

    dossier_entity_state = build_dossier_entity_state(
        conversation_entity_states=normalized_conversation_entity_states,
        dossier_id=dossier_id,
        user_id=user_id,
        resolution_decisions=normalized_resolution_decisions,
        conversation_summaries=normalized_conversation_summaries,
    )
    coordination_graph = coordination_graph_from_dossier_entity_state(dossier_entity_state)
    canonical_entity_summary = format_h9_canonical_entity_summary(dossier_entity_state)
    return {
        "dossier_id": dossier_id,
        "user_id": user_id,
        "conversation_entity_states": normalized_conversation_entity_states,
        "resolution_decisions": normalized_resolution_decisions,
        "conversation_summaries": normalized_conversation_summaries,
        "dossier_entity_state": dossier_entity_state,
        "coordination_graph": coordination_graph,
        "canonical_entity_summary": canonical_entity_summary,
    }


def build_single_conversation_dossier_state_snapshot(
    *,
    message_text: str,
    raw_graph_payload: dict[str, Any] | None,
    conversation_id: int,
    conversation_mode: str | None,
    persistence_consent: str | None,
    dossier_id: int | None,
    user_id: int | None,
) -> dict[str, Any]:
    graph_payload = raw_graph_payload if isinstance(raw_graph_payload, dict) else {}
    conversation_entity_state = build_conversation_entity_state(
        message_text=message_text,
        raw_graph_payload=graph_payload,
        conversation_id=conversation_id,
        conversation_mode=conversation_mode,
        persistence_consent=persistence_consent,
    )
    return build_dossier_state_snapshot(
        conversation_entity_states=[conversation_entity_state],
        dossier_id=dossier_id,
        user_id=user_id,
        resolution_decisions=[],
        conversation_summaries=[],
    )


async def build_dossier_state_snapshot_for_user(
    db: AsyncSession,
    *,
    user_id: int,
    dossier_id: int,
    raw_graph_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    conversations = await list_dossier_conversations(db, user_id, dossier_id)
    serialized_decisions = [
        serialize_dossier_entity_resolution_decision(decision)
        for decision in await list_dossier_entity_resolution_decisions(db, user_id, dossier_id)
    ]
    conversation_summaries = [
        summary
        for summary in (_conversation_summary(conversation) for conversation in conversations)
        if isinstance(summary, dict)
    ]

    conversation_entity_states: list[dict[str, Any]] = []
    for conversation in conversations:
        conversation_id = _positive_int(getattr(conversation, "id", None))
        if conversation_id <= 0:
            continue
        message_text = await _conversation_onboarding_message_text(
            db,
            conversation_id=conversation_id,
        )
        conversation_entity_states.append(
            build_conversation_entity_state(
                message_text=message_text,
                raw_graph_payload=raw_graph_payload if isinstance(raw_graph_payload, dict) else {},
                conversation_id=conversation_id,
                conversation_mode=getattr(conversation, "conversation_mode", None) or "TRACKED_CASE",
                persistence_consent=getattr(conversation, "persistence_consent", None) or "tracked",
            )
        )

    snapshot = build_dossier_state_snapshot(
        conversation_entity_states=conversation_entity_states,
        dossier_id=dossier_id,
        user_id=user_id,
        resolution_decisions=serialized_decisions,
        conversation_summaries=conversation_summaries,
    )
    snapshot["conversations"] = conversations
    return snapshot


__all__ = [
    "build_dossier_state_snapshot",
    "build_dossier_state_snapshot_for_user",
    "build_single_conversation_dossier_state_snapshot",
    "format_h9_canonical_entity_summary",
]
