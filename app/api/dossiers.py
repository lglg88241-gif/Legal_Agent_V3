from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthenticatedUser, get_current_user
from app.database.mysql_db import get_db
from app.services.conversation_store import (
    DEFAULT_DOSSIER_STATUS,
    archive_dossier_record,
    build_cross_conversation_resolution_pair_hash,
    build_cross_conversation_resolution_pair_key,
    build_dossier_participant_candidates,
    build_dossier_participant_lookup,
    create_dossier_entity_resolution_decision,
    create_dossier_participant_record,
    create_dossier_record,
    ensure_storage_schema,
    get_dossier_for_user,
    get_dossier_participant_for_user,
    hard_delete_dossier_record,
    is_generic_participant_label,
    list_dossier_conversations,
    list_dossier_entity_resolution_decisions,
    list_dossier_participant_mentions,
    list_dossier_participants,
    list_user_dossiers,
    normalize_dossier_status,
    resolve_dossier_participant_mentions,
    serialize_dossier,
    serialize_dossier_entity_resolution_decision,
    serialize_dossier_detail,
    serialize_dossier_participant,
    serialize_dossier_participant_mention,
    touch_dossier,
    update_dossier_auto_summary,
    update_dossier_fields,
    update_dossier_participant_aliases,
    update_dossier_participant_record,
)
from app.services.dossier_display_graph import build_dossier_display_graph
from app.services.dossier_coordination_graph import (
    build_dossier_coordination_graph_for_user,
)
from app.services.dossier_graph import get_dossier_graph_payload
from app.services.dossier_intelligence import build_dossier_intelligence
from app.services.dossier_summary import generate_dossier_auto_summary

router = APIRouter(prefix="/dossiers", tags=["dossiers"])


class DossierCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    case_type: str | None = Field(default=None, max_length=100)
    summary: str | None = Field(default=None, max_length=5000)


class DossierUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    case_type: str | None = Field(default=None, max_length=100)
    summary: str | None = Field(default=None, max_length=5000)
    status: str | None = Field(default=None, max_length=32)


class DossierParticipantAliasRequest(BaseModel):
    label: str = Field(..., min_length=1, max_length=100)
    role: str = Field(..., min_length=1, max_length=100)
    party_side: str = Field(..., min_length=1, max_length=32)


class DossierParticipantsUpdateRequest(BaseModel):
    participant_aliases: dict[str, DossierParticipantAliasRequest]


class DossierParticipantCreateRequest(BaseModel):
    label: str = Field(..., min_length=1, max_length=100)
    role: str | None = Field(default=None, max_length=100)
    party_side: str = Field(default="unknown", min_length=1, max_length=32)
    entity_type: str = Field(default="unknown", min_length=1, max_length=32)
    description: str | None = Field(default=None, max_length=1000)


class DossierParticipantUpdateRequest(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=100)
    role: str | None = Field(default=None, max_length=100)
    party_side: str | None = Field(default=None, min_length=1, max_length=32)
    entity_type: str | None = Field(default=None, min_length=1, max_length=32)
    description: str | None = Field(default=None, max_length=1000)


class DossierParticipantResolveMentionRequest(BaseModel):
    conversation_id: int = Field(..., gt=0)
    raw_label: str = Field(..., min_length=1, max_length=100)
    source: str = Field(default="graph", min_length=1, max_length=32)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    graph_node_id: str | None = Field(default=None, max_length=255)
    graph_node_label: str | None = Field(default=None, max_length=255)
    graph_node_type: str | None = Field(default=None, max_length=100)


class DossierParticipantResolveRequest(BaseModel):
    participant_id: int | None = Field(default=None, gt=0)
    participant: DossierParticipantCreateRequest | None = None
    mentions: list[DossierParticipantResolveMentionRequest] = Field(
        ...,
        min_length=1,
        max_length=50,
    )


class PartyNamingEvidenceRequest(BaseModel):
    raw_labels: list[str] = Field(default_factory=list, max_length=50)
    source_conversation_ids: list[int] = Field(default_factory=list, max_length=50)
    raw_node_ids: list[str] = Field(default_factory=list, max_length=50)


class PartyNamingSourceTaskRequest(BaseModel):
    subject: str = Field(..., min_length=1, max_length=32)
    evidence: PartyNamingEvidenceRequest | None = None


class PartyNamingConfirmRequest(BaseModel):
    display_label: str = Field(..., min_length=1, max_length=50)
    source_task: PartyNamingSourceTaskRequest


class SelfIdentitySourceNodeRequest(BaseModel):
    conversation_id: int = Field(..., gt=0)
    raw_label: str = Field(..., min_length=1, max_length=100)
    graph_node_id: str = Field(..., min_length=1, max_length=255)
    graph_node_label: str | None = Field(default=None, max_length=255)
    graph_node_type: str | None = Field(default=None, max_length=100)


class CrossConversationDecisionNodeRequest(BaseModel):
    conversation_id: int = Field(..., gt=0)
    raw_label: str = Field(..., min_length=1, max_length=100)
    graph_node_id: str = Field(..., min_length=1, max_length=255)
    graph_node_label: str | None = Field(default=None, max_length=255)
    graph_node_type: str | None = Field(default=None, max_length=100)


