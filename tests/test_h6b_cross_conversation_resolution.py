from __future__ import annotations

from typing import Any

from app.models import DossierParticipant, DossierParticipantMention
from app.services.conversation_store import build_dossier_participant_candidates


def _assert_no_internal_node_reference_keys(payload: Any) -> None:
    if isinstance(payload, dict):
        assert "node_reference_keys" not in payload
        for value in payload.values():
            _assert_no_internal_node_reference_keys(value)
        return
    if isinstance(payload, list):
        for item in payload:
            _assert_no_internal_node_reference_keys(item)


def _find_cross_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        candidate
        for candidate in candidates
        if candidate.get("task_type") == "cross_conversation_entity_resolution"
    ]


def test_cross_conversation_exact_label_generates_high_confidence_candidate() -> None:
    candidates = build_dossier_participant_candidates(
        graph_payload={
            "graph": {
                "nodes": [
                    {"id": "conv1-a", "label": "A公司", "type": "organization", "conversation_id": 11},
                    {"id": "conv2-a", "label": "A公司", "type": "organization", "conversation_id": 12},
                ]
            }
        },
        participants=[],
        confirmed_mentions=[],
        participant_aliases={},
    )

    cross_candidate = next(
        candidate
        for candidate in _find_cross_candidates(candidates)
        if candidate["source_node"]["graph_node_label"] == "A公司"
        and candidate["target_node"]["graph_node_label"] == "A公司"
    )

    assert cross_candidate["confidence"] == "high"
    assert cross_candidate["source_node"]["conversation_id"] != cross_candidate["target_node"]["conversation_id"]
    assert cross_candidate["source_node"]["graph_node_id"] == "conv1-a"
    assert cross_candidate["target_node"]["graph_node_id"] == "conv2-a"
    _assert_no_internal_node_reference_keys(candidates)


def test_cross_conversation_specific_generic_candidate_recommends_existing_participant() -> None:
    participant = DossierParticipant(
        id=7,
        dossier_id=5,
        user_id=9,
        label="A公司",
        role="用人单位",
        party_side="opponent",
        entity_type="organization",
        confirmed_by_user=True,
    )
    confirmed_mention = DossierParticipantMention(
        id=101,
        dossier_id=5,
        participant_id=7,
        conversation_id=11,
        raw_label="A公司",
        source="graph_node",
        graph_node_id="conv1-a",
        graph_node_label="A公司",
        graph_node_type="organization",
    )

    candidates = build_dossier_participant_candidates(
        graph_payload={
            "graph": {
                "nodes": [
                    {"id": "conv1-a", "label": "A公司", "type": "organization", "conversation_id": 11},
                    {"id": "conv2-g", "label": "公司", "type": "organization", "conversation_id": 12},
                ]
            }
        },
        participants=[participant],
        confirmed_mentions=[confirmed_mention],
        participant_aliases={},
    )

    cross_candidate = next(
        candidate
        for candidate in _find_cross_candidates(candidates)
        if candidate["recommended_participant_id"] == 7
    )

    assert cross_candidate["confidence"] == "medium"
    assert cross_candidate["label_kind"] == "mixed"
    assert cross_candidate["can_link_existing"] is True
    assert cross_candidate["can_create_new"] is False
    assert cross_candidate["suggested_label"] == "A公司"
    assert cross_candidate["source_node"]["graph_node_label"] == "A公司"
    assert cross_candidate["target_node"]["graph_node_label"] == "公司"
    assert cross_candidate["target_node"]["raw_label"] == "公司"
    assert cross_candidate["match_candidates"][0]["participant_id"] == 7
    _assert_no_internal_node_reference_keys(candidates)


def test_cross_conversation_does_not_pair_person_with_organization() -> None:
    candidates = build_dossier_participant_candidates(
        graph_payload={
            "graph": {
                "nodes": [
                    {"id": "p1", "label": "王经理", "type": "person", "conversation_id": 11},
                    {"id": "o1", "label": "A公司", "type": "organization", "conversation_id": 12},
                ]
            }
        },
        participants=[],
        confirmed_mentions=[],
        participant_aliases={},
    )

    assert _find_cross_candidates(candidates) == []
    _assert_no_internal_node_reference_keys(candidates)


