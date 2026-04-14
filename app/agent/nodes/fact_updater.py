"""
Fact updater node: incremental case summary merging via LLM.
"""
from __future__ import annotations

import logging
from typing import Any

from app.agent.graph import AgentState
from app.agent.prompts.advisor import FACT_UPDATER_SYSTEM_PROMPT_V2

logger = logging.getLogger(__name__)


async def fact_updater_node(self, state: AgentState) -> AgentState:
    previous_summary = (state.get("case_summary") or "").strip()
    question = " ".join((state.get("question") or "").strip().split())
    fallback_summary = previous_summary or question

    try:
        content = await self._chat_model(
            self.expert_llm,
            [
                {"role": "system", "content": FACT_UPDATER_SYSTEM_PROMPT_V2},
                {
                    "role": "user",
                    "content": (
                        f"旧摘要：{previous_summary or '无'}\n\n"
                        f"用户最新输入：{question}"
                    ),
                },
            ],
        )
        parsed = self._safe_json_load(content, {"case_summary": fallback_summary})
        case_summary = parsed.get("case_summary", fallback_summary)
        if not isinstance(case_summary, str):
            case_summary = fallback_summary
        case_summary = " ".join(case_summary.strip().split())
    except Exception as exc:
        logger.warning("Fact updater failed, fallback to previous summary: %s", exc)
        case_summary = fallback_summary

    retrieval_query = case_summary or question
    return {
        "case_summary": case_summary,
        "current_query": retrieval_query,
        "routed_query": "",
        "tool_calls": self._patched_build_tool_calls_for_intent("CASE_UPDATE", retrieval_query),
    }
