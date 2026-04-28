from __future__ import annotations

from copy import deepcopy

from app.services.conversation_store import build_dossier_participant_candidates
from app.services.dossier_coordination_graph import build_dossier_coordination_graph
from app.services.dossier_display_graph import build_dossier_display_graph
from app.services.dossier_intelligence import _build_party_naming_task


def _make_raw_graph_payload(
    graph: dict,
    *,
    confirmed_participants: list[dict] | None = None,
    participant_mappings: list[dict] | None = None,
) -> dict:
    conversation_ids = sorted(
        {
            int(node.get("conversation_id") or 0)
            for node in graph.get("nodes", [])
            if isinstance(node, dict) and int(node.get("conversation_id") or 0) > 0
        }
    )
    return {
        "status": "success",
        "dossier_id": 51,
        "participant_aliases": {},
        "confirmed_participants": confirmed_participants or [],
        "participant_mappings": participant_mappings or [],
        "graph": graph,
        "generated_at": "2026-04-28T12:00:00+00:00",
        "graph_stats": {"conversation_ids": conversation_ids},
    }


def _build_cross_candidates(raw_graph_payload: dict) -> list[dict]:
    return build_dossier_participant_candidates(
        graph_payload=raw_graph_payload,
        participants=[],
        confirmed_mentions=[],
        participant_aliases={},
    )


def _find_entities(payload: dict, *, status: str) -> list[dict]:
    return [
        entity
        for entity in payload["entities"]
        if entity.get("status") == status
    ]


def _find_tasks(payload: dict, *, task_type: str) -> list[dict]:
    return [
        task
        for task in payload["tasks"]
        if task.get("type") == task_type
    ]


def test_confirmed_participant_becomes_connected_coordination_entity() -> None:
    raw_graph = {
        "nodes": [
            {"id": "self-101", "label": "当事人", "type": "person", "conversation_id": 101},
            {"id": "company-101", "label": "A公司", "type": "organization", "conversation_id": 101},
            {"id": "notice-101", "label": "解除通知", "type": "event", "conversation_id": 101},
            {"id": "company-102", "label": "A公司", "type": "organization", "conversation_id": 102},
            {"id": "agreement-102", "label": "补签协议", "type": "document", "conversation_id": 102},
        ],
        "edges": [
            {
                "id": "edge-101-1",
                "source": "self-101",
                "target": "company-101",
                "label": "签订劳动合同",
                "properties": {"conversation_id": 101},
            },
            {
                "id": "edge-101-2",
                "source": "company-101",
                "target": "notice-101",
                "label": "发出",
                "properties": {"conversation_id": 101},
            },
            {
                "id": "edge-102-1",
                "source": "company-102",
                "target": "agreement-102",
                "label": "要求补签",
                "properties": {"conversation_id": 102},
            },
        ],
    }
    confirmed_participants = [
        {"id": 12, "label": "A公司", "entity_type": "organization", "party_side": "opponent"},
    ]
    participant_mappings = [
        {
            "participant_id": 12,
            "participant_label": "A公司",
            "participant_entity_type": "organization",
            "conversation_id": 101,
            "raw_label": "A公司",
            "graph_node_id": "company-101",
            "graph_node_label": "A公司",
            "graph_node_type": "organization",
        },
        {
            "participant_id": 12,
            "participant_label": "A公司",
            "participant_entity_type": "organization",
            "conversation_id": 102,
            "raw_label": "A公司",
            "graph_node_id": "company-102",
            "graph_node_label": "A公司",
            "graph_node_type": "organization",
        },
    ]
    raw_graph_payload = _make_raw_graph_payload(
        raw_graph,
        confirmed_participants=confirmed_participants,
        participant_mappings=participant_mappings,
    )
    display_graph = build_dossier_display_graph(
        raw_graph=raw_graph_payload["graph"],
        confirmed_participants=confirmed_participants,
        participant_mappings=participant_mappings,
    )

    result = build_dossier_coordination_graph(
        raw_graph_payload=raw_graph_payload,
        display_graph=display_graph,
        participant_candidates=[],
        intelligence_payload={"confirmation_tasks": []},
        bound_conversation_ids=[101, 102],
    )

    confirmed_entities = _find_entities(result, status="confirmed")
    assert len(confirmed_entities) == 1
    entity = confirmed_entities[0]
    assert entity["label"] == "A公司"
    assert entity["participant_id"] == 12
    assert entity["source_conversation_ids"] == [101, 102]
    assert entity["raw_node_ids"] == ["company-101", "company-102"]
    assert entity["best_conversation_id"] == 101
    assert entity["edge_count_by_conversation"] == {"101": 2, "102": 1}
    assert entity["suggested_action"] == "view"
    assert result["stats"]["connected_entities"] == 1
    assert result["stats"]["isolated_anchors"] == 0


def test_pending_identity_candidate_becomes_task() -> None:
    raw_graph = {
        "nodes": [
            {"id": "company-201", "label": "A公司", "type": "organization", "conversation_id": 201},
            {"id": "company-202", "label": "A公司", "type": "organization", "conversation_id": 202},
        ],
        "edges": [],
    }
    raw_graph_payload = _make_raw_graph_payload(raw_graph)
    display_graph = build_dossier_display_graph(
        raw_graph=raw_graph_payload["graph"],
        confirmed_participants=[],
        participant_mappings=[],
    )
    participant_candidates = _build_cross_candidates(raw_graph_payload)

    result = build_dossier_coordination_graph(
        raw_graph_payload=raw_graph_payload,
        display_graph=display_graph,
        participant_candidates=participant_candidates,
        intelligence_payload={"confirmation_tasks": []},
        bound_conversation_ids=[201, 202],
    )

    pending_entities = _find_entities(result, status="pending_identity")
    pending_tasks = _find_tasks(result, task_type="entity_identity")
    assert len(pending_entities) == 1
    assert len(pending_tasks) == 1
    assert pending_entities[0]["label"] == "A公司"
    assert pending_entities[0]["source_conversation_ids"] == [201, 202]
    assert pending_entities[0]["suggested_action"] == "confirm_identity"
    assert pending_tasks[0]["status"] == "pending"
    assert pending_tasks[0]["source_conversation_ids"] == [201, 202]
    assert result["stats"]["pending_identity"] == 1
    assert result["stats"]["isolated_anchors"] == 0


