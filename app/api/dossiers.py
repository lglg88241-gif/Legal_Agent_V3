from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthenticatedUser, get_current_user
from app.database.mysql_db import get_db
from app.services.conversation_store import (
    DEFAULT_DOSSIER_STATUS,
    create_dossier_record,
    ensure_storage_schema,
    get_dossier_for_user,
    list_dossier_conversations,
    list_user_dossiers,
    normalize_dossier_status,
    serialize_dossier,
    serialize_dossier_detail,
    update_dossier_fields,
    archive_dossier_record,
    update_dossier_auto_summary,
)
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
    return {
        "status": "success",
        "dossier": serialize_dossier_detail(dossier, conversations),
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
    return {
        "status": "success",
        "dossier": serialize_dossier_detail(dossier, conversations),
    }
