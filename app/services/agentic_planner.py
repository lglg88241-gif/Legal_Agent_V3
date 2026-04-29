from __future__ import annotations

import re
from typing import Any

from app.services.tool_registry import get_tool_contract


_EXPLICIT_LAW_ARTICLE_PATTERNS = (
    re.compile(r"(劳动合同法|民法典|刑法|社会保险法)[^。！？\n]{0,24}第[一二三四五六七八九十百千万\d]+条"),
    re.compile(r"第[一二三四五六七八九十百千万\d]+条[^。！？\n]{0,12}(规定了什么|怎么规定|是什么意思|内容是什么)"),
)


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _looks_like_explicit_law_article_question(question: str) -> bool:
    normalized = _normalize_text(question)
    if not normalized:
        return False
    return any(pattern.search(normalized) for pattern in _EXPLICIT_LAW_ARTICLE_PATTERNS)


def _tool_call(
    tool_name: str,
    *,
    arguments: dict[str, Any],
    reason: str,
    dispatch_mode: str = "planner_direct",
) -> dict[str, Any]:
    return {
        "tool_name": tool_name,
        "arguments": arguments,
        "reason": _normalize_text(reason),
        "dispatch_mode": dispatch_mode,
        "contract": get_tool_contract(tool_name),
    }


def _write_request(name: str, *, requested: bool, input_text: str, reason: str) -> dict[str, Any]:
    return {
        "name": name,
        "requested": bool(requested),
        "input_text": _normalize_text(input_text),
        "reason": _normalize_text(reason),
    }


