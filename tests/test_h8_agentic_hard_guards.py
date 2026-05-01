from __future__ import annotations

from app.services.agentic_hard_guards import evaluate_agentic_hard_guards
from app.services.agentic_request_context import build_agentic_request_context


FACT_MESSAGE = "B公司的人事通知我去签确认书，而且人事王经理口头通知我不用来了。"


def test_subject_reference_blocks_unexpected_writes() -> None:
    context = build_agentic_request_context(
        request_id="req-subject",
        user_id=1,
        conversation_id=9,
        message_text="这里的公司指的是哪家公司？",
        recent_messages=[],
        persistence_consent="tracked",
        conversation_mode="TRACKED_CASE",
        message_understanding={"intent": "subject_reference"},
    )
    result = evaluate_agentic_hard_guards(
        agentic_plan={
            "intent": "subject_reference",
            "route": "DOSSIER_MEMORY",
            "writes_requested": [
                {
                    "name": "structured_case_persistence",
                    "tool_name": "case_summary_update",
                    "requested": True,
                    "input_text": "这里的公司指的是哪家公司？",
                    "reason": "unexpected",
                }
            ],
        },
        request_context=context,
        message_understanding={"intent": "subject_reference"},
    )

    assert result["writes_allowed"] == []
    assert result["writes_blocked"]
    assert result["writes_blocked"][0]["blocked_by"] == ["subject_reference_blocks_writes"]


def test_legal_question_blocks_case_and_graph_writes() -> None:
    context = build_agentic_request_context(
        request_id="req-legal",
        user_id=1,
        conversation_id=9,
        message_text="劳动合同法第八十七条规定了什么？",
        recent_messages=[],
        persistence_consent="tracked",
        conversation_mode="LEGAL_QA",
        message_understanding={"intent": "legal_question", "route_hint": "LEGAL_QA"},
    )
    result = evaluate_agentic_hard_guards(
        agentic_plan={
            "intent": "legal_question",
            "route": "LEGAL_QA",
            "writes_requested": [
                {
                    "name": "structured_case_persistence",
                    "tool_name": "case_summary_update",
                    "requested": True,
                    "input_text": FACT_MESSAGE,
                    "reason": "unexpected",
                },
                {
                    "name": "graph_update_schedule",
                    "tool_name": "schedule_graph_update",
                    "requested": True,
                    "input_text": FACT_MESSAGE,
                    "reason": "unexpected",
                },
            ],
        },
        request_context=context,
        message_understanding={"intent": "legal_question", "route_hint": "LEGAL_QA"},
    )

    assert result["writes_allowed"] == []
    assert len(result["writes_blocked"]) == 2
    assert all(
        "legal_qa_blocks_case_graph_pollution" in item["blocked_by"]
        for item in result["writes_blocked"]
    )


def test_temporary_fact_update_blocks_case_summary_and_graph_update() -> None:
    context = build_agentic_request_context(
        request_id="req-temp",
        user_id=1,
        conversation_id=9,
        message_text=FACT_MESSAGE,
        recent_messages=[],
        persistence_consent="temporary",
        conversation_mode="TRACKED_CASE",
        message_understanding={"intent": "fact_update"},
    )
    result = evaluate_agentic_hard_guards(
        agentic_plan={
            "intent": "fact_update",
            "route": "CASE_UPDATE",
            "writes_requested": [
                {
                    "name": "structured_case_persistence",
                    "tool_name": "case_summary_update",
                    "requested": True,
                    "input_text": FACT_MESSAGE,
                    "reason": "persist fact",
                },
                {
                    "name": "graph_update_schedule",
                    "tool_name": "schedule_graph_update",
                    "requested": True,
                    "input_text": FACT_MESSAGE,
                    "reason": "schedule graph",
                },
            ],
        },
        request_context=context,
        message_understanding={"intent": "fact_update"},
    )

    assert result["writes_allowed"] == []
    blocked_lookup = {item["name"]: item for item in result["writes_blocked"]}
    assert "temporary_blocks_structured_persistence" in blocked_lookup["structured_case_persistence"]["blocked_by"]
    assert "temporary_blocks_graph_update" in blocked_lookup["graph_update_schedule"]["blocked_by"]


def test_tracked_fact_update_allows_case_summary_and_graph_update() -> None:
    context = build_agentic_request_context(
        request_id="req-tracked",
        user_id=1,
        conversation_id=9,
        dossier_id=21,
        message_text=FACT_MESSAGE,
        recent_messages=[],
        persistence_consent="tracked",
        conversation_mode="TRACKED_CASE",
        message_understanding={"intent": "fact_update"},
    )
    result = evaluate_agentic_hard_guards(
        agentic_plan={
            "intent": "fact_update",
            "route": "CASE_UPDATE",
            "writes_requested": [
                {
                    "name": "structured_case_persistence",
                    "tool_name": "case_summary_update",
                    "requested": True,
                    "input_text": FACT_MESSAGE,
                    "reason": "persist fact",
                },
                {
                    "name": "graph_update_schedule",
                    "tool_name": "schedule_graph_update",
                    "requested": True,
                    "input_text": FACT_MESSAGE,
                    "reason": "schedule graph",
                },
            ],
        },
        request_context=context,
        message_understanding={"intent": "fact_update"},
    )

    assert [item["name"] for item in result["writes_allowed"]] == [
        "structured_case_persistence",
        "graph_update_schedule",
    ]
    assert result["writes_blocked"] == []