class SelfIdentityConfirmRequest(BaseModel):
    action: str = Field(..., min_length=1, max_length=32)
    source_node: SelfIdentitySourceNodeRequest
    target_participant_id: int = Field(..., gt=0)
    new_display_label: str | None = Field(default=None, max_length=50)


class CrossConversationResolutionDecisionRequest(BaseModel):
    subject: str = Field(..., min_length=1, max_length=64)
    decision: str = Field(..., min_length=1, max_length=32)
    source_node: CrossConversationDecisionNodeRequest
    target_node: CrossConversationDecisionNodeRequest
    reason: str | None = Field(default=None, max_length=1000)
    candidate_key: str | None = Field(default=None, max_length=255)
    task_key: str | None = Field(default=None, max_length=255)


_SELF_ANCHOR_SUBJECT = "self_anchor"
_CROSS_CONVERSATION_IDENTITY_SUBJECT = "cross_conversation_identity"
_PARTY_NAMING_ROLE = "当事人"
_PARTY_NAMING_MENTION_SOURCE = "party_naming"
_PARTY_NAMING_GRAPH_NODE_TYPE = "person"
_SELF_IDENTITY_CONFIRM_MENTION_SOURCE = "self_identity_confirmation"
_SELF_IDENTITY_CONFIRM_ACTION_SAME = "confirm_same"
_SELF_IDENTITY_CONFIRM_ACTION_DIFFERENT = "confirm_different"
_SELF_IDENTITY_DECISION_SAME = "same"
_SELF_IDENTITY_DECISION_NOT_SAME = "not_same"
_CROSS_CONVERSATION_DECISION_TYPES = {"same", "not_same", "skip", "not_actionable"}
_SELF_ANCHOR_LABELS = {
    "当事人",
    "我",
    "本人",
    "劳动者",
    "申请人",
    "自己",
    "用户",
    "我们",
    "咱们",
}
_EXPLICIT_SELF_DISPLAY_LABELS = {
    "我",
    "本人",
    "自己",
    "我们",
    "咱们",
    "劳动者本人",
    "我方当事人",
    "申请人",
}


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _normalize_key(value: Any) -> str:
    return _normalize_text(value).lower()


def _positive_int(value: Any) -> int:
    try:
        normalized = int(value or 0)
    except (TypeError, ValueError):
        normalized = 0
    return normalized if normalized > 0 else 0


def _normalize_self_identity_action(value: Any) -> str:
    normalized = _normalize_key(value)
    if normalized not in {
        _SELF_IDENTITY_CONFIRM_ACTION_SAME,
        _SELF_IDENTITY_CONFIRM_ACTION_DIFFERENT,
    }:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported self identity confirmation action",
        )
    return normalized


def _normalize_optional_display_label(value: Any) -> str:
    return _normalize_text(value)


def _source_node_matches_mention(
    mention: Any,
    *,
    conversation_id: int,
    raw_label: str,
    graph_node_id: str,
) -> bool:
    mention_graph_node_id = _normalize_text(getattr(mention, "graph_node_id", ""))
    if graph_node_id and mention_graph_node_id:
        return mention_graph_node_id == graph_node_id
    return (
        _positive_int(getattr(mention, "conversation_id", 0)) == conversation_id
        and _normalize_key(getattr(mention, "raw_label", "")) == _normalize_key(raw_label)
    )


def _find_source_node_mention(
    mentions: list[Any],
    *,
    conversation_id: int,
    raw_label: str,
    graph_node_id: str,
) -> Any | None:
    for mention in mentions:
        if _source_node_matches_mention(
            mention,
            conversation_id=conversation_id,
            raw_label=raw_label,
            graph_node_id=graph_node_id,
        ):
            return mention
    return None


def _find_self_identity_decision(
    decisions: list[Any],
    *,
    decision_type: str,
    graph_node_id: str,
    target_participant_id: int,
) -> Any | None:
    for decision in decisions:
        if _normalize_key(getattr(decision, "subject", "")) != _SELF_ANCHOR_SUBJECT:
            continue
        if _normalize_key(getattr(decision, "decision_type", "")) != decision_type:
            continue
        if _positive_int(getattr(decision, "target_participant_id", 0)) != target_participant_id:
            continue
        if _normalize_text(getattr(decision, "source_graph_node_id", "")) != graph_node_id:
            continue
        return decision
    return None


def _find_self_participant(participants: list[Any]) -> Any | None:
    ranked_matches: list[tuple[int, int, Any]] = []
    for index, participant in enumerate(participants):
        label = _normalize_text(getattr(participant, "label", ""))
        role = _normalize_text(getattr(participant, "role", ""))
        party_side = _normalize_key(getattr(participant, "party_side", "unknown"))
        entity_type = _normalize_key(getattr(participant, "entity_type", "unknown"))

        rank: int | None = None
        if (
            label in _SELF_ANCHOR_LABELS
            or role in _SELF_ANCHOR_LABELS
            or label in _EXPLICIT_SELF_DISPLAY_LABELS
            or role in _EXPLICIT_SELF_DISPLAY_LABELS
        ):
            rank = 0
        elif party_side == "claimant" and entity_type == "person":
            rank = 1
        elif party_side == "claimant" and entity_type == "unknown":
            rank = 2

        if rank is not None:
            ranked_matches.append((rank, index, participant))

    if not ranked_matches:
        return None
    ranked_matches.sort(key=lambda item: (item[0], item[1]))
    return ranked_matches[0][2]


