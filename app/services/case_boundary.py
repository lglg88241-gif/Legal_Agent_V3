from __future__ import annotations

import logging
import threading

from langchain_community.chat_models import ChatZhipuAI
from langchain_core.messages import HumanMessage, SystemMessage

from app.core.config import settings

logger = logging.getLogger(__name__)

CASE_BOUNDARY_PROMPT = """
你是一个法律案件边界探测器。
这是当前案件已有的案情图谱或总结：{current_graph_summary}
这是最近的会话上下文：{recent_history}
用户输入了新消息：{user_input}

请判断新消息是：
1. 继续补充当前案件细节 / 继续追问当前案件
2. 开启了一个完全不相关的新法律纠纷

你必须严格只输出以下两种结果之一：
SAME_CASE
NEW_CASE
""".strip()

_boundary_llm: ChatZhipuAI | None = None
_boundary_lock = threading.Lock()


def _get_boundary_llm() -> ChatZhipuAI:
    global _boundary_llm

    if _boundary_llm is None:
        with _boundary_lock:
            if _boundary_llm is None:
                _boundary_llm = ChatZhipuAI(
                    model="glm-4",
                    api_key=settings.ZHIPUAI_API_KEY,
                    temperature=0.0,
                )
    return _boundary_llm


async def detect_case_boundary(
    *,
    current_graph_summary: str,
    recent_history: str,
    user_input: str,
) -> str:
    graph_text = (current_graph_summary or "").strip()
    history_text = (recent_history or "").strip()
    clean_input = (user_input or "").strip()

    if not clean_input:
        return "SAME_CASE"

    if not graph_text and not history_text:
        return "SAME_CASE"

    try:
        response = await _get_boundary_llm().ainvoke(
            [
                SystemMessage(content="你必须只返回 SAME_CASE 或 NEW_CASE。"),
                HumanMessage(
                    content=CASE_BOUNDARY_PROMPT.format(
                        current_graph_summary=graph_text or "无案情图谱",
                        recent_history=history_text or "无近期上下文",
                        user_input=clean_input,
                    )
                ),
            ]
        )
        content = response.content if isinstance(response.content, str) else str(response.content)
        normalized = content.strip().upper()
        if "NEW_CASE" in normalized:
            return "NEW_CASE"
        return "SAME_CASE"
    except Exception as exc:
        logger.warning("Case boundary detection failed, fallback to SAME_CASE: %s", exc)
        return "SAME_CASE"
