"""
Clarifier node: sandwich-style follow-up questions.
"""
from __future__ import annotations

from typing import Any

from app.agent.graph import AgentState


async def generate_clarifier_question_v3(self, state: AgentState) -> str:
    # Import here to avoid circular imports at module level
    from app.services.legal_agent import _build_layered_advisor_payload_v3

    payload = _build_layered_advisor_payload_v3(
        (state.get("case_summary") or "").strip(),
        state["question"],
        (state.get("graph_context") or "").strip() or (state.get("graph_memory") or "").strip(),
    )
    if payload["needs_core_followup"]:
        return payload["followup"]
    return payload["constructive_next_step"]