def test_confirm_previous_fact_uses_previous_fact_text_and_allows_writes() -> None:
    context = build_agentic_request_context(
        request_id="req-confirm",
        user_id=1,
        conversation_id=9,
        dossier_id=21,
        message_text="我确认这个事实",
        recent_messages=[
            {"message_id": 71, "role": "user", "content": FACT_MESSAGE, "metadata": {"message_understanding": {"intent": "fact_update"}}},
            {"message_id": 72, "role": "assistant", "content": "继续补充。", "metadata": {}},
        ],
        persistence_consent="tracked",
        conversation_mode="TRACKED_CASE",
        message_understanding={
            "intent": "confirm_previous_fact",
            "previous_fact_confirmation": {"target_message_id": 71},
        },
    )
    result = evaluate_agentic_hard_guards(
        agentic_plan={
            "intent": "confirm_previous_fact",
            "route": "CASE_UPDATE",
            "writes_requested": [
                {
                    "name": "structured_case_persistence",
                    "tool_name": "case_summary_update",
                    "requested": True,
                    "input_text": FACT_MESSAGE,
                    "reason": "persist previous fact",
                },
                {
                    "name": "graph_update_schedule",
                    "tool_name": "schedule_graph_update",
                    "requested": True,
                    "input_text": FACT_MESSAGE,
                    "reason": "schedule previous fact graph update",
                },
            ],
        },
        request_context=context,
        message_understanding={
            "intent": "confirm_previous_fact",
            "previous_fact_confirmation": {"target_message_id": 71},
        },
    )

    assert result["writes_blocked"] == []
    assert all(item["input_text"] == FACT_MESSAGE for item in result["writes_allowed"])


def test_confirm_previous_fact_missing_previous_fact_blocks_synthetic_writes() -> None:
    context = build_agentic_request_context(
        request_id="req-confirm-missing",
        user_id=1,
        conversation_id=9,
        dossier_id=21,
        message_text="我确认这个事实",
        recent_messages=[],
        persistence_consent="tracked",
        conversation_mode="TRACKED_CASE",
        message_understanding={"intent": "confirm_previous_fact"},
    )
    result = evaluate_agentic_hard_guards(
        agentic_plan={
            "intent": "confirm_previous_fact",
            "route": "CASE_UPDATE",
            "writes_requested": [],
        },
        request_context=context,
        message_understanding={"intent": "confirm_previous_fact"},
        missing_previous_fact=True,
    )

    assert result["writes_allowed"] == []
    assert [item["name"] for item in result["writes_requested"]] == [
        "structured_case_persistence",
        "graph_update_schedule",
    ]
    assert all(
        "missing_previous_fact_blocks_confirm_writes" in item["blocked_by"]
        for item in result["writes_blocked"]
    )
    assert all(item["input_text"] == "" for item in result["writes_blocked"])


def test_tracked_fact_update_without_dossier_blocks_writes() -> None:
    context = build_agentic_request_context(
        request_id="req-unbound",
        user_id=1,
        conversation_id=9,
        dossier_id=None,
        message_text=FACT_MESSAGE,
        recent_messages=[],
        persistence_consent="tracked",
        conversation_mode="TRACKED_CASE",
        message_understanding={"intent": "fact_update"},
    )
    result = evaluate_agentic_hard_guards(
        agentic_plan={
            "intent": "fact_update",
            "route": "CASE_UPDATE",
            "writes_requested": [
                {
                    "name": "structured_case_persistence",
                    "tool_name": "case_summary_update",
                    "requested": True,
                    "input_text": FACT_MESSAGE,
                    "reason": "persist fact",
                },
                {
                    "name": "graph_update_schedule",
                    "tool_name": "schedule_graph_update",
                    "requested": True,
                    "input_text": FACT_MESSAGE,
                    "reason": "schedule graph",
                },
            ],
        },
        request_context=context,
        message_understanding={"intent": "fact_update"},
    )

    assert result["writes_allowed"] == []
    assert len(result["writes_blocked"]) == 2
    assert all(
        "tracked_writes_require_bound_dossier" in item["blocked_by"]
        for item in result["writes_blocked"]
    )

def test_confirm_previous_fact_without_dossier_blocks_writes() -> None:
    context = build_agentic_request_context(
        request_id="req-confirm-unbound",
        user_id=1,
        conversation_id=9,
        dossier_id=None,
        message_text="我确认这个事实",
        recent_messages=[
            {"message_id": 71, "role": "user", "content": FACT_MESSAGE, "metadata": {"message_understanding": {"intent": "fact_update"}}},
            {"message_id": 72, "role": "assistant", "content": "继续补充。", "metadata": {}},
        ],
        persistence_consent="tracked",
        conversation_mode="TRACKED_CASE",
        message_understanding={
            "intent": "confirm_previous_fact",
            "previous_fact_confirmation": {"target_message_id": 71},
        },
    )
    result = evaluate_agentic_hard_guards(
        agentic_plan={
            "intent": "confirm_previous_fact",
            "route": "CASE_UPDATE",
            "writes_requested": [
                {
                    "name": "structured_case_persistence",
                    "tool_name": "case_summary_update",
                    "requested": True,
                    "input_text": FACT_MESSAGE,
                    "reason": "persist previous fact",
                },
                {
                    "name": "graph_update_schedule",
                    "tool_name": "schedule_graph_update",
                    "requested": True,
                    "input_text": FACT_MESSAGE,
                    "reason": "schedule previous fact graph update",
                },
            ],
        },
        request_context=context,
        message_understanding={
            "intent": "confirm_previous_fact",
            "previous_fact_confirmation": {"target_message_id": 71},
        },
    )

    assert result["writes_allowed"] == []
    assert len(result["writes_blocked"]) == 2
    assert all(
        "tracked_writes_require_bound_dossier" in item["blocked_by"]
        for item in result["writes_blocked"]
    )
