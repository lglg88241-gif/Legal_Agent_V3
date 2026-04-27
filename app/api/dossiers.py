from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthenticatedUser, get_current_user
from app.database.mysql_db import get_db
from app.services.conversation_store import (
    DEFAULT_DOSSIER_STATUS,
    archive_dossier_record,
    build_dossier_participant_candidates,
    build_dossier_participant_lookup,
    create_dossier_participant_record,
    create_dossier_record,
    ensure_storage_schema,
    get_dossier_for_user,
    get_dossier_participant_for_user,
    hard_delete_dossier_record,
    list_dossier_conversations,
    list_dossier_participant_mentions,
    list_dossier_participants,
    list_user_dossiers,
    normalize_dossier_status,
    resolve_dossier_participant_mentions,
    serialize_dossier,
    serialize_dossier_detail,
    serialize_dossier_participant,
    serialize_dossier_participant_mention,
    update_dossier_auto_summary,
    update_dossier_fields,
    update_dossier_participant_aliases,
    update_dossier_participant_record,
)
from app.services.dossier_graph import get_dossier_graph_payload
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


class DossierParticipantResolveRequest(BaseModel):
    participant_id: int | None = Field(default=None, gt=0)
    participant: DossierParticipantCreateRequest | None = None
    mentions: list[DossierParticipantResolveMentionRequest] = Field(
        ...,
        min_length=1,
        max_length=50,
    )


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
    graph_payload = await get_dossier_graph_payload(
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
    candidates = build_dossier_participant_candidates(
        graph_payload=graph_payload,
        participants=participants,
        confirmed_mentions=mentions,
        participant_aliases=participant_aliases,
    )
    return {
        "status": "success",
        "dossier_id": dossier.id,
        "conversation_count": len(conversations),
        "candidates": candidates,
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
