"""
Legal Graph Agent — thin orchestrator.

This module defines the LegalGraphAgent class and wires up all node
implementations from ``app.agent.nodes.*``. The class itself contains
only LLM initialization, thin node wrappers, and the ``ask()`` entry point.

All business logic lives in the domain node modules:
  - app.agent.nodes.router      — four-track routing
  - app.agent.nodes.fact_updater — case summary merging
  - app.agent.nodes.researcher   — RAG retrieval + graph memory
  - app.agent.nodes.reflector    — evidence sufficiency analysis
  - app.agent.nodes.advisor      — layered legal analysis + LLM polishing
  - app.agent.nodes.clarifier    — sandwich-style follow-up questions
  - app.agent.nodes._llm         — shared LLM utilities
"""
from __future__ import annotations

import logging
from typing import Any

from app.agent.graph import AgentState, build_legal_agent_graph
from app.core.llm_factory import (
    create_expert_llm,
    create_summary_llm,
)

# --- Import node implementations ---
from app.agent.nodes._llm import (
    GREETING_RESPONSE,
    OUT_OF_SCOPE_RESPONSE,
    GRAPH_TOOL,
    create_cloud_llm,
    create_local_router_llm,
    chat_model,
    chat_model_with_fallback,
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
    detect_out_of_scope_reason,
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

_CASE_TYPE_RULES: list[tuple[str, list[str]]] = [
    ("劳动争议", ["劳动合同", "工资", "解雇", "辞退", "工伤", "社保", "加班", "竞业", "未签合同", "经济补偿"]),
    ("刑事", ["犯罪", "刑事", "判刑", "拘留", "逮捕", "正当防卫", "醉驾", "盗窃", "诈骗", "故意伤害"]),
    ("民事侵权", ["侵权", "名誉", "隐私", "人身损害", "交通事故", "医疗事故"]),
    ("合同纠纷", ["合同", "违约", "解约", "退款", "定金", "买卖"]),
    ("婚姻家庭", ["离婚", "抚养", "赡养", "财产分割", "家暴"]),
    ("知识产权", ["版权", "专利", "商标", "著作权"]),
    ("行政", ["行政", "公安", "城管", "拆迁", "征地"]),
    ("环境公益", ["环境", "污染", "公益诉讼"]),
]


def _infer_case_type(case_summary: str, question: str) -> str | None:
    """基于关键词匹配推断案件类型。"""
    merged = f"{case_summary} {question}"
    best_type = None
    best_score = 0
    for case_type, keywords in _CASE_TYPE_RULES:
        score = sum(1 for kw in keywords if kw in merged)
        if score > best_score:
            best_score = score
            best_type = case_type
    return best_type if best_score > 0 else None


class LegalGraphAgent:
    def __init__(self):
        self.router_llm = create_local_router_llm()
        self._router_llm_fallback = create_cloud_llm(model="glm-4-flash")
        self.expert_llm = create_expert_llm()
        self.summary_llm = create_summary_llm()
        self.graph = build_legal_agent_graph(self)

    # === Shared LLM utilities (delegated to _llm module) ===
    _chat_model = chat_model
    _chat_model_with_fallback = chat_model_with_fallback
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
    _detect_out_of_scope_reason = detect_out_of_scope_reason
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

    async def out_of_scope_node(self, state: AgentState) -> AgentState:
        return {
            "response": OUT_OF_SCOPE_RESPONSE,
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
        saved_case_state: dict | None = None,
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
            "saved_case_state": saved_case_state,
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
        # Surgery #13 + fix: 序列化 CaseState 快照
        _case_state_snapshot = None
        _case_type = None
        try:
            from app.schemas.case_state import CaseState

            _final_summary = final_state.get("case_summary") or case_summary or ""
            _final_question = question
            _final_graph = final_state.get("graph_context") or graph_memory or ""
            # 构建 CaseState：优先用 case_summary，fallback 用 question
            _text_for_extraction = _final_summary.strip() or _final_question.strip()
            if _text_for_extraction:
                _cs = CaseState.from_text(
                    _final_summary if _final_summary.strip() else "",
                    _final_question,
                    _final_graph,
                )
                # Bug B fix: 用 saved_case_state 补全 from_text 未能提取的字段
                if saved_case_state and isinstance(saved_case_state, dict):
                    if _cs.monthly_salary is None and saved_case_state.get("monthly_salary") is not None:
                        _cs.monthly_salary = saved_case_state["monthly_salary"]
                    # Fix 2: 从 fact_updater 检测到的月薪填充（优先级低于 saved_case_state）
                    if _cs.monthly_salary is None:
                        _detected = final_state.get("_detected_monthly_wage")
                        if isinstance(_detected, (int, float)) and _detected > 0:
                            _cs.monthly_salary = float(_detected)
                    if _cs.work_months is None and saved_case_state.get("work_months") is not None:
                        _cs.work_months = saved_case_state["work_months"]
                    if not _cs.has_unsigned_contract and saved_case_state.get("has_unsigned_contract"):
                        _cs.has_unsigned_contract = True
                    if _cs.deposit == 0.0 and saved_case_state.get("deposit", 0) > 0:
                        _cs.deposit = saved_case_state["deposit"]
                    # 触发 Pydantic 重新计算派生字段
                    _cs = _cs.model_validate(_cs.model_dump())
                _case_state_snapshot = _cs.model_dump()
                # 推断案件类型（合并 summary + question）
                _case_type = _infer_case_type(
                    _final_summary or _final_question,
                    _final_question,
                )
        except Exception:
            pass

        return {
            "response": final_state.get("response") or "未找到依据。",
            "sources": final_state.get("sources") or [],
            "is_final": bool(final_state.get("is_final", False)),
            "confidence_score": float(final_state.get("confidence_score", 0.0)),
            "missing_info": final_state.get("missing_info") or [],
            "route_intent": str(final_state.get("route_intent") or "GENERAL_CONSULT").upper(),
            "case_summary": final_state.get("case_summary") or case_summary or "",
            "case_state_snapshot": _case_state_snapshot,
            "case_type": _case_type,
        }


# ======================================================================
# Module-level singleton
# ======================================================================

legal_agent = LegalGraphAgent()
