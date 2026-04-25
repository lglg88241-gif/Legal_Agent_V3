"""
Fact updater node: incremental case summary merging via LLM.
"""
from __future__ import annotations

import logging
import re as _re
from typing import Any

from app.agent.graph import AgentState
from app.agent.prompts.advisor import FACT_UPDATER_SYSTEM_PROMPT_V2
from app.core.llm_provider import LLMProvider

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
    historical_reference = (state.get("historical_reference") or "").strip()
    question = " ".join((state.get("question") or "").strip().split())
    user_id = state.get("user_id")
    conversation_id = state.get("conversation_id")
    fallback_summary = previous_summary or question
    _saved_case_state = state.get("saved_case_state")
    existing_wage = 0.0
    if isinstance(_saved_case_state, dict):
        _saved_wage = _saved_case_state.get("monthly_salary")
        if isinstance(_saved_wage, (int, float)):
            existing_wage = float(_saved_wage)
    _state_wage = state.get("_detected_monthly_wage")
    carried_wage = (
        _extract_monthly_wage(question)
        or _extract_monthly_wage(previous_summary)
        or (_state_wage if _state_wage and _state_wage > 0 else None)
        or (existing_wage or None)
    )
    preserved_fact_block = ""
    if carried_wage is not None and _extract_monthly_wage(previous_summary) is None:
        preserved_fact_block = f"\n\n已保存结构化事实：月薪{_format_wage_value(carried_wage)}元"

    if not historical_reference and user_id and conversation_id:
        try:
            from app.services import graph_extractor as _graph_extractor

            get_user_cases_graph_overview = getattr(_graph_extractor, "get_user_cases_graph_overview", None)
            if get_user_cases_graph_overview is None:
                raise AttributeError("get_user_cases_graph_overview unavailable")
            user_cases = await get_user_cases_graph_overview(int(user_id))
            history_cases = [case for case in user_cases if case.get("case_id") != int(conversation_id)]
            history_lines: list[str] = []
            for case in history_cases[:5]:
                case_id = case.get("case_id", "?")
                sources = case.get("source_names", [])
                targets = case.get("target_names", [])
                all_entities = list(dict.fromkeys([*sources, *targets]))[:8]
                history_lines.append(f"  案件#{case_id}: {', '.join(all_entities)}")
            if history_lines:
                historical_reference = (
                    "用户历史案件实体（仅供参考，不得作为当前案件事实参与计算）：\n"
                    + "\n".join(history_lines)
                )
        except Exception as exc:
            logger.warning("Fact updater failed to load historical reference: %s", exc)

    try:
        llm_router = self._get_llm_router()
        logger.info(
            "Fact updater | requested_provider=%s requested_model=%s base_url=%s",
            LLMProvider.GPT_5_1.value,
            "gpt-5.1",
            getattr(llm_router, "openai_base_url", ""),
        )
        content = await self._chat_model(
            self.summary_llm,
            [
                {"role": "system", "content": FACT_UPDATER_SYSTEM_PROMPT_V2},
                {
                    "role": "user",
                    "content": (
                        f"旧摘要：{previous_summary or '无'}"
                        f"{preserved_fact_block}\n\n"
                        f"<historical_reference>\n{historical_reference or '无'}\n</historical_reference>\n\n"
                        f"用户最新输入：{question}"
                    ),
                },
            ],
            provider=LLMProvider.GPT_5_1,
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
        "historical_reference": historical_reference,
        **_extra,
    }