def _validate_self_anchor_source_node(source_node: SelfIdentitySourceNodeRequest) -> tuple[int, str, str]:
    conversation_id = _positive_int(source_node.conversation_id)
    raw_label = _normalize_text(source_node.raw_label)
    graph_node_id = _normalize_text(source_node.graph_node_id)

    if conversation_id <= 0 or not raw_label or not graph_node_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="source_node conversation_id, raw_label, and graph_node_id are required",
        )
    if raw_label not in _SELF_ANCHOR_LABELS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="self identity confirmation only supports self anchor labels",
        )
    return conversation_id, raw_label, graph_node_id


def _normalize_cross_conversation_subject(value: Any) -> str:
    normalized = _normalize_key(value)
    if normalized != _CROSS_CONVERSATION_IDENTITY_SUBJECT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported cross conversation resolution subject",
        )
    return normalized


def _normalize_cross_conversation_decision(value: Any) -> str:
    normalized = _normalize_key(value)
    if normalized not in _CROSS_CONVERSATION_DECISION_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported cross conversation resolution decision",
        )
    return normalized


def _validate_cross_conversation_node(
    node: CrossConversationDecisionNodeRequest,
    *,
    field_name: str,
) -> dict[str, Any]:
    conversation_id = _positive_int(node.conversation_id)
    raw_label = _normalize_text(node.raw_label)
    graph_node_id = _normalize_text(node.graph_node_id)
    if conversation_id <= 0 or not raw_label or not graph_node_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} conversation_id, raw_label, and graph_node_id are required",
        )
    return {
        "conversation_id": conversation_id,
        "raw_label": raw_label,
        "graph_node_id": graph_node_id,
        "graph_node_label": _normalize_text(node.graph_node_label) or raw_label,
        "graph_node_type": _normalize_text(node.graph_node_type) or None,
    }


def _find_cross_conversation_resolution_decision(
    decisions: list[Any],
    *,
    pair_key: str,
    pair_hash: int,
) -> Any | None:
    for decision in decisions:
        if _normalize_key(getattr(decision, "subject", "")) != _CROSS_CONVERSATION_IDENTITY_SUBJECT:
            continue
        if _normalize_text(getattr(decision, "source_graph_node_id", "")) != pair_key:
            continue
        if _positive_int(getattr(decision, "target_participant_id", 0)) != pair_hash:
            continue
        return decision
    return None


def _build_party_naming_mentions_payload(
    display_label: str,
    evidence: PartyNamingEvidenceRequest | None,
) -> list[dict[str, Any]]:
    if evidence is None:
        return []

    raw_labels = [
        _normalize_text(item)
        for item in evidence.raw_labels
        if _normalize_text(item)
    ]
    conversation_ids = [_positive_int(item) for item in evidence.source_conversation_ids]
    raw_node_ids = [_normalize_text(item) for item in evidence.raw_node_ids]
    mention_count = max(len(raw_labels), len(conversation_ids), len(raw_node_ids))
    if mention_count <= 0:
        return []

    default_raw_label = raw_labels[0] if raw_labels else display_label
    mentions: list[dict[str, Any]] = []
    seen_keys: set[tuple[int, str, str]] = set()

    for index in range(mention_count):
        conversation_id = conversation_ids[index] if index < len(conversation_ids) else 0
        if conversation_id <= 0:
            continue

        raw_label = raw_labels[index] if index < len(raw_labels) else default_raw_label
        raw_node_id = raw_node_ids[index] if index < len(raw_node_ids) else ""
        seen_key = (conversation_id, raw_label.lower(), raw_node_id)
        if seen_key in seen_keys:
            continue
        seen_keys.add(seen_key)

        mentions.append(
            {
                "conversation_id": conversation_id,
                "raw_label": raw_label or display_label,
                "source": _PARTY_NAMING_MENTION_SOURCE,
                "confidence": 1.0,
                "graph_node_id": raw_node_id or None,
                "graph_node_label": raw_label or display_label,
                "graph_node_type": _PARTY_NAMING_GRAPH_NODE_TYPE,
            }
        )

    return mentions


def _party_naming_aliases_from_evidence(
    evidence: PartyNamingEvidenceRequest | None,
) -> list[str]:
    if evidence is None:
        return []

    aliases: list[str] = []
    seen: set[str] = set()
    for item in evidence.raw_labels:
        label = _normalize_text(item)
        key = _normalize_key(label)
        if not label or key in seen:
            continue
        seen.add(key)
        aliases.append(label)
    return aliases


def _serialize_dossier_with_participants(
    dossier,
    conversations,
    participants,
    mentions,
) -> dict:
    return serialize_dossier_detail(
        dossier,
        conversations,
        confirmed_participants=participants,
        participant_mentions=mentions,
    )


