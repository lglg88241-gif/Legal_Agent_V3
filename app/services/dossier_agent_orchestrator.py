from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.services.conversation_entity_state import build_conversation_entity_state
from app.services.conversation_store import (
    build_dossier_participant_lookup,
    get_dossier_for_user,
    list_dossier_conversations,
    list_dossier_entity_resolution_decisions,
    list_dossier_participant_mentions,
    list_dossier_participants,
    list_recent_conversation_messages,
    serialize_dossier,
    serialize_dossier_entity_resolution_decision,
    serialize_dossier_participant,
    serialize_dossier_participant_mention,
)
from app.services.dossier_agent_context import DossierEntityRequestContext
from app.services.dossier_entity_state import (
    build_dossier_entity_state,
    coordination_graph_from_dossier_entity_state,
)
from app.services.dossier_graph import get_dossier_graph_payload


SUPPORTED_DOSSIER_EVENTS = {
    "open_dossier_drawer",
    "bind_conversation_to_dossier",
    "conversation_graph_updated",
    "refresh_dossier_entity_state",
    "confirm_cross_conversation_same_entity",
    "reject_cross_conversation_same_entity",
    "manual_connect_conversation_entities",
    "dismiss_cross_conversation_task",
}


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _positive_int(value: Any) -> int:
    try:
        normalized = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return normalized if normalized > 0 else 0


def _extract_user_id(current_user: User | Any) -> int:
    user_id = _positive_int(getattr(current_user, "id", None))
    if user_id <= 0:
        user_id = _positive_int(getattr(current_user, "user_id", None))
    if user_id <= 0:
        raise ValueError("Current user is required")
    return user_id


async def _conversation_message_text(db: AsyncSession, conversation_id: int) -> str:
    messages = await list_recent_conversation_messages(db, conversation_id=conversation_id, limit=10)
    return " ".join(
        _normalize_text(getattr(message, "content", ""))
        for message in messages
        if _normalize_text(getattr(message, "role", "")).lower() == "user"
        and _normalize_text(getattr(message, "content", ""))
    )


async def build_dossier_entity_request_context(
    db: AsyncSession,
    *,
    current_user: User | Any,
    dossier_id: int,
    event_type: str,
    conversation_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> DossierEntityRequestContext:
    user_id = _extract_user_id(current_user)
    if event_type not in SUPPORTED_DOSSIER_EVENTS:
        raise ValueError(f"Unsupported dossier event_type: {event_type}")

    dossier = await get_dossier_for_user(db, user_id, dossier_id)
    if dossier is None:
        raise ValueError("Dossier not found")

    conversations = await list_dossier_conversations(db, user_id, dossier.id)
    conversation_ids = [
        _positive_int(getattr(conversation, "id", None))
        for conversation in conversations
        if _positive_int(getattr(conversation, "id", None)) > 0
    ]
    participants = await list_dossier_participants(db, user_id, dossier.id)
    mentions = await list_dossier_participant_mentions(db, user_id, dossier.id)
    participant_lookup = build_dossier_participant_lookup(participants)
    raw_graph_payload = await get_dossier_graph_payload(
        user_id=user_id,
        dossier_id=dossier.id,
        conversation_ids=conversation_ids,
        participant_aliases=serialize_dossier(dossier).get("participant_aliases", {}),
        confirmed_participants=[serialize_dossier_participant(participant) for participant in participants],
        participant_mappings=[
            serialize_dossier_participant_mention(mention, participant_lookup)
            for mention in mentions
        ],
    )

    conversation_entity_states: list[dict[str, Any]] = []
    for conversation in conversations:
        current_id = _positive_int(getattr(conversation, "id", None))
        if current_id <= 0:
            continue
        message_text = await _conversation_message_text(db, current_id)
        conversation_entity_states.append(
            build_conversation_entity_state(
                message_text=message_text,
                raw_graph_payload=raw_graph_payload,
                conversation_id=current_id,
                conversation_mode=getattr(conversation, "conversation_mode", None),
                persistence_consent=getattr(conversation, "persistence_consent", None),
            )
        )

    decisions = await list_dossier_entity_resolution_decisions(db, user_id, dossier.id)
    return DossierEntityRequestContext(
        user_id=user_id,
        dossier_id=dossier.id,
        conversation_id=conversation_id,
        event_type=event_type,
        payload=dict(payload or {}),
        conversation_entity_states=conversation_entity_states,
        resolution_decisions=[
            serialize_dossier_entity_resolution_decision(decision)
            for decision in decisions
        ],
    )


async def run_dossier_agent_event(
    db: AsyncSession,
    *,
    current_user: User | Any,
    dossier_id: int,
    event_type: str,
    conversation_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = await build_dossier_entity_request_context(
        db,
        current_user=current_user,
        dossier_id=dossier_id,
        event_type=event_type,
        conversation_id=conversation_id,
        payload=payload,
    )
    dossier_entity_state = build_dossier_entity_state(
        conversation_entity_states=context.conversation_entity_states,
        dossier_id=context.dossier_id,
        user_id=context.user_id,
        resolution_decisions=context.resolution_decisions,
    )
    coordination_graph = coordination_graph_from_dossier_entity_state(dossier_entity_state)
    return {
        "dossier_entity_state": dossier_entity_state,
        "coordination_graph": coordination_graph,
        "execution_trace": {
            "source": "dossier_agent_orchestrator",
            "event_type": context.event_type,
            "steps": [
                "build_dossier_entity_request_context",
                "build_dossier_entity_state",
                "build_coordination_graph",
            ],
        },
        "writes": {},
        "ui_hints": {
            "show_cross_conversation_tasks": bool(dossier_entity_state.get("confirmation_tasks")),
            "hide_legacy_party_naming_as_primary": True,
        },
    }


__all__ = [
    "SUPPORTED_DOSSIER_EVENTS",
    "build_dossier_entity_request_context",
    "run_dossier_agent_event",
]
