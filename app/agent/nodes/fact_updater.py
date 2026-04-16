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


def _format_wage_value(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


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


def _merge_monthly_wage_into_summary(case_summary: str, wage: float | None) -> str:
    """在摘要中确定性保留月薪，便于下一轮从文本中重新提取。"""
    normalized = " ".join((case_summary or "").strip().split())
    if wage is None or wage <= 0:
        return normalized

    detected = _extract_monthly_wage(normalized)
    if detected is not None and abs(detected - wage) < 0.01:
        return normalized

    wage_fragment = f"月薪{_format_wage_value(wage)}元"
    if not normalized:
        return wage_fragment
    if normalized.endswith(("。", "；", ";")):
        return f"{normalized}{wage_fragment}"
    return f"{normalized}；{wage_fragment}"


async def fact_updater_node(self, state: AgentState) -> AgentState:
    previous_summary = (state.get("case_summary") or "").strip()
    question = " ".join((state.get("question") or "").strip().split())
    fallback_summary = previous_summary or question
    _saved_case_state = state.get("saved_case_state")
    existing_wage = 0.0
    if isinstance(_saved_case_state, dict):
        _saved_wage = _saved_case_state.get("monthly_salary")
        if isinstance(_saved_wage, (int, float)):
            existing_wage = float(_saved_wage)
    carried_wage = _extract_monthly_wage(question) or _extract_monthly_wage(previous_summary) or (existing_wage or None)
    preserved_fact_block = ""
    if carried_wage is not None and _extract_monthly_wage(previous_summary) is None:
        preserved_fact_block = f"\n\n已保存结构化事实：月薪{_format_wage_value(carried_wage)}元"

    try:
        content = await self._chat_model(
            self.summary_llm,
            [
                {"role": "system", "content": FACT_UPDATER_SYSTEM_PROMPT_V2},
                {
                    "role": "user",
                    "content": (
                        f"旧摘要：{previous_summary or '无'}"
                        f"{preserved_fact_block}\n\n"
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

    case_summary = _merge_monthly_wage_into_summary(case_summary, carried_wage)
    retrieval_query = case_summary or question
    _wage = _extract_monthly_wage(question) or _extract_monthly_wage(case_summary)
    _extra: dict[str, Any] = {}
    if _wage is not None and existing_wage == 0:
        _extra["_detected_monthly_wage"] = _wage

    return {
        "case_summary": case_summary,
        "current_query": retrieval_query,
        "routed_query": "",
        "tool_calls": self._patched_build_tool_calls_for_intent("CASE_UPDATE", retrieval_query),
        **_extra,
    }
