# app/api/feedback.py
import logging
import time
from fastapi import APIRouter
from pydantic import BaseModel
from app.database.mysql_db import engine
from sqlalchemy import text

logger = logging.getLogger(__name__)
router = APIRouter()

# 🌟 严格对标前端 AnalysisPanel.vue emits 的反馈数据
class FeedbackRequest(BaseModel):
    session_id: str
    rating: int
    comment: str = ""
    # 兼容前端可能传的其他字段
    turn_index: int = 0
    feedback_type: str = "general"

@router.post("/feedback")
async def submit_feedback(req: FeedbackRequest):
    async with engine.begin() as conn:
        # 建表：专为论文数据可视化准备
        await conn.execute(text('''
            CREATE TABLE IF NOT EXISTS system_feedback (
                id INT AUTO_INCREMENT PRIMARY KEY,
                session_id VARCHAR(255), 
                rating INT, 
                comment TEXT,
                feedback_type VARCHAR(50), 
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        '''))
        # 插入真实的反馈数据
        await conn.execute(text('''
            INSERT INTO system_feedback (session_id, rating, comment, feedback_type)
            VALUES (:sid, :rating, :comment, :type)
        '''), {"sid": req.session_id, "rating": req.rating, "comment": req.comment, "type": req.feedback_type})
    
    logger.info(f"📈 [MySQL埋点] 收到用户真实评分: {req.rating} 星 | 评论: {req.comment}")
    return {"status": "success"}