def _ensure_dossier_participant_write_allowed(dossier) -> None:
    if normalize_dossier_status(getattr(dossier, "status", DEFAULT_DOSSIER_STATUS)) == "archived":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Archived dossier cannot accept new participant changes",
        )


async def _load_dossier_graph_scope(
    db: AsyncSession,
    *,
    user_id: int,
    dossier,
) -> tuple[list, list[int], dict, list, list, dict]:
    conversations = await list_dossier_conversations(db, user_id, dossier.id)
    conversation_ids = [
        conversation.id
        for conversation in conversations
        if isinstance(getattr(conversation, "id", None), int)
    ]
    participant_aliases = serialize_dossier(dossier).get("participant_aliases", {})
    participants = await list_dossier_participants(db, user_id, dossier.id)
    mentions = await list_dossier_participant_mentions(db, user_id, dossier.id)
    participant_lookup = build_dossier_participant_lookup(participants)
    return (
        conversations,
        conversation_ids,
        participant_aliases,
        participants,
        mentions,
        participant_lookup,
    )


async def _build_raw_dossier_graph_payload(
    *,
    current_user: AuthenticatedUser,
    dossier,
    conversation_ids: list[int],
    participant_aliases: dict,
    participants: list,
    mentions: list,
    participant_lookup: dict,
) -> dict:
    return await get_dossier_graph_payload(
        user_id=current_user.user_id,
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


@router.get("")
async def get_dossiers(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_storage_schema()
    dossiers = await list_user_dossiers(db, current_user.user_id)
    return {
        "status": "success",
        "dossiers": [serialize_dossier(dossier) for dossier in dossiers],
    }


@router.post("")
async def create_dossier(
    payload: DossierCreateRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_storage_schema()
    dossier = await create_dossier_record(
        db,
        current_user.user_id,
        title=payload.title,
        case_type=payload.case_type,
        summary=payload.summary,
    )
    await db.commit()
    await db.refresh(dossier)
    return {"status": "success", "dossier": serialize_dossier(dossier)}


@router.get("/{dossier_id}")
async def get_dossier(
    dossier_id: int,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_storage_schema()
    dossier = await get_dossier_for_user(db, current_user.user_id, dossier_id)
    if dossier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dossier not found")
    conversations = await list_dossier_conversations(db, current_user.user_id, dossier.id)
    participants = await list_dossier_participants(db, current_user.user_id, dossier.id)
    mentions = await list_dossier_participant_mentions(db, current_user.user_id, dossier.id)
    return {
        "status": "success",
        "dossier": _serialize_dossier_with_participants(
            dossier,
            conversations,
            participants,
            mentions,
        ),
    }


@router.get("/{dossier_id}/participants")
async def get_dossier_participants(
    dossier_id: int,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_storage_schema()
    dossier = await get_dossier_for_user(db, current_user.user_id, dossier_id)
    if dossier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dossier not found")

    participants = await list_dossier_participants(db, current_user.user_id, dossier.id)
    mentions = await list_dossier_participant_mentions(db, current_user.user_id, dossier.id)
    participant_lookup = build_dossier_participant_lookup(participants)
    return {
        "status": "success",
        "dossier_id": dossier.id,
        "participants": [
            serialize_dossier_participant(participant)
            for participant in participants
        ],
        "mentions": [
            serialize_dossier_participant_mention(mention, participant_lookup)
            for mention in mentions
        ],
    }


@router.post("/{dossier_id}/participants")
async def create_dossier_participant(
    dossier_id: int,
    payload: DossierParticipantCreateRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_storage_schema()
    dossier = await get_dossier_for_user(db, current_user.user_id, dossier_id)
    if dossier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dossier not found")
    _ensure_dossier_participant_write_allowed(dossier)

    try:
        participant = await create_dossier_participant_record(
            db,
            dossier=dossier,
            user_id=current_user.user_id,
            label=payload.label,
            role=payload.role,
            party_side=payload.party_side,
            entity_type=payload.entity_type,
            description=payload.description,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await db.commit()
    await db.refresh(participant)
    return {
        "status": "success",
        "participant": serialize_dossier_participant(participant),
    }


@router.patch("/{dossier_id}/participants/{participant_id}")
async def update_dossier_participant(
    dossier_id: int,
    participant_id: int,
    payload: DossierParticipantUpdateRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_storage_schema()
    dossier = await get_dossier_for_user(db, current_user.user_id, dossier_id)
    if dossier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dossier not found")
    _ensure_dossier_participant_write_allowed(dossier)

    participant = await get_dossier_participant_for_user(
        db,
        current_user.user_id,
        dossier.id,
        participant_id,
    )
    if participant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Participant not found")

    if (
        payload.label is None
        and payload.role is None
        and payload.party_side is None
        and payload.entity_type is None
        and payload.description is None
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No participant fields provided")

    try:
        await update_dossier_participant_record(
            db,
            participant,
            label=payload.label,
            role=payload.role,
            party_side=payload.party_side,
            entity_type=payload.entity_type,
            description=payload.description,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await db.commit()
    await db.refresh(participant)
    return {
        "status": "success",
        "participant": serialize_dossier_participant(participant),
    }


@router.post("/{dossier_id}/participants/resolve")
async def resolve_dossier_participants(
    dossier_id: int,
    payload: DossierParticipantResolveRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_storage_schema()
    dossier = await get_dossier_for_user(db, current_user.user_id, dossier_id)
    if dossier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dossier not found")
    _ensure_dossier_participant_write_allowed(dossier)

    participant = None
    if payload.participant_id is not None:
        participant = await get_dossier_participant_for_user(
            db,
            current_user.user_id,
            dossier.id,
            payload.participant_id,
        )
        if participant is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Participant not found")
    elif payload.participant is not None:
        if is_generic_participant_label(payload.participant.label):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Generic participant labels must be linked to an existing participant",
            )
        try:
            participant = await create_dossier_participant_record(
                db,
                dossier=dossier,
                user_id=current_user.user_id,
                label=payload.participant.label,
                role=payload.participant.role,
                party_side=payload.participant.party_side,
                entity_type=payload.participant.entity_type,
                description=payload.participant.description,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="participant_id or participant payload is required",
        )

    mentions_payload = [
        item.model_dump() if hasattr(item, "model_dump") else item.dict()
        for item in payload.mentions
    ]
    try:
        mentions = await resolve_dossier_participant_mentions(
            db,
            dossier=dossier,
            user_id=current_user.user_id,
            participant=participant,
            mentions=mentions_payload,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await db.commit()
    await db.refresh(participant)
    for mention in mentions:
        await db.refresh(mention)
    participant_lookup = {participant.id: participant}
    return {
        "status": "success",
        "participant": serialize_dossier_participant(participant),
        "mentions": [
            serialize_dossier_participant_mention(mention, participant_lookup)
            for mention in mentions
        ],
    }


@router.get("/{dossier_id}/participant-candidates")
async def get_dossier_participant_candidates(
    dossier_id: int,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_storage_schema()
    dossier = await get_dossier_for_user(db, current_user.user_id, dossier_id)
    if dossier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dossier not found")

    (
        conversations,
        conversation_ids,
        participant_aliases,
        participants,
        mentions,
        participant_lookup,
    ) = await _load_dossier_graph_scope(
        db,
        user_id=current_user.user_id,
        dossier=dossier,
    )
    graph_payload = await _build_raw_dossier_graph_payload(
        current_user=current_user,
        dossier=dossier,
        conversation_ids=conversation_ids,
        participant_aliases=participant_aliases,
        participants=participants,
        mentions=mentions,
        participant_lookup=participant_lookup,
    )
    decisions = await list_dossier_entity_resolution_decisions(
        db,
        current_user.user_id,
        dossier.id,
    )
    candidates = build_dossier_participant_candidates(
        graph_payload=graph_payload,
        participants=participants,
        confirmed_mentions=mentions,
        participant_aliases=participant_aliases,
        resolution_decisions=decisions,
    )
    return {
        "status": "success",
        "dossier_id": dossier.id,
        "conversation_count": len(conversations),
        "candidates": candidates,
        "classifier_tasks_available": True,
    }


@router.get("/{dossier_id}/graph")
async def get_dossier_graph(
    dossier_id: int,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_storage_schema()
    dossier = await get_dossier_for_user(db, current_user.user_id, dossier_id)
    if dossier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dossier not found")

    (
        _conversations,
        conversation_ids,
        participant_aliases,
        participants,
        mentions,
        participant_lookup,
    ) = await _load_dossier_graph_scope(
        db,
        user_id=current_user.user_id,
        dossier=dossier,
    )
    return await _build_raw_dossier_graph_payload(
        current_user=current_user,
        dossier=dossier,
        conversation_ids=conversation_ids,
        participant_aliases=participant_aliases,
        participants=participants,
        mentions=mentions,
        participant_lookup=participant_lookup,
    )


@router.get("/{dossier_id}/display-graph")
async def get_dossier_display_graph(
    dossier_id: int,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_storage_schema()
    dossier = await get_dossier_for_user(db, current_user.user_id, dossier_id)
    if dossier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dossier not found")

    (
        _conversations,
        conversation_ids,
        participant_aliases,
        participants,
        mentions,
        participant_lookup,
    ) = await _load_dossier_graph_scope(
        db,
        user_id=current_user.user_id,
        dossier=dossier,
    )
    raw_graph_payload = await _build_raw_dossier_graph_payload(
        current_user=current_user,
        dossier=dossier,
        conversation_ids=conversation_ids,
        participant_aliases=participant_aliases,
        participants=participants,
        mentions=mentions,
        participant_lookup=participant_lookup,
    )
    display_graph = build_dossier_display_graph(
        raw_graph=raw_graph_payload.get("graph"),
        confirmed_participants=raw_graph_payload.get("confirmed_participants"),
        participant_mappings=raw_graph_payload.get("participant_mappings"),
    )
    return {
        "status": "success",
        "dossier_id": dossier.id,
        "view": "display",
        "display_graph": display_graph,
        "generated_at": raw_graph_payload.get("generated_at"),
    }


@router.get("/{dossier_id}/coordination-graph")
async def get_dossier_coordination_graph(
    dossier_id: int,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_storage_schema()
    try:
        payload = await build_dossier_coordination_graph_for_user(
            db,
            current_user=current_user,
            dossier_id=dossier_id,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail == "Dossier not found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail) from exc
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc

    return {
        "status": "success",
        "dossier_id": dossier_id,
        "view": "coordination",
        "coordination_graph": payload.get("coordination_graph", {}),
        "entity_onboarding": payload.get("entity_onboarding", []),
        "generated_at": payload.get("generated_at"),
    }


@router.get("/{dossier_id}/intelligence")
async def get_dossier_intelligence(
    dossier_id: int,
    conversation_id: int | None = None,
    include_display_graph: bool = True,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_storage_schema()
    try:
        return await build_dossier_intelligence(
            db,
            current_user=current_user,
            dossier_id=dossier_id,
            conversation_id=conversation_id,
            include_display_graph=include_display_graph,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail == "Dossier not found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail) from exc
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc


@router.post("/{dossier_id}/intelligence/tasks/party-naming/confirm")
async def confirm_dossier_party_naming(
    dossier_id: int,
    payload: PartyNamingConfirmRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_storage_schema()
    dossier = await get_dossier_for_user(db, current_user.user_id, dossier_id)
    if dossier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dossier not found")
    _ensure_dossier_participant_write_allowed(dossier)

    display_label = _normalize_text(payload.display_label)
    if not display_label:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="display_label is required",
        )
    if _normalize_key(payload.source_task.subject) != _SELF_ANCHOR_SUBJECT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="party_naming confirmation only supports self_anchor subject",
        )

    participants = await list_dossier_participants(db, current_user.user_id, dossier.id)
    existing_participant = _find_self_participant(participants)

    try:
        if existing_participant is None:
            participant = await create_dossier_participant_record(
                db,
                dossier=dossier,
                user_id=current_user.user_id,
                label=display_label,
                role=_PARTY_NAMING_ROLE,
                party_side="claimant",
                entity_type="person",
                description=None,
                confirmed_by_user=True,
            )
        else:
            participant = await update_dossier_participant_record(
                db,
                existing_participant,
                label=display_label,
                role=getattr(existing_participant, "role", None) or _PARTY_NAMING_ROLE,
                party_side="claimant",
                entity_type="person",
            )
            participant.confirmed_by_user = True
            db.add(participant)
            await db.flush()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    mentions_payload = _build_party_naming_mentions_payload(
        display_label,
        payload.source_task.evidence,
    )
    mentions = []
    if mentions_payload:
        try:
            mentions = await resolve_dossier_participant_mentions(
                db,
                dossier=dossier,
                user_id=current_user.user_id,
                participant=participant,
                mentions=mentions_payload,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await db.commit()
    await db.refresh(participant)
    for mention in mentions:
        await db.refresh(mention)
    participant_lookup = {participant.id: participant}
    aliases = _party_naming_aliases_from_evidence(payload.source_task.evidence)
    return {
        "status": "success",
        "participant_id": participant.id,
        "display_name": participant.label,
        "aliases": aliases,
        "updated": True,
        "participant": serialize_dossier_participant(participant),
        "mentions": [
            serialize_dossier_participant_mention(mention, participant_lookup)
            for mention in mentions
        ],
    }


@router.post("/{dossier_id}/intelligence/tasks/self-identity/confirm")
async def confirm_dossier_self_identity(
    dossier_id: int,
    payload: SelfIdentityConfirmRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_storage_schema()
    dossier = await get_dossier_for_user(db, current_user.user_id, dossier_id)
    if dossier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dossier not found")
    _ensure_dossier_participant_write_allowed(dossier)

    action = _normalize_self_identity_action(payload.action)
    conversation_id, raw_label, graph_node_id = _validate_self_anchor_source_node(
        payload.source_node
    )
    target_participant = await get_dossier_participant_for_user(
        db,
        current_user.user_id,
        dossier.id,
        payload.target_participant_id,
    )
    if target_participant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Participant not found")

    target_display_label = _normalize_text(getattr(target_participant, "label", ""))
    if not target_display_label:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Target participant must have a display label",
        )

    mentions = await list_dossier_participant_mentions(db, current_user.user_id, dossier.id)
    existing_source_mention = _find_source_node_mention(
        mentions,
        conversation_id=conversation_id,
        raw_label=raw_label,
        graph_node_id=graph_node_id,
    )
    if existing_source_mention is not None:
        existing_participant_id = _positive_int(
            getattr(existing_source_mention, "participant_id", 0)
        )
        if action == _SELF_IDENTITY_CONFIRM_ACTION_SAME:
            if existing_participant_id != target_participant.id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Self anchor source node is already linked to another participant",
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Self anchor source node is already linked to another participant",
            )

    decisions = await list_dossier_entity_resolution_decisions(
        db,
        current_user.user_id,
        dossier.id,
    )
    mention_records: list[Any] = []
    participant = target_participant
    decision = None

    if action == _SELF_IDENTITY_CONFIRM_ACTION_SAME:
        if existing_source_mention is None:
            mention_records = await resolve_dossier_participant_mentions(
                db,
                dossier=dossier,
                user_id=current_user.user_id,
                participant=target_participant,
                mentions=[
                    {
                        "conversation_id": conversation_id,
                        "raw_label": raw_label,
                        "source": _SELF_IDENTITY_CONFIRM_MENTION_SOURCE,
                        "confidence": 1.0,
                        "graph_node_id": graph_node_id,
                        "graph_node_label": _normalize_text(
                            payload.source_node.graph_node_label
                        )
                        or raw_label,
                        "graph_node_type": _normalize_text(payload.source_node.graph_node_type)
                        or "person",
                    }
                ],
            )
        else:
            mention_records = [existing_source_mention]

        decision = _find_self_identity_decision(
            decisions,
            decision_type=_SELF_IDENTITY_DECISION_SAME,
            graph_node_id=graph_node_id,
            target_participant_id=target_participant.id,
        )
        if decision is None:
            try:
                decision = await create_dossier_entity_resolution_decision(
                    db,
                    dossier=dossier,
                    user_id=current_user.user_id,
                    decision_type=_SELF_IDENTITY_DECISION_SAME,
                    subject=_SELF_ANCHOR_SUBJECT,
                    source_conversation_id=conversation_id,
                    source_graph_node_id=graph_node_id,
                    source_raw_label=raw_label,
                    target_participant_id=target_participant.id,
                    target_display_label=target_display_label,
                )
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    else:
        new_display_label = _normalize_optional_display_label(payload.new_display_label)
        if not new_display_label:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="new_display_label is required for confirm_different",
            )
        try:
            participant = await create_dossier_participant_record(
                db,
                dossier=dossier,
                user_id=current_user.user_id,
                label=new_display_label,
                role=_PARTY_NAMING_ROLE,
                party_side="claimant",
                entity_type="person",
                description=None,
                confirmed_by_user=True,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        try:
            mention_records = await resolve_dossier_participant_mentions(
                db,
                dossier=dossier,
                user_id=current_user.user_id,
                participant=participant,
                mentions=[
                    {
                        "conversation_id": conversation_id,
                        "raw_label": raw_label,
                        "source": _SELF_IDENTITY_CONFIRM_MENTION_SOURCE,
                        "confidence": 1.0,
                        "graph_node_id": graph_node_id,
                        "graph_node_label": _normalize_text(
                            payload.source_node.graph_node_label
                        )
                        or raw_label,
                        "graph_node_type": _normalize_text(payload.source_node.graph_node_type)
                        or "person",
                    }
                ],
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        try:
            decision = await create_dossier_entity_resolution_decision(
                db,
                dossier=dossier,
                user_id=current_user.user_id,
                decision_type=_SELF_IDENTITY_DECISION_NOT_SAME,
                subject=_SELF_ANCHOR_SUBJECT,
                source_conversation_id=conversation_id,
                source_graph_node_id=graph_node_id,
                source_raw_label=raw_label,
                target_participant_id=target_participant.id,
                target_display_label=target_display_label,
                new_participant_id=participant.id,
                new_display_label=participant.label,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await db.commit()
    await db.refresh(participant)
    for mention in mention_records:
        await db.refresh(mention)
    if decision is not None:
        await db.refresh(decision)

    participant_lookup = {participant.id: participant}
    return {
        "status": "success",
        "action": action,
        "participant": serialize_dossier_participant(participant),
        "mentions": [
            serialize_dossier_participant_mention(mention, participant_lookup)
            for mention in mention_records
        ],
        "decision": (
            serialize_dossier_entity_resolution_decision(decision)
            if decision is not None
            else None
        ),
    }


@router.post("/{dossier_id}/participants/cross-conversation/decide")
async def decide_cross_conversation_participant_resolution(
    dossier_id: int,
    payload: CrossConversationResolutionDecisionRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_storage_schema()
    dossier = await get_dossier_for_user(db, current_user.user_id, dossier_id)
    if dossier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dossier not found")
    _ensure_dossier_participant_write_allowed(dossier)

    subject = _normalize_cross_conversation_subject(payload.subject)
    decision_type = _normalize_cross_conversation_decision(payload.decision)
    source_node = _validate_cross_conversation_node(payload.source_node, field_name="source_node")
    target_node = _validate_cross_conversation_node(payload.target_node, field_name="target_node")

    try:
        pair_key = build_cross_conversation_resolution_pair_key(
            source_node["graph_node_id"],
            target_node["graph_node_id"],
        )
        pair_hash = build_cross_conversation_resolution_pair_hash(pair_key)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if source_node["graph_node_id"] == target_node["graph_node_id"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="cross conversation resolution requires two different graph nodes",
        )

    ordered_nodes = sorted(
        [source_node, target_node],
        key=lambda item: str(item.get("graph_node_id") or ""),
    )
    primary_node = ordered_nodes[0]
    secondary_node = ordered_nodes[1]

    decisions = await list_dossier_entity_resolution_decisions(
        db,
        current_user.user_id,
        dossier.id,
    )
    decision = _find_cross_conversation_resolution_decision(
        decisions,
        pair_key=pair_key,
        pair_hash=pair_hash,
    )

    if decision is None:
        try:
            decision = await create_dossier_entity_resolution_decision(
                db,
                dossier=dossier,
                user_id=current_user.user_id,
                decision_type=decision_type,
                subject=subject,
                source_conversation_id=primary_node["conversation_id"],
                source_graph_node_id=pair_key,
                source_raw_label=primary_node["raw_label"],
                target_participant_id=pair_hash,
                target_display_label=secondary_node["raw_label"],
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    else:
        decision.decision_type = decision_type
        decision.source_conversation_id = primary_node["conversation_id"]
        decision.source_graph_node_id = pair_key
        decision.source_raw_label = primary_node["raw_label"]
        decision.target_participant_id = pair_hash
        decision.target_display_label = secondary_node["raw_label"]
        db.add(decision)
        await db.flush()
        await touch_dossier(db, dossier=dossier)

    await db.commit()
    await db.refresh(decision)

    return {
        "status": "success",
        "decision": {
            **serialize_dossier_entity_resolution_decision(decision),
            "pair_key": pair_key,
            "source_node": source_node,
            "target_node": target_node,
            "reason": _normalize_text(payload.reason) or None,
            "candidate_key": _normalize_text(payload.candidate_key) or None,
            "task_key": _normalize_text(payload.task_key) or None,
        },
    }


@router.patch("/{dossier_id}")
async def update_dossier(
    dossier_id: int,
    payload: DossierUpdateRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_storage_schema()
    dossier = await get_dossier_for_user(db, current_user.user_id, dossier_id)
    if dossier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dossier not found")

    if (
        payload.title is None
        and payload.case_type is None
        and payload.summary is None
        and payload.status is None
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No dossier fields provided")

    try:
        normalized_status = (
            normalize_dossier_status(payload.status)
            if payload.status is not None
            else None
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await update_dossier_fields(
        db,
        dossier,
        title=payload.title,
        case_type=payload.case_type,
        summary=payload.summary,
        status=normalized_status,
    )
    await db.commit()
    await db.refresh(dossier)
    return {"status": "success", "dossier": serialize_dossier(dossier)}


@router.delete("/{dossier_id}")
async def archive_dossier(
    dossier_id: int,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_storage_schema()
    dossier = await get_dossier_for_user(db, current_user.user_id, dossier_id)
    if dossier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dossier not found")

    if normalize_dossier_status(getattr(dossier, "status", DEFAULT_DOSSIER_STATUS)) != "archived":
        await archive_dossier_record(db, dossier)
        await db.commit()
        await db.refresh(dossier)

    return {"status": "success", "dossier": serialize_dossier(dossier)}


async def _hard_delete_dossier_endpoint(
    dossier_id: int,
    current_user: AuthenticatedUser,
    db: AsyncSession,
):
    await ensure_storage_schema()
    dossier = await get_dossier_for_user(db, current_user.user_id, dossier_id)
    if dossier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dossier not found")

    result = await hard_delete_dossier_record(db, dossier)
    await db.commit()
    return {
        "success": True,
        "deleted": True,
        "dossier_id": dossier_id,
        "detached_conversations": int(result.get("detached_conversations", 0)),
        "deleted_participants": int(result.get("deleted_participants", 0)),
        "deleted_mentions": int(result.get("deleted_mentions", 0)),
    }


@router.delete("/{dossier_id}/hard")
async def hard_delete_dossier(
    dossier_id: int,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _hard_delete_dossier_endpoint(dossier_id, current_user, db)


@router.delete("/hard/{dossier_id}")
async def hard_delete_dossier_alias(
    dossier_id: int,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _hard_delete_dossier_endpoint(dossier_id, current_user, db)


@router.patch("/{dossier_id}/participants")
async def update_dossier_participants(
    dossier_id: int,
    payload: DossierParticipantsUpdateRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_storage_schema()
    dossier = await get_dossier_for_user(db, current_user.user_id, dossier_id)
    if dossier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dossier not found")

    aliases_payload = {
        key: (value.model_dump() if hasattr(value, "model_dump") else value.dict())
        for key, value in payload.participant_aliases.items()
    }
    await update_dossier_participant_aliases(db, dossier, aliases_payload)
    await db.commit()
    await db.refresh(dossier)
    return {"status": "success", "dossier": serialize_dossier(dossier)}


@router.post("/{dossier_id}/generate-summary")
async def generate_dossier_summary(
    dossier_id: int,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_storage_schema()
    dossier = await get_dossier_for_user(db, current_user.user_id, dossier_id)
    if dossier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dossier not found")

    try:
        generated = await generate_dossier_auto_summary(
            db,
            user_id=current_user.user_id,
            dossier=dossier,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    await update_dossier_auto_summary(
        db,
        dossier,
        auto_summary=generated["auto_summary"],
        auto_summary_source_count=generated["auto_summary_source_count"],
    )
    await db.commit()
    await db.refresh(dossier)
    conversations = await list_dossier_conversations(db, current_user.user_id, dossier.id)
    participants = await list_dossier_participants(db, current_user.user_id, dossier.id)
    mentions = await list_dossier_participant_mentions(db, current_user.user_id, dossier.id)
    response = {
        "status": "success",
        "dossier": _serialize_dossier_with_participants(
            dossier,
            conversations,
            participants,
            mentions,
        ),
    }
    if generated.get("warning"):
        response["warning"] = generated["warning"]
    return response
