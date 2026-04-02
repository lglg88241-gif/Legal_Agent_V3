from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, hash_password, verify_password
from app.database.mysql_db import Base, engine, get_db
from app.models import Conversation, Message, User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


class AuthRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=6, max_length=128)


async def ensure_auth_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def build_auth_response(user: User) -> dict:
    access_token = create_access_token({"sub": str(user.id), "username": user.username})
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "username": user.username,
        },
    }


@router.post("/register")
async def register(payload: AuthRequest, db: AsyncSession = Depends(get_db)):
    await ensure_auth_schema()

    existing_user = await db.scalar(select(User).where(User.username == payload.username))
    if existing_user is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already exists",
        )

    user = User(
        username=payload.username.strip(),
        hashed_password=hash_password(payload.password),
    )
    db.add(user)
    await db.flush()

    conversation = Conversation(
        user_id=user.id,
        session_id=f"user_{user.id}_default",
        title="默认会话",
    )
    db.add(conversation)

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Failed to register user")
        raise HTTPException(status_code=500, detail="Failed to register user")

    await db.refresh(user)
    return build_auth_response(user)


@router.post("/login")
async def login(payload: AuthRequest, db: AsyncSession = Depends(get_db)):
    await ensure_auth_schema()

    user = await db.scalar(select(User).where(User.username == payload.username))
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    return build_auth_response(user)
