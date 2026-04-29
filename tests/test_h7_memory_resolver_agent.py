from __future__ import annotations

from app.services.memory_resolver import (
    build_subject_reference_response_text,
    extract_pending_subject_reference_clarification,
    looks_like_subject_reference_clarification_response,
)


def _subject_reference_message_understanding() -> dict[str, object]:
    return {
        "intent": "subject_reference",
        "route_hint": "DOSSIER_MEMORY",
        "subject_reference": {
            "enabled": True,
            "target_mention": "公司",
            "mention": "公司",
            "target_type": "organization",
            "reference_type": "organization",
            "scope_order": [
                "current_conversation_graph",
                "current_conversation_messages",
                "dossier_confirmed_participants",
                "dossier_display_graph",
                "dossier_cross_conversation_candidates",
            ],
            "needs_clarification_if_ambiguous": True,
        },
    }


def test_extract_pending_subject_reference_clarification_from_recent_assistant_metadata() -> None:
    pending = extract_pending_subject_reference_clarification(
        [
            {"role": "user", "content": "这里的公司指的是哪家公司？"},
            {
                "role": "assistant",
                "content": "当前还不能稳定确定“公司”具体指向哪个主体。",
                "metadata": {
                    "pending_clarification": {
                        "type": "subject_reference_resolution",
                        "target_mention": "公司",
                        "reference_type": "organization",
                        "previous_question": "这里的公司指的是哪家公司？",
                        "scope_attempted": [
                            "current_conversation_graph",
                            "current_conversation_messages",
                        ],
                        "expected_response": "entity_name_or_context",
                    }
                },
            },
        ]
    )

    assert pending is not None
    assert pending["type"] == "subject_reference_resolution"
    assert pending["target_mention"] == "公司"
    assert pending["reference_type"] == "organization"
    assert pending["scope_attempted"] == [
        "current_conversation_graph",
        "current_conversation_messages",
    ]


def test_clarification_response_detection_matches_short_followup() -> None:
    pending = {
        "type": "subject_reference_resolution",
        "target_mention": "公司",
        "reference_type": "organization",
        "previous_question": "这里的公司指的是哪家公司？",
        "scope_attempted": ["current_conversation_graph"],
        "expected_response": "entity_name_or_context",
    }

    assert looks_like_subject_reference_clarification_response(
        message="是B公司",
        pending_clarification=pending,
    ) is True
    assert looks_like_subject_reference_clarification_response(
        message="我，当事人",
        pending_clarification=pending,
    ) is True
    assert looks_like_subject_reference_clarification_response(
        message="这里的公司为什么和我前面说的不一样？",
        pending_clarification=pending,
    ) is False


def test_memory_resolver_uses_current_conversation_graph_nodes_without_participant_mapping() -> None:
    text, confidence, trace = build_subject_reference_response_text(
        question="这里的公司指的是哪家公司？",
        message_understanding=_subject_reference_message_understanding(),
        dossier_context={"participant_mentions": [], "confirmed_participants": []},
        dossier_graph_context={
            "graph_nodes": [
                {
                    "id": "company-b",
                    "conversation_id": 9,
                    "display_label": "B公司",
                    "raw_label": "B公司",
                    "type": "organization",
                }
            ],
            "participant_mentions": [],
            "participants": [],
            "matched_nodes": [],
        },
        recent_messages=[],
        current_conversation_id=9,
    )

    assert "当前会话" in text
    assert "B公司" in text
    assert trace["selected_scope"] == "current_conversation_graph"
    assert trace["used_evidence"][0]["source"] == "graph_node"
    assert confidence >= 0.8


def test_memory_resolver_clarification_response_confirms_explicit_company_name() -> None:
    text, confidence, trace = build_subject_reference_response_text(
        question="是B公司",
        message_understanding=_subject_reference_message_understanding(),
        dossier_context={"participant_mentions": [], "confirmed_participants": []},
        dossier_graph_context={"graph_nodes": [], "participant_mentions": [], "participants": [], "matched_nodes": []},
        recent_messages=[
            {
                "role": "assistant",
                "content": "当前还不能稳定确定“公司”具体指向哪个主体。",
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
            }
        ],
        current_conversation_id=9,
    )

    assert text == "已理解，你这里说的“公司”指 B公司。"
    assert trace["selected_scope"] == "clarification_response"
    assert trace["unresolved_reason"] is None
    assert "pending_clarification" not in trace
    assert confidence >= 0.9


def test_memory_resolver_clarification_response_rejects_self_answer_for_company_target() -> None:
    text, confidence, trace = build_subject_reference_response_text(
        question="我，当事人",
        message_understanding=_subject_reference_message_understanding(),
        dossier_context={"participant_mentions": [], "confirmed_participants": []},
        dossier_graph_context={"graph_nodes": [], "participant_mentions": [], "participants": [], "matched_nodes": []},
        recent_messages=[
            {
                "role": "assistant",
                "content": "当前还不能稳定确定“公司”具体指向哪个主体。",
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
            }
        ],
        current_conversation_id=9,
    )

    assert "你补充的是当事人本人" in text
    assert "公司" in text
    assert trace["selected_scope"] == "clarification_response"
    assert trace["unresolved_reason"] == "clarification_type_mismatch"
    assert trace["pending_clarification"]["target_mention"] == "公司"
    assert confidence < 0.6