def test_party_naming_task_becomes_pending_naming_task() -> None:
    raw_graph = {
        "nodes": [
            {"id": "self-301", "label": "当事人", "type": "person", "conversation_id": 301},
            {"id": "company-301", "label": "A公司", "type": "organization", "conversation_id": 301},
        ],
        "edges": [
            {
                "id": "edge-301-1",
                "source": "self-301",
                "target": "company-301",
                "label": "签订劳动合同",
                "properties": {"conversation_id": 301},
            }
        ],
    }
    raw_graph_payload = _make_raw_graph_payload(raw_graph)
    display_graph = build_dossier_display_graph(
        raw_graph=raw_graph_payload["graph"],
        confirmed_participants=[],
        participant_mappings=[],
    )
    party_naming_task = _build_party_naming_task(raw_graph_payload, display_graph)

    result = build_dossier_coordination_graph(
        raw_graph_payload=raw_graph_payload,
        display_graph=display_graph,
        participant_candidates=[],
        intelligence_payload={"confirmation_tasks": [party_naming_task]},
        bound_conversation_ids=[301],
    )

    pending_entities = _find_entities(result, status="pending_naming")
    pending_tasks = _find_tasks(result, task_type="party_naming")
    assert len(pending_entities) == 1
    assert len(pending_tasks) == 1
    assert pending_entities[0]["entity_type"] == "party"
    assert pending_entities[0]["label"] == "当事人"
    assert pending_entities[0]["source_conversation_ids"] == [301]
    assert pending_entities[0]["raw_node_ids"] == ["self-301"]
    assert pending_entities[0]["suggested_action"] == "name_party"
    assert pending_tasks[0]["reason"]
    assert result["stats"]["pending_naming"] == 1
    assert result["stats"]["isolated_anchors"] == 0


def test_isolated_conversation_produces_one_isolated_anchor() -> None:
    raw_graph = {
        "nodes": [
            {"id": "company-401", "label": "A公司", "type": "organization", "conversation_id": 401},
            {"id": "company-402", "label": "A公司", "type": "organization", "conversation_id": 402},
            {"id": "company-403", "label": "B公司", "type": "organization", "conversation_id": 403},
            {"id": "event-403", "label": "解除通知", "type": "event", "conversation_id": 403},
            {"id": "date-403", "label": "2024-02-01", "type": "date", "conversation_id": 403},
        ],
        "edges": [
            {
                "id": "edge-403-1",
                "source": "company-403",
                "target": "event-403",
                "label": "发出",
                "properties": {"conversation_id": 403},
            }
        ],
    }
    raw_graph_payload = _make_raw_graph_payload(raw_graph)
    display_graph = build_dossier_display_graph(
        raw_graph=raw_graph_payload["graph"],
        confirmed_participants=[],
        participant_mappings=[],
    )
    participant_candidates = _build_cross_candidates(raw_graph_payload)

    result = build_dossier_coordination_graph(
        raw_graph_payload=raw_graph_payload,
        display_graph=display_graph,
        participant_candidates=participant_candidates,
        intelligence_payload={"confirmation_tasks": []},
        bound_conversation_ids=[401, 402, 403],
    )

    isolated_entities = _find_entities(result, status="isolated_anchor")
    assert len(isolated_entities) == 1
    entity = isolated_entities[0]
    assert entity["label"] == "B公司"
    assert entity["source_conversation_ids"] == [403]
    assert entity["best_conversation_id"] == 403
    assert entity["suggested_action"] == "review_anchor"
    assert entity["raw_node_ids"] == ["company-403"]
    assert result["stats"]["isolated_anchors"] == 1
    assert "event-403" not in entity["raw_node_ids"]
    assert "date-403" not in entity["raw_node_ids"]


def test_coordination_graph_has_no_conversation_nodes_and_does_not_mutate_raw_graph() -> None:
    raw_graph = {
        "nodes": [
            {"id": "company-501", "label": "A公司", "type": "organization", "conversation_id": 501},
            {"id": "company-502", "label": "A公司", "type": "organization", "conversation_id": 502},
            {"id": "anchor-503", "label": "B公司", "type": "organization", "conversation_id": 503},
        ],
        "edges": [],
    }
    original_graph = deepcopy(raw_graph)
    raw_graph_payload = _make_raw_graph_payload(raw_graph)
    display_graph = build_dossier_display_graph(
        raw_graph=raw_graph_payload["graph"],
        confirmed_participants=[],
        participant_mappings=[],
    )
    participant_candidates = _build_cross_candidates(raw_graph_payload)

    result = build_dossier_coordination_graph(
        raw_graph_payload=raw_graph_payload,
        display_graph=display_graph,
        participant_candidates=participant_candidates,
        intelligence_payload={"confirmation_tasks": []},
        bound_conversation_ids=[501, 502, 503],
    )

    assert raw_graph == original_graph
    assert all(not str(entity["id"]).startswith("conversation:") for entity in result["entities"])
    assert all(
        not str(edge.get("source") or "").startswith("conversation:")
        and not str(edge.get("target") or "").startswith("conversation:")
        for edge in result["edges"]
    )