def build_agentic_plan(
    *,
    question: str,
    message_understanding: dict[str, Any] | None,
    execution_plan: dict[str, Any] | None,
    conversation_id: int,
    dossier_id: int | None,
    persistence_consent: str | None,
    conversation_mode: str | None,
) -> dict[str, Any]:
    del conversation_mode

    mu = message_understanding if isinstance(message_understanding, dict) else {}
    plan = execution_plan if isinstance(execution_plan, dict) else {}
    intent = _normalize_text(mu.get("intent")).lower() or "unknown"
    route = _normalize_text(plan.get("primary_route") or mu.get("route_hint")).upper() or "UNKNOWN"
    response_mode = _normalize_text(plan.get("response_mode")).lower() or "legal_answer"
    normalized_question = _normalize_text(question)

    agentic_plan: dict[str, Any] = {
        "planner_version": 1,
        "intent": intent,
        "route": route,
        "response_style": response_mode,
        "specialist": "legacy_fallback",
        "plan_summary": "本轮仍走旧链路，暂未形成可执行的 Agent 计划。",
        "tool_calls": [],
        "writes_requested": [],
    }

    if intent in {"subject_reference", "clarification_response"} and route == "DOSSIER_MEMORY":
        tool_calls = [
            _tool_call(
                "current_conversation_graph_query",
                arguments={"conversation_id": conversation_id},
                reason="优先查询当前会话图谱，先在本会话里解析主体指代。",
            ),
        ]
        if dossier_id:
            tool_calls.append(
                _tool_call(
                    "dossier_graph_context_query",
                    arguments={
                        "conversation_id": conversation_id,
                        "dossier_id": dossier_id,
                        "query": normalized_question,
                    },
                    reason="如果当前会话信息不足，再补充案卷范围的图谱与主体线索。",
                )
            )
        tool_calls.append(
            _tool_call(
                "subject_reference_resolve",
                arguments={
                    "question": normalized_question,
                    "current_conversation_id": conversation_id,
                },
                reason="结合当前会话图谱、最近消息与案卷线索生成指代解析结论。",
            )
        )
        agentic_plan.update(
            specialist="dossier_memory_agent",
            response_style="memory_answer",
            plan_summary="这是主体指代问题。先查当前会话图谱，再按 scope_order 逐级升级到案卷记忆。",
            tool_calls=tool_calls,
            writes_requested=[],
        )
        return agentic_plan

    if intent == "fact_update" and route == "CASE_UPDATE":
        writes_requested = [
            _write_request(
                "structured_case_persistence",
                requested=True,
                input_text=normalized_question,
                reason="当前消息是新的案情事实，应进入结构化案情。",
            ),
            _write_request(
                "graph_update_schedule",
                requested=True,
                input_text=normalized_question,
                reason="当前消息包含新的主体、事件或关系线索，应作为图谱更新输入。",
            ),
        ]
        if _normalize_text(persistence_consent).lower() == "temporary":
            agentic_plan.update(
                specialist="case_update_agent",
                response_style="case_analysis_without_persistence",
                plan_summary="这是事实补充，但当前会话为 temporary 模式。本轮只做法律分析，不写结构化案情，也不更新图谱。",
                tool_calls=[
                    _tool_call(
                        "legal_answer_compose",
                        arguments={"question": normalized_question},
                        reason="基于当前事实补充生成本轮法律分析。",
                        dispatch_mode="specialist_owned",
                    )
                ],
                writes_requested=writes_requested,
            )
            return agentic_plan

        agentic_plan.update(
            specialist="case_update_agent",
            response_style="acknowledge_and_persist",
            plan_summary="这是新的案情事实。记录结构化案情并计划更新当前会话图谱，然后返回轻量确认。",
            tool_calls=[
                _tool_call(
                    "acknowledgement_compose",
                    arguments={"intent": "fact_update"},
                    reason="生成面向用户的事实补充确认文本。",
                    dispatch_mode="specialist_owned",
                )
            ],
            writes_requested=writes_requested,
        )
        return agentic_plan

    if intent == "confirm_previous_fact" and route == "CASE_UPDATE":
        resolved_inputs = plan.get("resolved_inputs") if isinstance(plan.get("resolved_inputs"), dict) else {}
        previous_fact_text = _normalize_text(resolved_inputs.get("previous_fact_text"))
        if previous_fact_text:
            writes_requested = [
                _write_request(
                    "structured_case_persistence",
                    requested=True,
                    input_text=previous_fact_text,
                    reason="用户确认了上一条事实，应使用被确认的事实文本做结构化更新。",
                ),
                _write_request(
                    "graph_update_schedule",
                    requested=True,
                    input_text=previous_fact_text,
                    reason="图谱更新必须使用被确认的上一条事实文本，而不是确认短句本身。",
                ),
            ]
            agentic_plan.update(
                specialist="case_update_agent",
                response_style="acknowledge_and_persist",
                plan_summary="这是上一条事实确认。绑定最近一条可确认事实，并以该事实文本执行持久化和图谱更新。",
                tool_calls=[
                    _tool_call(
                        "acknowledgement_compose",
                        arguments={"intent": "confirm_previous_fact"},
                        reason="生成上一条事实确认后的用户确认文本。",
                        dispatch_mode="specialist_owned",
                    )
                ],
                writes_requested=writes_requested,
            )
            return agentic_plan

        agentic_plan.update(
            specialist="case_update_agent",
            response_style="clarification",
            plan_summary="用户在确认上一条事实，但当前无法定位可确认的事实文本。本轮只返回澄清，不执行写入。",
            tool_calls=[
                _tool_call(
                    "acknowledgement_compose",
                    arguments={"intent": "confirm_previous_fact"},
                    reason="生成缺少上一条事实时的保守澄清回复。",
                    dispatch_mode="specialist_owned",
                )
            ],
            writes_requested=[
                _write_request(
                    "structured_case_persistence",
                    requested=True,
                    input_text="",
                    reason="如果找不到上一条事实，必须阻断结构化写入。",
                ),
                _write_request(
                    "graph_update_schedule",
                    requested=True,
                    input_text="",
                    reason="如果找不到上一条事实，必须阻断图谱更新。",
                ),
            ],
        )
        return agentic_plan

    if intent == "legal_question" and route == "LEGAL_QA":
        explicit_article = _looks_like_explicit_law_article_question(normalized_question)
        lookup_tool = "exact_law_lookup" if explicit_article else "legal_rag_search"
        lookup_reason = (
            "这是明确法条问题，优先做精确法条查询。"
            if explicit_article
            else "这是法律规则问题，先做法律检索。"
        )
        agentic_plan.update(
            specialist="legal_qa_agent",
            response_style="legal_answer",
            plan_summary="这是法律问答。先完成法律检索，再交给法律问答专家生成解释，不写案情也不更新图谱。",
            tool_calls=[
                _tool_call(
                    lookup_tool,
                    arguments={"query": normalized_question},
                    reason=lookup_reason,
                ),
                _tool_call(
                    "legal_answer_compose",
                    arguments={"question": normalized_question},
                    reason="基于检索结果生成最终法律回答。",
                    dispatch_mode="specialist_owned",
                ),
            ],
            writes_requested=[],
        )
        return agentic_plan

    if intent == "advice_question" and route in {"GENERAL_CONSULT", "LEGAL_QA"}:
        agentic_plan.update(
            specialist="legal_qa_agent",
            response_style="legal_answer",
            plan_summary="这是法律建议问题。先做法律检索，再生成维权或准备建议，不写案情也不更新图谱。",
            tool_calls=[
                _tool_call(
                    "legal_rag_search",
                    arguments={"query": normalized_question},
                    reason="先检索相关法律依据与裁判规则。",
                ),
                _tool_call(
                    "legal_answer_compose",
                    arguments={"question": normalized_question},
                    reason="基于检索依据生成最终建议。",
                    dispatch_mode="specialist_owned",
                ),
            ],
            writes_requested=[],
        )
        return agentic_plan

    return agentic_plan
