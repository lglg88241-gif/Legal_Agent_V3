from __future__ import annotations

from app.services.agentic_request_context import build_agentic_request_context


FACT_MESSAGE = "B公司的人事通知我去签确认书，而且人事王经理口头通知我不用来了。"
LEGAL_MESSAGE = "劳动合同法第八十七条规定了什么？"


def _fact_update_assistant_metadata() -> dict[str, object]:
    return {
        "message_understanding": {
            "intent": "fact_update",
            "route_hint": "CASE_UPDATE",
        },
        "execution_plan": {
            "primary_route": "CASE_UPDATE",
            "steps": ["fact_persistence", "graph_update", "assistant_acknowledgement"],
        },
        "route_intent": "CASE_UPDATE",
    }


def _legal_question_assistant_metadata() -> dict[str, object]:
    return {
        "message_understanding": {
            "intent": "legal_question",
            "route_hint": "LEGAL_QA",
        },
        "execution_plan": {
            "primary_route": "LEGAL_QA",
            "steps": [],
        },
        "route_intent": "LEGAL_QA",
    }


def test_request_context_collects_core_fields() -> None:
    context = build_agentic_request_context(
        request_id="req-1",
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
                "metadata": _fact_update_assistant_metadata(),
            },
            {
                "message_id": 103,
                "role": "assistant",
                "content": "请再说明这里的公司指哪家。",
                "metadata": {
                    "pending_clarification": {
                        "type": "subject_reference_resolution",
                        "target_mention": "公司",
                        "reference_type": "organization",
                        "previous_question": "这里的公司指的是哪家公司？",
                        "scope_attempted": ["current_conversation_graph"],
                        "expected_response": "entity_name_or_context",
                    }
                },
            },
        ],
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
        case_summary="已有案情摘要",
    )

    assert context.message_text == "我确认这个事实"
    assert context.conversation_id == 11
    assert context.dossier_id == 21
    assert len(context.recent_messages) == 3
    assert context.pending_clarification is not None
    assert context.pending_clarification["target_mention"] == "公司"
    assert context.previous_factual_user_message is not None
    assert context.previous_factual_user_message["message_id"] == 101
    assert context.previous_factual_user_message["content"] == FACT_MESSAGE


def test_request_context_prefers_target_message_id_for_previous_fact() -> None:
    context = build_agentic_request_context(
        request_id="req-2",
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
                "metadata": _fact_update_assistant_metadata(),
            },
        ],
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
        message_understanding={
            "intent": "confirm_previous_fact",
            "previous_fact_confirmation": {
                "target_message_id": 101,
            },
        },
    )

    assert context.previous_factual_user_message is not None
    assert context.previous_factual_user_message["message_id"] == 101
    assert context.previous_factual_user_message["content"] == FACT_MESSAGE


def test_request_context_uses_recent_factual_user_message_only_when_explicitly_marked() -> None:
    context = build_agentic_request_context(
        request_id="req-3",
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
                "metadata": _fact_update_assistant_metadata(),
            },
            {"message_id": 201, "role": "user", "content": LEGAL_MESSAGE},
            {
                "message_id": 202,
                "role": "assistant",
                "content": "这是法条解释。",
                "metadata": _legal_question_assistant_metadata(),
            },
        ],
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )

    assert context.previous_factual_user_message is not None
    assert context.previous_factual_user_message["message_id"] == 101
    assert context.previous_factual_user_message["content"] == FACT_MESSAGE


def test_request_context_does_not_fallback_to_last_user_message() -> None:
    context = build_agentic_request_context(
        request_id="req-4",
        user_id=7,
        conversation_id=11,
        dossier_id=21,
        message_text="我确认这个事实",
        recent_messages=[
            {"message_id": 201, "role": "user", "content": LEGAL_MESSAGE},
            {
                "message_id": 202,
                "role": "assistant",
                "content": "这是法条解释。",
                "metadata": _legal_question_assistant_metadata(),
            },
        ],
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )

    assert context.previous_factual_user_message is None
    assert context.context_summary["has_previous_factual_user_message"] is False