def test_cross_conversation_candidates_are_not_generated_within_same_conversation() -> None:
    candidates = build_dossier_participant_candidates(
        graph_payload={
            "graph": {
                "nodes": [
                    {"id": "same-1", "label": "A公司", "type": "organization", "conversation_id": 11},
                    {"id": "same-2", "label": "公司", "type": "organization", "conversation_id": 11},
                ]
            }
        },
        participants=[],
        confirmed_mentions=[],
        participant_aliases={},
    )

    assert _find_cross_candidates(candidates) == []
    _assert_no_internal_node_reference_keys(candidates)


def test_raw_label_candidates_remain_compatible_with_cross_conversation_extension() -> None:
    candidates = build_dossier_participant_candidates(
        graph_payload={
            "graph": {
                "nodes": [
                    {"id": "n1", "label": "公司", "type": "organization", "conversation_id": 11},
                ]
            }
        },
        participants=[],
        confirmed_mentions=[],
        participant_aliases={},
    )

    candidate = candidates[0]
    assert candidate["task_type"] == "raw_label_resolution"
    for key in (
        "candidate_key",
        "task_key",
        "raw_label",
        "suggested_label",
        "label_kind",
        "reason",
        "mentions",
        "match_candidates",
        "existing_participant_id",
        "recommended_participant_id",
        "can_create_new",
        "can_link_existing",
    ):
        assert key in candidate
    assert candidate["can_mark_non_entity"] is True
    assert candidate["can_create_new"] is False
    _assert_no_internal_node_reference_keys(candidates)


def test_confirmed_mentions_with_graph_node_id_do_not_auto_confirm_same_label_sibling_node() -> None:
    participant = DossierParticipant(
        id=7,
        dossier_id=5,
        user_id=9,
        label="A公司",
        role="用人单位",
        party_side="opponent",
        entity_type="organization",
        confirmed_by_user=True,
    )
    confirmed_mention = DossierParticipantMention(
        id=102,
        dossier_id=5,
        participant_id=7,
        conversation_id=11,
        raw_label="A公司",
        source="graph_node",
        graph_node_id="node-1",
        graph_node_label="A公司",
        graph_node_type="organization",
    )

    candidates = build_dossier_participant_candidates(
        graph_payload={
            "graph": {
                "nodes": [
                    {"id": "node-1", "label": "A公司", "type": "organization", "conversation_id": 11},
                    {"id": "node-2", "label": "A公司", "type": "organization", "conversation_id": 11},
                ]
            }
        },
        participants=[participant],
        confirmed_mentions=[confirmed_mention],
        participant_aliases={},
    )

    raw_candidate = next(
        candidate
        for candidate in candidates
        if candidate["task_type"] == "raw_label_resolution" and candidate["raw_label"] == "A公司"
    )
    assert [mention["graph_node_id"] for mention in raw_candidate["mentions"]] == ["node-2"]
    _assert_no_internal_node_reference_keys(candidates)


def test_legacy_confirmed_mentions_without_graph_node_id_still_suppress_same_label_candidates() -> None:
    participant = DossierParticipant(
        id=7,
        dossier_id=5,
        user_id=9,
        label="A公司",
        role="用人单位",
        party_side="opponent",
        entity_type="organization",
        confirmed_by_user=True,
    )
    confirmed_mention = DossierParticipantMention(
        id=103,
        dossier_id=5,
        participant_id=7,
        conversation_id=11,
        raw_label="A公司",
        source="graph",
        graph_node_id=None,
        graph_node_label=None,
        graph_node_type=None,
    )

    candidates = build_dossier_participant_candidates(
        graph_payload={
            "graph": {
                "nodes": [
                    {"id": "node-2", "label": "A公司", "type": "organization", "conversation_id": 11},
                ]
            }
        },
        participants=[participant],
        confirmed_mentions=[confirmed_mention],
        participant_aliases={},
    )

    assert candidates == []
