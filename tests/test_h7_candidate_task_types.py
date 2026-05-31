from __future__ import annotations

from app.services.conversation_store import build_dossier_participant_candidates


def _find_candidate(candidates: list[dict], raw_label: str) -> dict:
    return next(candidate for candidate in candidates if candidate.get("raw_label") == raw_label)


def test_candidate_task_type_identity_remains_cross_conversation_primary_task() -> None:
    candidates = build_dossier_participant_candidates(
        graph_payload={
            "graph": {
                "nodes": [
                    {"id": "conv1-company", "label": "A公司", "type": "organization", "conversation_id": 11},
                    {"id": "conv2-company", "label": "A公司", "type": "organization", "conversation_id": 12},
                ]
            }
        },
        participants=[],
        confirmed_mentions=[],
        participant_aliases={},
    )

    identity_candidate = next(candidate for candidate in candidates if candidate.get("task_type") == "identity")
    assert identity_candidate["confidence"] == "high"
    assert identity_candidate["source_node"]["conversation_id"] != identity_candidate["target_node"]["conversation_id"]


def test_candidate_task_type_party_naming_for_self_anchor_label() -> None:
    candidates = build_dossier_participant_candidates(
        graph_payload={
            "graph": {
                "nodes": [
                    {"id": "self-1", "label": "我", "type": "person", "conversation_id": 11},
                ]
            }
        },
        participants=[],
        confirmed_mentions=[],
        participant_aliases={},
    )

    candidate = _find_candidate(candidates, "我")
    assert candidate["task_type"] == "party_naming"
    assert candidate["can_create_new"] is False
    assert candidate["can_link_existing"] is False


def test_candidate_task_type_affiliation_relation_for_explicit_role_org_label() -> None:
    candidates = build_dossier_participant_candidates(
        graph_payload={
            "graph": {
                "nodes": [
                    {"id": "hr-1", "label": "B公司的人事", "type": "person", "conversation_id": 11},
                ]
            }
        },
        participants=[],
        confirmed_mentions=[],
        participant_aliases={},
    )

    candidate = _find_candidate(candidates, "B公司的人事")
    assert candidate["task_type"] == "affiliation_relation"
    assert candidate["can_create_new"] is False
    assert candidate["can_link_existing"] is False


def test_candidate_task_type_affiliation_relation_uses_single_conversation_org_hint() -> None:
    candidates = build_dossier_participant_candidates(
        graph_payload={
            "graph": {
                "nodes": [
                    {"id": "mgr-1", "label": "王经理", "type": "person", "conversation_id": 11},
                    {"id": "company-1", "label": "B公司", "type": "organization", "conversation_id": 11},
                ]
            }
        },
        participants=[],
        confirmed_mentions=[],
        participant_aliases={},
    )

    candidate = _find_candidate(candidates, "王经理")
    assert candidate["task_type"] == "affiliation_relation"
    assert candidate["related_organization_labels"] == ["B公司"]


def test_candidate_task_type_relation_only_for_role_without_stable_org_hint() -> None:
    candidates = build_dossier_participant_candidates(
        graph_payload={
            "graph": {
                "nodes": [
                    {"id": "mgr-1", "label": "王经理", "type": "person", "conversation_id": 11},
                    {"id": "worker-1", "label": "张三", "type": "person", "conversation_id": 11},
                ]
            }
        },
        participants=[],
        confirmed_mentions=[],
        participant_aliases={},
    )

    candidate = _find_candidate(candidates, "王经理")
    assert candidate["task_type"] == "relation_only"
    assert candidate["related_organization_labels"] == []


def test_candidate_task_type_not_actionable_for_fact_object_node() -> None:
    candidates = build_dossier_participant_candidates(
        graph_payload={
            "graph": {
                "nodes": [
                    {"id": "doc-1", "label": "确认书", "type": "document", "conversation_id": 11},
                ]
            }
        },
        participants=[],
        confirmed_mentions=[],
        participant_aliases={},
    )

    candidate = _find_candidate(candidates, "确认书")
    assert candidate["task_type"] == "not_actionable"
    assert candidate["can_create_new"] is False


def test_candidate_task_type_raw_label_resolution_remains_for_generic_entity_label() -> None:
    candidates = build_dossier_participant_candidates(
        graph_payload={
            "graph": {
                "nodes": [
                    {"id": "org-1", "label": "公司", "type": "organization", "conversation_id": 11},
                ]
            }
        },
        participants=[],
        confirmed_mentions=[],
        participant_aliases={},
    )

    candidate = _find_candidate(candidates, "公司")
    assert candidate["task_type"] == "raw_label_resolution"
    assert candidate["can_mark_non_entity"] is True
