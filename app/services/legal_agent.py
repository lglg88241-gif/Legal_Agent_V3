"""
Legal Graph Agent — thin orchestrator.

This module defines the LegalGraphAgent class and wires up all node
implementations from ``app.agent.nodes.*``. The class itself contains
only LLM initialization, thin node wrappers, and the ``ask()`` entry point.

All business logic lives in the domain node modules:
  - app.agent.nodes.router      — triple-track routing
  - app.agent.nodes.fact_updater — case summary merging
  - app.agent.nodes.researcher   — RAG retrieval + graph memory
  - app.agent.nodes.reflector    — evidence sufficiency analysis
  - app.agent.nodes.advisor      — layered legal analysis + LLM polishing
  - app.agent.nodes.clarifier    — sandwich-style follow-up questions
  - app.agent.nodes._llm         — shared LLM utilities
"""
from __future__ import annotations

import re
import json
import logging
from typing import Any

from langchain_community.chat_models import ChatZhipuAI

from app.agent.graph import AgentState, build_legal_agent_graph
from app.core.config import settings

# --- Import node implementations ---
from app.agent.nodes._llm import (
    GREETING_RESPONSE,
    GRAPH_TOOL,
    chat_model,
    safe_json_load,
    extract_candidate_sources,
    normalize_sources,
    merge_tool_results,
    build_context_blocks,
    execute_tool_call,
    execute_tool_calls,
)
from app.agent.nodes.router import (
    has_substantive_legal_intent,
    history_indicates_legal_dialogue,
    is_contextual_followup,
    looks_like_chitchat,
    detect_intents,
    fallback_route_decision,
    dedupe_tool_calls,
    enforce_composite_routes,
    is_case_update,
    is_general_consult,
    build_tool_calls_for_intent,
    route_decision,
    tool_router_node,
)
from app.agent.nodes.fact_updater import fact_updater_node
from app.agent.nodes.researcher import vector_researcher_node
from app.agent.nodes.reflector import (
    proactive_graph_mode_enabled,
    build_reflector_user_prompt,
    generate_reflection_v3,
)
from app.agent.nodes.advisor import generate_final_answer_v3
from app.agent.nodes.clarifier import generate_clarifier_question_v3

logger = logging.getLogger(__name__)


