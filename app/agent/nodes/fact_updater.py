"""
Fact updater node: incremental case summary merging via LLM.
"""
from __future__ import annotations

import logging
import re as _re
from typing import Any

from app.agent.graph import AgentState
from app.agent.prompts.advisor import FACT_UPDATER_SYSTEM_PROMPT_V2

logger = logging.getLogger(__name__)


def _extract_monthly_wage(text: str) -> float | None:
    """从文本中提取月薪/月工资数字，返回 float 或 None。"""
    patterns = [
        r"月薪[：:\s]*(\d+(?:[,，]\d+)*(?:\.\d+)?)\s*元?",
        r"月工资[：:\s]*(\d+(?:[,，]\d+)*(?:\.\d+)?)\s*元?",
        r"每月[工薪资]+[：:\s]*(\d+(?:[,，]\d+)*(?:\.\d+)?)\s*元?",
        r"工资[：:\s]*每月[：:\s]*(\d+(?:[,，]\d+)*(?:\.\d+)?)\s*元?",
        r"底薪[：:\s]*(\d+(?:[,，]\d+)*(?:\.\d+)?)\s*元?",
    ]
    for pattern in patterns:
        match = _re.search(pattern, text)
        if not match:
            continue
        raw = match.group(1).replace(",", "").replace("，", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        if 100 <= value <= 500000:
            return value
    return None


async def fact_updater_node(self, state: AgentState) -> AgentState:
    previous_summary = (state.get("case_summary") or "").strip()
    question = " ".join((state.get("question") or "").strip().split())
    fallback_summary = previous_summary or question

    try:
        content = await self._chat_model(
            self.summary_llm,
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
    _wage = _extract_monthly_wage(question) or _extract_monthly_wage(case_summary)
    _extra: dict[str, Any] = {}
    _saved_case_state = state.get("saved_case_state")
    existing_wage = 0.0
    if isinstance(_saved_case_state, dict):
        _saved_wage = _saved_case_state.get("monthly_salary")
        if isinstance(_saved_wage, (int, float)):
            existing_wage = float(_saved_wage)
    if _wage is not None and existing_wage == 0:
        _extra["_detected_monthly_wage"] = _wage

    return {
        "case_summary": case_summary,
        "current_query": retrieval_query,
        "routed_query": "",
        "tool_calls": self._patched_build_tool_calls_for_intent("CASE_UPDATE", retrieval_query),
        **_extra,
    }
