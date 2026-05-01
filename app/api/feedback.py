# app/api/feedback.py
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Feedback
from app.database.mysql_db import engine, get_db

logger = logging.getLogger(__name__)
router = APIRouter()


class FeedbackRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=255)
    rating: int = Field(..., ge=1, le=5)
    comment: str = ""
    turn_index: int = 0
    feedback_type: str = "general"


async def ensure_feedback_table() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Feedback.__table__.create, checkfirst=True)


@router.post("/feedback")
async def submit_feedback(
    req: FeedbackRequest, db: AsyncSession = Depends(get_db)
):
    try:
        await ensure_feedback_table()

        feedback = Feedback(
            session_id=req.session_id,
            rating=req.rating,
            comment=req.comment,
            turn_index=req.turn_index,
            feedback_type=req.feedback_type,
        )
        db.add(feedback)
        await db.commit()

        logger.info(
            "Stored feedback in MySQL | session_id=%s rating=%s type=%s",
            req.session_id,
            req.rating,
            req.feedback_type,
        )
        return {"status": "success"}
    except Exception:
        await db.rollback()
        logger.exception("Failed to store feedback in MySQL")
        raise HTTPException(status_code=500, detail="Failed to store feedback")