class LegalGraphAgent:
    def __init__(self):
        self.router_llm = ChatZhipuAI(
            model="glm-4",
            api_key=settings.ZHIPUAI_API_KEY,
            temperature=0.1,
        )
        self.reflector_llm = ChatZhipuAI(
            model="glm-4",
            api_key=settings.ZHIPUAI_API_KEY,
            temperature=0.1,
        )
        self.expert_llm = ChatZhipuAI(
            model="glm-4",
            api_key=settings.ZHIPUAI_API_KEY,
            temperature=0.1,
        )
        self.graph = build_legal_agent_graph(self)

    # === Shared LLM utilities (delegated to _llm module) ===
    _chat_model = chat_model
    _safe_json_load = safe_json_load
    _extract_candidate_sources = extract_candidate_sources
    _normalize_sources = normalize_sources
    _merge_tool_results = merge_tool_results
    _build_context_blocks = build_context_blocks
    _execute_tool_call = execute_tool_call
    _execute_tool_calls = execute_tool_calls

    # === Router utilities (delegated to router module) ===
    _has_substantive_legal_intent = has_substantive_legal_intent
    _history_indicates_legal_dialogue = history_indicates_legal_dialogue
    _is_contextual_followup = is_contextual_followup
    _looks_like_chitchat = looks_like_chitchat
    _detect_intents = detect_intents
    _fallback_route_decision = fallback_route_decision
    _dedupe_tool_calls = dedupe_tool_calls
    _enforce_composite_routes = enforce_composite_routes
    _patched_is_case_update = is_case_update
    _patched_is_general_consult = is_general_consult
    _patched_build_tool_calls_for_intent = build_tool_calls_for_intent
    _route_decision = route_decision
    _patched_route_decision = route_decision

    # === Reflector utilities ===
    _proactive_graph_mode_enabled = proactive_graph_mode_enabled
    _patched_build_reflector_user_prompt = build_reflector_user_prompt

    # === Core generation methods ===
    _generate_reflection = generate_reflection_v3
    _generate_final_answer = generate_final_answer_v3
    _generate_clarifier_question = generate_clarifier_question_v3

    # === Graph node methods ===

    tool_router_node = tool_router_node
    fact_updater_node = fact_updater_node
    vector_researcher_node = vector_researcher_node

    async def greeting_node(self, state: AgentState) -> AgentState:
        return {
            "response": GREETING_RESPONSE,
            "sources": [],
            "is_final": True,
        }

    async def neo4j_specialist_node(self, state: AgentState) -> AgentState:
        graph_focus = state.get("graph_focus") or state.get("current_query") or state["question"]
        graph_result = await self._execute_tool_call(
            GRAPH_TOOL,
            {"entity_name": graph_focus},
        )
        merged_results = self._merge_tool_results(state.get("tool_results", []), [graph_result])
        return {
            "tool_results": merged_results,
            "current_query": graph_focus,
        }

    async def reflector_node(self, state: AgentState) -> AgentState:
        reflection = await self._generate_reflection(state)
        return {
            **reflection,
            "loop_count": int(state.get("loop_count", 0)) + 1,
        }

    async def clarifier_node(self, state: AgentState) -> AgentState:
        question = await self._generate_clarifier_question(state)
        return {
            "response": question,
            "sources": self._extract_candidate_sources(state.get("tool_results", [])),
            "is_final": False,
        }

    async def legal_advisor_node(self, state: AgentState) -> AgentState:
        return await self._generate_final_answer(state)

    # === Top-level entry point ===

    async def ask(
        self,
        user_id: int,
        session_id: str,
        question: str,
        chat_history: str = "",
        case_summary: str = "",
        graph_memory: str = "",
        is_precise_mode: bool = False,
        proactive_graph_mode: bool = True,
        conversation_id: int | None = None,
    ) -> dict[str, Any]:
        initial_state: AgentState = {
            "user_id": user_id,
            "conversation_id": int(conversation_id) if conversation_id is not None else 0,
            "session_id": session_id,
            "question": question,
            "is_precise_mode": bool(is_precise_mode),
            "proactive_graph_mode": bool(proactive_graph_mode),
            "chat_history": chat_history,
            "case_summary": case_summary,
            "graph_context": graph_memory,
            "graph_memory": graph_memory,
            "current_query": question,
            "route_intent": "GENERAL_CONSULT",
            "loop_count": 0,
            "confidence_score": 0.0,
            "missing_info": [],
            "clarification_questions": [],
            "tool_calls": [],
            "tool_results": [],
            "sources": [],
            "response": "",
            "reflection_status": "",
            "reflection_analysis": "",
            "graph_focus": "",
            "is_final": False,
        }

        final_state = await self.graph.ainvoke(
            initial_state,
            config={
                "configurable": {
                    "thread_id": session_id,
                    "user_id": str(user_id),
                }
            },
        )
        return {
            "response": final_state.get("response") or "未找到依据。",
            "sources": final_state.get("sources") or [],
            "is_final": bool(final_state.get("is_final", False)),
            "confidence_score": float(final_state.get("confidence_score", 0.0)),
            "missing_info": final_state.get("missing_info") or [],
            "route_intent": str(final_state.get("route_intent") or "GENERAL_CONSULT").upper(),
            "case_summary": final_state.get("case_summary") or case_summary or "",
        }


# ======================================================================
# Legacy computation functions — kept at module level for backward
# compatibility with tests that import legal_agent._xxx directly.
# These will be removed once all consumers switch to app.logic.calculators
# and app.schemas.case_state.
# ======================================================================

def _normalize_summary_text(text: str) -> str:
    return " ".join((text or "").strip().split())


