from __future__ import annotations

from app.services.memory_resolver import build_subject_reference_response_text


def _message_understanding_with_scope(scope_order: list[str] | None = None) -> dict[str, object]:
    return {
        "intent": "subject_reference",
        "route_hint": "DOSSIER_MEMORY",
        "subject_reference": {
            "enabled": True,
            "target_mention": "公司",
            "mention": "公司",
            "target_type": "organization",
            "reference_type": "organization",
            "scope_order": scope_order
            or [
                "current_conversation_graph",
                "current_conversation_messages",
                "dossier_confirmed_participants",
                "dossier_display_graph",
                "dossier_cross_conversation_candidates",
            ],
            "needs_clarification_if_ambiguous": True,
        },
    }


def test_subject_reference_prefers_current_conversation_graph_scope() -> None:
    text, confidence, trace = build_subject_reference_response_text(
        question="这里的公司指的是哪家公司？",
        message_understanding=_message_understanding_with_scope(),
        dossier_context={
            "participant_mentions": [],
            "confirmed_participants": [],
        },
        dossier_graph_context={
            "graph_nodes": [
                {
                    "id": "company-b",
                    "conversation_id": 9,
                    "display_label": "B公司",
                    "raw_label": "B公司",
                    "type": "organization",
                },
                {
                    "id": "company-a",
                    "conversation_id": 10,
                    "display_label": "A公司",
                    "raw_label": "A公司",
                    "type": "organization",
                },
            ],
            "participant_mentions": [],
            "participants": [],
            "matched_nodes": [],
        },
        recent_messages=[{"role": "user", "content": "我和B公司签了劳动合同。"}],
        current_conversation_id=9,
    )

    assert "当前会话" in text
    assert "B公司" in text
    assert trace["selected_scope"] == "current_conversation_graph"
    assert trace["candidate_count_by_scope"]["current_conversation_graph"] >= 1
    assert trace["escalated_to_dossier"] is False
    assert trace["used_evidence"]
    assert confidence >= 0.8


def test_subject_reference_escalates_to_dossier_when_current_conversation_is_empty() -> None:
    text, confidence, trace = build_subject_reference_response_text(
        question="这里的公司指的是哪家公司？",
        message_understanding=_message_understanding_with_scope(),
        dossier_context={"participant_mentions": [], "confirmed_participants": []},
        dossier_graph_context={
            "graph_nodes": [
                {
                    "id": "company-b",
                    "conversation_id": 10,
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

    assert "当前会话里还没有直接确认" in text
    assert "B公司" in text
    assert "需要你确认" in text
    assert trace["selected_scope"] in {
        "dossier_display_graph",
        "dossier_cross_conversation_candidates",
    }
    assert trace["escalated_to_dossier"] is True
    assert trace["unresolved_reason"] is None
    assert confidence < 0.8


def test_subject_reference_returns_clarification_when_unresolved() -> None:
    text, confidence, trace = build_subject_reference_response_text(
        question="这里的公司指的是哪家公司？",
        message_understanding=_message_understanding_with_scope(),
        dossier_context={"participant_mentions": [], "confirmed_participants": []},
        dossier_graph_context={
            "graph_nodes": [],
            "participant_mentions": [],
            "participants": [],
            "matched_nodes": [],
        },
        recent_messages=[],
        current_conversation_id=9,
    )

    assert "当前还不能稳定确定" in text
    assert trace["selected_scope"] == ""
    assert trace["escalated_to_dossier"] is False
    assert trace["unresolved_reason"] == "no_matching_subject_found"
    assert trace["pending_clarification"]["type"] == "subject_reference_resolution"
    assert trace["pending_clarification"]["target_mention"] == "公司"
    assert confidence < 0.5
