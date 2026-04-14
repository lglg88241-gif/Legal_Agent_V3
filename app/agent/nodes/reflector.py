"""
Reflector node: evidence sufficiency analysis via deterministic pipeline.
"""
from __future__ import annotations

from typing import Any

from app.agent.graph import AgentState


def proactive_graph_mode_enabled(self, state: AgentState) -> bool:
    return bool(state.get("proactive_graph_mode", True))


def build_reflector_user_prompt(
    self,
    state: AgentState,
    chat_history: str,
    context_blocks: str,
) -> str:
    mode_enabled = self._proactive_graph_mode_enabled(state)
    mode_label = "ON" if mode_enabled else "OFF"
    case_summary = (state.get("case_summary") or "").strip() or "无案情摘要"
    mode_instruction = (
        "主动案情梳理模式已开启。除了法律要件是否缺失，你还必须检查案情摘要和最新输入里是否存在指代不明的实体、代词或模糊称谓。"
        "如果存在，把最关键的 1 到 2 个实体澄清要求写入 missing_info。"
    )
    if not mode_enabled:
        mode_instruction = (
            "主动案情梳理模式已关闭。你只关注法律要件是否缺失，忽略\u201c他\u201d\u201c老板\u201d\u201c对方\u201d\u201c那辆车\u201d这类实体指代歧义。"
        )

    return (
        f"用户问题：{state['question']}\n\n"
        f"主动案情梳理模式：{mode_label}\n"
        f"{mode_instruction}\n\n"
        f"当前卷宗净白摘要：\n{case_summary}\n\n"
        "请注意：摘要中已经明确的事实，一律视为已知证据，严禁重复写入 missing_info。\n\n"
        f"当前检索查询：{state.get('current_query', state['question'])}\n"
        f"当前循环次数：{state.get('loop_count', 0)}\n\n"
        f"当前检索结果：\n{context_blocks or '无有效检索结果'}"
    )


async def generate_reflection_v3(self, state: AgentState) -> dict[str, Any]:
    # Import here to avoid circular imports at module level
    from app.services.legal_agent import _build_layered_advisor_payload_v3

    payload = _build_layered_advisor_payload_v3(
        (state.get("case_summary") or "").strip(),
        state["question"],
        (state.get("graph_context") or "").strip() or (state.get("graph_memory") or "").strip(),
    )
    if payload["needs_core_followup"]:
        return {
            "reflection_status": "NEED_USER_INFO",
            "confidence_score": 0.62,
            "reflection_analysis": "可以先回答，但若要把金额或请求精确落表，还需要补充极少量核心字段。",
            "missing_info": payload["unknowns"],
            "clarification_questions": [payload["followup"]],
            "graph_focus": (state.get("case_summary") or "").strip() or state.get("current_query", state["question"]),
        }

    return {
        "reflection_status": "SUFFICIENT",
        "confidence_score": 0.84,
        "reflection_analysis": "核心条件已经足够，应直接输出分层答案，并进入执行导向的下一步建议。",
        "missing_info": [],
        "clarification_questions": [payload["constructive_next_step"]],
        "graph_focus": (state.get("case_summary") or "").strip() or state.get("current_query", state["question"]),
    }