def _extract_numeric_amount(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:元|块|人民币|月薪|工资)", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _extract_month_span(text: str) -> int | None:
    worked_match = re.search(r"(?:工作|任职|入职后|用工)\D{0,6}(\d+)\s*个?月", text)
    if worked_match:
        try:
            return max(int(worked_match.group(1)), 0)
        except ValueError:
            return None

    month_match = re.search(r"(\d+)\s*个月", text)
    if month_match:
        try:
            return max(int(month_match.group(1)), 0)
        except ValueError:
            return None

    range_match = re.search(
        r"(\d{4})[.\-/年](\d{1,2})[.\-/月]?(?:\d{1,2}日?)?.{0,12}?"
        r"(?:到|至|-|~)"
        r".{0,12}?"
        r"(\d{4})[.\-/年](\d{1,2})[.\-/月]?(?:\d{1,2}日?)?",
        text,
    )
    if not range_match:
        return None

    start_year, start_month, end_year, end_month = map(int, range_match.groups())
    months = (end_year - start_year) * 12 + (end_month - start_month)
    return months + 1 if months >= 0 else None


def _build_case_fact_lines(case_summary: str, question: str, graph_context: str) -> list[str]:
    facts: list[str] = []
    for raw in (case_summary, question, graph_context):
        clean = _normalize_summary_text(raw)
        if not clean:
            continue
        for part in re.split(r"[；;。！？\n]+", clean):
            item = part.strip(" ，,")
            if not item or item in facts:
                continue
            facts.append(item)
    return facts[:8]


def _build_practical_suggestions(case_summary: str, question: str) -> list[str]:
    merged = f"{case_summary} {question}"
    suggestions: list[str] = []
    if any(token in merged for token in ("未签合同", "没签合同", "未签劳动合同", "没有劳动合同")):
        suggestions.append("立即固定入职时间、工资标准、考勤记录、工资流水、聊天记录，作为劳动关系和未签书面合同事实的核心证据。")
        suggestions.append("如担心对方转移财产、注销公司或抽逃资产，可评估申请财产保全。")
    if any(token in merged for token in ("拖欠工资", "欠薪", "未发工资", "克扣工资")):
        suggestions.append("同步整理工资条、银行流水、考勤和催要记录，优先准备劳动仲裁材料。")
    if any(token in merged for token in ("辞退", "开除", "解除劳动关系", "逼离职", "被迫离职")):
        suggestions.append("保留解除通知、录音、绩效考核和岗位调整记录，用于判断是否构成违法解除或被迫解除。")
    if not suggestions:
        suggestions.append("先把关键时间、金额、主体身份和证据材料整理成时间轴，再推进具体请求。")
    return suggestions[:3]


def _build_rule_analysis(case_summary: str, question: str) -> tuple[list[str], list[str]]:
    merged = f"{case_summary} {question}"
    rules: list[str] = []
    unknowns: list[str] = []

    if any(token in merged for token in ("未签合同", "没签合同", "未签劳动合同", "没有劳动合同")):
        rules.append("若已实际用工而单位在法定期限内未签书面劳动合同，通常可主张未签书面劳动合同的二倍工资差额。")
        salary = _extract_numeric_amount(merged)
        months = _extract_month_span(merged)
        if salary is not None and months is not None:
            compensable_months = max(min(months - 1, 11), 0)
            rules.append(
                f"按现有事实估算，若月工资约为 {salary:.0f} 元、未签约期间约 {months} 个月，"
                f"二倍工资差额可先按 {compensable_months} 个月工资、约 {salary * compensable_months:.0f} 元测算。"
            )
        else:
            missing_parts = []
            if salary is None:
                missing_parts.append("月工资")
            if months is None:
                missing_parts.append("未签合同持续时间")
            if missing_parts:
                unknowns.append(f"二倍工资差额的精确金额仍取决于{'、'.join(missing_parts)}。")

    if any(token in merged for token in ("劳动关系", "入职", "考勤", "发工资", "工资流水")):
        rules.append("即便没有书面劳动合同，只要存在管理、考勤、报酬支付等事实，仍可优先从劳动关系成立角度主张权利。")

    if any(token in merged for token in ("公司变更", "变更主体", "更名", "注销", "股东变更")):
        rules.append("若用工主体发生更名、分立、并购或资产转移，需要同步核查实际用工主体、工资支付主体和责任承继链条。")

    return rules[:4], unknowns[:3]


