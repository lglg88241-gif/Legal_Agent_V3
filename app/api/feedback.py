import logging
from fastapi import APIRouter
from pydantic import BaseModel
from app.database.mysql_db import engine
from sqlalchemy import text

logger = logging.getLogger(__name__)
router = APIRouter()

class FeedbackRequest(BaseModel):
    session_id: str
    turn_index: int
    rating: int
    comment: str = None
    feedback_type: str = "general"

@router.post("/feedback")
async def submit_feedback(req: FeedbackRequest):
    # 🌟 核心突破：摒弃内存字典，评价直接落入 MySQL，为论文 ECharts 图表提供绝对真实的数据源！
    async with engine.begin() as conn:
        await conn.execute(text('''
            CREATE TABLE IF NOT EXISTS system_feedback (
                id INT AUTO_INCREMENT PRIMARY KEY,
                session_id VARCHAR(255), rating INT, comment TEXT,
                feedback_type VARCHAR(50), created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        '''))
        await conn.execute(text('''
            INSERT INTO system_feedback (session_id, rating, comment, feedback_type)
            VALUES (:sid, :rating, :comment, :type)
        '''), {"sid": req.session_id, "rating": req.rating, "comment": req.comment or "", "type": req.feedback_type})
    
    logger.info(f"📊 [MySQL落库] 评价数据已落库, 评分: {req.rating}")
    return {"status": "success"}
