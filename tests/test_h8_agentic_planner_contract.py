from __future__ import annotations

from app.services.agentic_planner import build_agentic_plan
from app.services.agentic_request_context import build_agentic_request_context


FACT_MESSAGE = "B公司的人事通知我去签确认书，而且人事王经理口头通知我不用来了。"


def _tool_names(plan: dict[str, object]) -> list[str]:
    return [
        str(item.get("tool_name") or "")
        for item in plan.get("tool_sequence", [])
        if isinstance(item, dict)
    ]


def test_subject_reference_plan_contract_is_explicit() -> None:
    request_context = build_agentic_request_context(
        request_id="req-subject",
        user_id=7,
        conversation_id=11,
        dossier_id=21,
        message_text="这里的公司指的是哪家公司？",
        recent_messages=[],
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )
    plan = build_agentic_plan(
        question=request_context.message_text,
        message_understanding={
            "intent": "subject_reference",
            "route_hint": "DOSSIER_MEMORY",
            "needs_legal_rag": False,
            "needs_dossier_graph_context": True,
            "needs_structured_persistence": False,
        },
        execution_plan={
            "primary_route": "DOSSIER_MEMORY",
            "response_mode": "dossier_memory_answer",
        },
        conversation_id=11,
        dossier_id=21,
        persistence_consent="tracked",
        conversation_mode="TRACKED_CASE",
        request_context=request_context,
    )

    assert plan["needs_legal_rag"] is False
    assert plan["writes_requested"] == []
    assert "current_conversation_graph" in plan["context_requirements"]
    assert "pending_clarification" in plan["context_requirements"]
    assert _tool_names(plan) == [
        "current_conversation_graph_query",
        "current_conversation_messages_query",
        "subject_reference_resolve",
        "memory_answer_compose",
    ]


def test_fact_update_plan_requests_structured_persistence() -> None:
    request_context = build_agentic_request_context(
        request_id="req-fact",
        user_id=7,
        conversation_id=11,
        dossier_id=21,
        message_text=FACT_MESSAGE,
        recent_messages=[],
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )
    plan = build_agentic_plan(
        question=request_context.message_text,
        message_understanding={
            "intent": "fact_update",
            "route_hint": "CASE_UPDATE",
            "needs_legal_rag": False,
            "needs_dossier_graph_context": False,
            "needs_structured_persistence": True,
        },
        execution_plan={
            "primary_route": "CASE_UPDATE",
            "response_mode": "acknowledge_and_persist",
        },
        conversation_id=11,
        dossier_id=21,
        persistence_consent="tracked",
        conversation_mode="TRACKED_CASE",
        request_context=request_context,
    )

    write_tool_names = [item.get("tool_name") or item["name"] for item in plan["writes_requested"]]

    assert plan["needs_structured_persistence"] is True
    assert "case_summary_update" in write_tool_names
    assert "schedule_graph_update" in write_tool_names
    assert plan["final_response_strategy"] == "acknowledgement"


def test_confirm_previous_fact_plan_uses_previous_fact_text() -> None:
    request_context = build_agentic_request_context(
        request_id="req-confirm",
        user_id=7,
        conversation_id=11,
        dossier_id=21,
        message_text="我确认这个事实",
        recent_messages=[
            {"message_id": 101, "role": "user", "content": FACT_MESSAGE},
            {
                "message_id": 102,
                "role": "assistant",
                "content": "已记录。",
                "metadata": {
                    "message_understanding": {
                        "intent": "fact_update",
                        "route_hint": "CASE_UPDATE",
                    },
                    "execution_plan": {
                        "primary_route": "CASE_UPDATE",
                        "steps": ["fact_persistence", "graph_update"],
                    },
                },
            },
        ],
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )
    plan = build_agentic_plan(
        question=request_context.message_text,
        message_understanding={
            "intent": "confirm_previous_fact",
            "route_hint": "CASE_UPDATE",
            "needs_structured_persistence": True,
        },
        execution_plan={
            "primary_route": "CASE_UPDATE",
            "response_mode": "acknowledge_and_persist",
        },
        conversation_id=11,
        dossier_id=21,
        persistence_consent="tracked",
        conversation_mode="TRACKED_CASE",
        request_context=request_context,
    )

    assert plan["missing_requirements"] == []
    assert all(item["input_text"] == FACT_MESSAGE for item in plan["writes_requested"])
    assert plan["final_response_strategy"] == "acknowledgement"