def _build_advisor_fallback(case_summary: str, question: str, graph_context: str) -> str:
    facts = _build_case_fact_lines(case_summary, question, graph_context)
    rules, unknowns = _build_rule_analysis(case_summary, question)
    suggestions = _build_practical_suggestions(case_summary, question)

    sections: list[str] = []
    if facts:
        sections.append("已知事实：\n- " + "\n- ".join(facts))
    if rules:
        sections.append("法律分析：\n- " + "\n- ".join(rules))
    else:
        sections.append("法律分析：\n- 现有案情已经足以先做基础判断，应围绕劳动关系、合同义务或赔偿责任继续推进。")
    if unknowns:
        sections.append("不确定事项：\n- " + "\n- ".join(unknowns))
    sections.append("实操建议：\n- " + "\n- ".join(suggestions))
    return "\n\n".join(sections)


def _safe_extract_amount_v3(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:元|块|人民币|月薪|工资)", text)
    if not match:
        match = re.search(r"(?:月薪|工资|底薪|薪资|薪水)\s*(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _safe_extract_months_v3(text: str) -> int | None:
    relative_span = re.search(
        r"去年\s*(\d{1,2})\s*月.*?今年\s*(\d{1,2})\s*月(?:底|末)?",
        text,
    )
    if relative_span:
        try:
            start_month = int(relative_span.group(1))
            end_month = int(relative_span.group(2))
            if 1 <= start_month <= 12 and 1 <= end_month <= 12:
                months = (12 - start_month + 1) + end_month
                return months if months > 0 else None
        except ValueError:
            return None

    worked_match = re.search(r"(?:工作|任职|入职后|用工)\D{0,6}(\d+)\s*个月", text)
    if worked_match:
        try:
            return max(int(worked_match.group(1)), 0)
        except ValueError:
            return None

    month_match = re.search(r"(\d+)\s*个月", text)
    if month_match:
        try:
            return max(int(month_match.group(1)), 0)
        except ValueError:
            return None
    return None


def _safe_extract_owed_wages(text: str, monthly_salary: float | None = None) -> float:
    """提取拖欠工资金额。支持 '拖欠N月工资' 或直接金额 '欠薪XXXX元'。"""
    direct = re.search(r"(?:欠薪|拖欠工资|欠发工资)\s*(\d+(?:\.\d+)?)\s*(?:元|块|万)?", text)
    if direct:
        val = float(direct.group(1))
        if "万" in text[direct.start():direct.end() + 2]:
            val *= 10000
        return val

    month_match = re.search(r"(?:拖欠|欠)\s*(\d+|[一二三四五六七八九十两]+)\s*(?:个?月|个月的?)\s*(?:工资|薪)", text)
    if not month_match:
        month_match = re.search(r"(?:拖欠|欠)\s*([一二三四五六七八九十两]+|[0-9]+)\s*月\s*(?:工资|薪)?", text)
    if month_match and monthly_salary:
        cn_map = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
                  "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        raw = month_match.group(1)
        n = cn_map.get(raw, None)
        if n is None:
            try:
                n = int(raw)
            except ValueError:
                return 0.0
        return monthly_salary * n
    return 0.0


def _safe_extract_deposit(text: str) -> float:
    """提取押金金额。支持 'N万押金' 'N元押金' '押金N万' 等。"""
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:万|万元)\s*(?:押金|保证金)", text)
    if match:
        return float(match.group(1)) * 10000
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:元|块)\s*(?:押金|保证金)", text)
    if match:
        return float(match.group(1))
    match = re.search(r"(?:押金|保证金)\s*(\d+(?:\.\d+)?)\s*(?:万|万元)", text)
    if match:
        return float(match.group(1)) * 10000
    match = re.search(r"(?:押金|保证金)\s*(\d+(?:\.\d+)?)\s*(?:元|块)?", text)
    if match:
        return float(match.group(1))
    return 0.0


