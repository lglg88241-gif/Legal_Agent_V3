"""
Clarifier node: sandwich-style follow-up questions.
"""
from __future__ import annotations

from app.agent.graph import AgentState
from app.schemas.case_state import CaseState
from app.logic.calculators import build_claim_payload


async def generate_clarifier_question_v3(self, state: AgentState) -> str:
    payload = build_claim_payload(
        CaseState.from_text(
            (state.get("case_summary") or "").strip(),
            state["question"],
            (state.get("graph_context") or "").strip() or (state.get("graph_memory") or "").strip(),
        )
    )
    if payload.needs_core_followup:
        return payload.followup
    return payload.constructive_next_step