def test_confirm_previous_fact_plan_blocks_writes_when_previous_fact_missing() -> None:
    request_context = build_agentic_request_context(
        request_id="req-confirm-missing",
        user_id=7,
        conversation_id=11,
        dossier_id=21,
        message_text="我确认这个事实",
        recent_messages=[],
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )
    plan = build_agentic_plan(
        question=request_context.message_text,
        message_understanding={
            "intent": "confirm_previous_fact",
            "route_hint": "CASE_UPDATE",
            "needs_structured_persistence": True,
        },
        execution_plan={
            "primary_route": "CASE_UPDATE",
            "response_mode": "acknowledge_and_persist",
        },
        conversation_id=11,
        dossier_id=21,
        persistence_consent="tracked",
        conversation_mode="TRACKED_CASE",
        request_context=request_context,
    )

    assert plan["writes_requested"] == []
    assert "previous_factual_user_message" in plan["missing_requirements"]
    assert plan["final_response_strategy"] == "ask_clarification"


def test_legal_question_plan_is_read_only() -> None:
    request_context = build_agentic_request_context(
        request_id="req-legal",
        user_id=7,
        conversation_id=11,
        dossier_id=None,
        message_text="劳动合同法第八十七条规定了什么？",
        recent_messages=[],
        conversation_mode="LEGAL_QA",
        persistence_consent="tracked",
    )
    plan = build_agentic_plan(
        question=request_context.message_text,
        message_understanding={
            "intent": "legal_question",
            "route_hint": "LEGAL_QA",
            "needs_legal_rag": True,
            "needs_structured_persistence": False,
        },
        execution_plan={
            "primary_route": "LEGAL_QA",
            "response_mode": "legal_answer",
        },
        conversation_id=11,
        dossier_id=None,
        persistence_consent="tracked",
        conversation_mode="LEGAL_QA",
        request_context=request_context,
    )

    assert plan["writes_requested"] == []
    assert plan["needs_structured_persistence"] is False
    assert plan["route"] == "LEGAL_QA"
    assert plan["final_response_strategy"] == "answer_legal_question_without_case_pollution"
    assert _tool_names(plan)[0] == "exact_law_lookup"
    assert _tool_names(plan)[-1] == "legal_answer_compose"


def test_document_request_enters_planner_contract() -> None:
    request_context = build_agentic_request_context(
        request_id="req-doc",
        user_id=7,
        conversation_id=11,
        dossier_id=21,
        message_text="帮我起草仲裁申请书。",
        recent_messages=[],
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )
    plan = build_agentic_plan(
        question=request_context.message_text,
        message_understanding={
            "intent": "document_request",
            "route_hint": "DOCUMENT_ASSIST",
            "needs_legal_rag": False,
            "needs_structured_persistence": False,
        },
        execution_plan={
            "primary_route": "DOCUMENT_ASSIST",
            "response_mode": "legacy_document_fallback",
        },
        conversation_id=11,
        dossier_id=21,
        persistence_consent="tracked",
        conversation_mode="TRACKED_CASE",
        request_context=request_context,
    )

    assert plan["intent"] == "document_request"
    assert plan["route"] == "DOCUMENT_ASSIST"
    assert plan["response_mode"] == "legacy_document_fallback"
    assert plan["specialist"] == "legacy_document_specialist"
    assert plan["fallback_allowed"] is True