def _build_layered_advisor_payload_v3(case_summary: str, question: str, graph_context: str) -> dict[str, Any]:
    merged = " ".join(part for part in [case_summary, question, graph_context] if part).strip()
    facts = _build_case_fact_lines(case_summary, question, graph_context)
    amount = _safe_extract_amount_v3(merged)
    months = _safe_extract_months_v3(merged)

    calculation_lines: list[str] = []
    strategy_lines: list[str] = []
    action_lines: list[str] = []
    unknowns: list[str] = []

    has_unsigned_contract = any(token in merged for token in ("未签合同", "没签合同", "没签劳动合同", "未签劳动合同", "没有劳动合同"))
    double_salary_total = 0.0
    if has_unsigned_contract:
        calculation_lines.append("只要已经形成实际用工关系，即使没有书面劳动合同，也可以先主张未签书面劳动合同的二倍工资差额。")
        if amount is not None and months is not None:
            compensable_months = max(min(months - 1, 11), 0)
            double_salary_total = amount * compensable_months
            calculation_lines.append(
                f"按现有信息，计算公式可以先写为：月工资 {amount:.0f} 元 × 可主张月数 {compensable_months} = 二倍工资差额约 {double_salary_total:.0f} 元。"
            )
        else:
            if amount is None:
                unknowns.append("月工资标准")
            if months is None:
                unknowns.append("未签合同持续月数")

    owed_wages = _safe_extract_owed_wages(merged, amount)
    deposit = _safe_extract_deposit(merged)
    has_owed = any(token in merged for token in ("拖欠", "欠薪", "未发工资", "克扣工资", "欠发"))
    has_deposit = any(token in merged for token in ("押金", "保证金"))

    if owed_wages > 0:
        calculation_lines.append(f"拖欠工资部分：约 {owed_wages:.0f} 元。")
    elif has_owed and amount is not None:
        calculation_lines.append("存在工资拖欠事实，待确认具体欠薪月数后可精确计算。")

    if deposit > 0:
        calculation_lines.append(f"押金/保证金部分：{deposit:.0f} 元，用人单位收取押金本身违反《劳动合同法》第九条，可依法要求全额退还。")
    elif has_deposit:
        calculation_lines.append("存在押金争议，用人单位向劳动者收取押金违反《劳动合同法》第九条，可要求退还。")

    claim_total = double_salary_total + owed_wages + deposit
    if claim_total > 0:
        parts = []
        if double_salary_total > 0:
            parts.append(f"二倍工资差额 {double_salary_total:.0f} 元")
        if owed_wages > 0:
            parts.append(f"拖欠工资 {owed_wages:.0f} 元")
        if deposit > 0:
            parts.append(f"押金退还 {deposit:.0f} 元")
        calculation_lines.append(
            f"以上各项合计可主张总额约 {claim_total:.0f} 元（{' + '.join(parts)}）。"
        )

    if any(token in merged for token in ("劳动关系", "入职", "考勤", "工资流水", "发工资")):
        calculation_lines.append("现有事实已经足以先从劳动关系成立、工资支付事实和未签约责任三个层面同时主张权利。")

    if any(token in merged for token in ("查封", "保全", "资产", "新公司", "人格混同", "关联公司", "主体变更", "更名", "注销", "换皮", "关门", "搬走", "换了个名")):
        strategy_lines.append("如果目标是查封新公司或关联主体，现阶段不要先卡在\u201c证据够不够\u201d这个问题上，而是先按人格混同、责任承继、实际用工主体三个方向布置材料。")
        strategy_lines.append("下一步应优先准备：工商档案、股东和法定代表人信息、工资发放账户、社保公积金缴纳主体、办公地点、业务连续性、聊天记录和财务往来材料。")

    if not strategy_lines:
        strategy_lines.append("高风险诉求可以先拆成两步走：先固定债权和责任主体，再根据对方资产和主体结构决定是否申请保全、追加被申请人或单独起诉。")

    if has_unsigned_contract:
        action_lines.append("先把入职时间、工资标准、考勤记录、工资流水、聊天记录整理成时间轴，这些足以支撑第一轮仲裁请求。")
    if any(token in merged for token in ("拖欠工资", "欠薪", "未发工资", "克扣工资", "拖欠")):
        action_lines.append("欠薪部分建议同步准备工资条、银行流水、催要记录和考勤，便于把工资、经济补偿或赔偿一并主张。")
    if any(token in merged for token in ("辞退", "开除", "解除劳动关系", "逼离职", "被迫离职")):
        action_lines.append("如果还涉及辞退或被迫离职，解除通知、谈话录音、岗位调整记录和绩效材料要单独归档。")
    if any(token in merged for token in ("新公司", "注销", "关联公司", "查封", "保全", "换皮", "关门", "搬走")):
        action_lines.append("如果担心对方转移资产，可在仲裁或起诉前同步梳理保全财产线索，而不是等全部证据齐全后再行动。")
    if not action_lines:
        action_lines.append("先把可用证据按\u201c身份关系、金额、时间、对方主体\u201d四类归档，后续维权路径就能快速落地。")

    needs_core_followup = False
    if has_unsigned_contract and (amount is None or months is None):
        needs_core_followup = True
    elif not (case_summary or "").strip():
        needs_core_followup = True

    if unknowns:
        sandwich_followup = (
            "你现在已经提供了有法律价值的关键事实，这些信息足以先帮你判断维权方向。"
            "按目前情况看，权利主张是有空间的，不需要因为材料还没凑齐就停在原地。"
            f"为了把金额和请求做得更准，我还需要你补 1 到 2 个核心信息：{'、'.join(unknowns[:2])}。"
        )
    else:
        sandwich_followup = (
            "你现有的信息已经能支持初步维权判断了。"
            "按现在的案情，主张路径已经比较清楚。"
            "如果你愿意，我接下来更建议直接帮你整理仲裁请求清单、证据目录，或者起草一份劳动仲裁申请书草稿。"
        )

    constructive_next_step = (
        "目前账目和维权方向已经基本清楚。接下来你可以选择先去劳动监察投诉，或者直接申请劳动仲裁。"
        "如果你愿意，我下一步可以直接帮你起草《劳动仲裁申请书》草稿，或者把证据目录整理成可提交版本。"
    )

    answer_parts: list[str] = []
    if facts:
        answer_parts.append("已知事实：\n- " + "\n- ".join(facts))
    if calculation_lines:
        answer_parts.append("算账与确权层：\n- " + "\n- ".join(calculation_lines))
    else:
        answer_parts.append("算账与确权层：\n- 现有信息虽然还不完整，但已经足以先明确可主张的权利方向，不需要整体停住。")
    answer_parts.append("高风险执行层：\n- " + "\n- ".join(strategy_lines))
    answer_parts.append("下一步落地动作：\n- " + "\n- ".join(action_lines))
    if needs_core_followup:
        answer_parts.append("柔性追问：\n- " + sandwich_followup)
    else:
        answer_parts.append("建设性推进：\n- " + constructive_next_step)

    return {
        "answer": "\n\n".join(answer_parts),
        "needs_core_followup": needs_core_followup,
        "followup": sandwich_followup,
        "constructive_next_step": constructive_next_step,
        "unknowns": unknowns[:2],
    }


# ======================================================================
# Module-level singleton
# ======================================================================

legal_agent = LegalGraphAgent()
