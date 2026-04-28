from __future__ import annotations

from copy import deepcopy

from app.services.dossier_display_graph import (
    DISPLAY_PARTICIPANT_KIND,
    DISPLAY_RAW_ENTITY_KIND,
    DISPLAY_RAW_EVENT_KIND,
    build_dossier_display_graph,
)


def _find_node(payload: dict, node_id: str) -> dict:
    return next(node for node in payload["nodes"] if node["id"] == node_id)


def _find_nodes_by_kind(payload: dict, kind: str) -> list[dict]:
    return [node for node in payload["nodes"] if node.get("kind") == kind]


def test_confirmed_participant_merges_two_raw_organization_nodes_across_conversations() -> None:
    raw_graph = {
        "nodes": [
            {"id": "raw-674-a", "label": "A公司", "type": "organization", "conversation_id": 674},
            {"id": "raw-675-a", "label": "A公司", "type": "organization", "conversation_id": 675},
        ],
        "edges": [],
    }
    confirmed_participants = [
        {
            "id": 12,
            "label": "A公司",
            "entity_type": "organization",
            "party_side": "opponent",
        }
    ]
    participant_mappings = [
        {
            "participant_id": 12,
            "participant_label": "A公司",
            "participant_entity_type": "organization",
            "conversation_id": 674,
            "raw_label": "A公司",
            "graph_node_id": "raw-674-a",
            "graph_node_label": "A公司",
            "graph_node_type": "organization",
        },
        {
            "participant_id": 12,
            "participant_label": "A公司",
            "participant_entity_type": "organization",
            "conversation_id": 675,
            "raw_label": "A公司",
            "graph_node_id": "raw-675-a",
            "graph_node_label": "A公司",
            "graph_node_type": "organization",
        },
    ]

    result = build_dossier_display_graph(raw_graph, confirmed_participants, participant_mappings)

    participant_nodes = _find_nodes_by_kind(result, DISPLAY_PARTICIPANT_KIND)
    assert len(participant_nodes) == 1
    participant_node = participant_nodes[0]
    assert participant_node["id"] == "participant:12"
    assert participant_node["display_label"] == "A公司"
    assert participant_node["confirmed"] is True
    assert participant_node["pending_confirmation"] is False
    assert participant_node["source_conversation_ids"] == [674, 675]
    assert participant_node["raw_node_ids"] == ["raw-674-a", "raw-675-a"]
    assert participant_node["raw_labels"] == ["A公司"]
    assert result["stats"]["raw_node_count"] == 2
    assert result["stats"]["display_node_count"] == 1
    assert result["stats"]["merged_participant_count"] == 1
    assert result["stats"]["pending_confirmation_count"] == 0


def test_unconfirmed_same_label_across_conversations_remains_separate_and_pending() -> None:
    raw_graph = {
        "nodes": [
            {"id": "raw-674-a", "label": "A公司", "type": "organization", "conversation_id": 674},
            {"id": "raw-675-a", "label": "A公司", "type": "organization", "conversation_id": 675},
        ],
        "edges": [],
    }

    result = build_dossier_display_graph(raw_graph, [], [])

    raw_nodes = _find_nodes_by_kind(result, DISPLAY_RAW_ENTITY_KIND)
    assert len(raw_nodes) == 2
    assert {node["id"] for node in raw_nodes} == {"raw:raw-674-a", "raw:raw-675-a"}
    assert all(node["display_label"] == "A公司" for node in raw_nodes)
    assert all(node["confirmed"] is False for node in raw_nodes)
    assert all(node["pending_confirmation"] is True for node in raw_nodes)
    assert sorted(node["source_conversation_ids"][0] for node in raw_nodes) == [674, 675]
    assert result["stats"]["display_node_count"] == 2
    assert result["stats"]["merged_participant_count"] == 0
    assert result["stats"]["pending_confirmation_count"] == 2


def test_unmapped_event_nodes_with_same_label_remain_raw_events() -> None:
    raw_graph = {
        "nodes": [
            {"id": "event-674", "label": "解除通知", "type": "event", "conversation_id": 674},
            {"id": "event-675", "label": "解除通知", "type": "event", "conversation_id": 675},
        ],
        "edges": [],
    }

    result = build_dossier_display_graph(raw_graph, [], [])

    assert _find_nodes_by_kind(result, DISPLAY_PARTICIPANT_KIND) == []
    event_nodes = _find_nodes_by_kind(result, DISPLAY_RAW_EVENT_KIND)
    assert len(event_nodes) == 2
    assert {node["id"] for node in event_nodes} == {"raw:event-674", "raw:event-675"}
    assert all(node["confirmed"] is False for node in event_nodes)
    assert all(node["pending_confirmation"] is False for node in event_nodes)


def test_confirmed_mapping_merges_subject_and_object_typed_nodes_into_one_participant() -> None:
    raw_graph = {
        "nodes": [
            {"id": "4:graph:6057", "label": "A公司", "type": "客体", "conversation_id": 679},
            {"id": "4:graph:3408", "label": "A公司", "type": "主体", "conversation_id": 680},
        ],
        "edges": [],
    }
    original = deepcopy(raw_graph)
    confirmed_participants = [
        {"id": 100, "label": "A公司", "entity_type": "organization", "party_side": "opponent"},
    ]
    participant_mappings = [
        {
            "participant_id": 100,
            "participant_label": "A公司",
            "participant_entity_type": "organization",
            "conversation_id": 679,
            "raw_label": "A公司",
            "graph_node_id": "4:graph:6057",
            "graph_node_label": "A公司",
            "graph_node_type": "客体",
        },
        {
            "participant_id": 100,
            "participant_label": "A公司",
            "participant_entity_type": "organization",
            "conversation_id": 680,
            "raw_label": "A公司",
            "graph_node_id": "4:graph:3408",
            "graph_node_label": "A公司",
            "graph_node_type": "主体",
        },
    ]

    result = build_dossier_display_graph(raw_graph, confirmed_participants, participant_mappings)

    participant_nodes = _find_nodes_by_kind(result, DISPLAY_PARTICIPANT_KIND)
    assert len(participant_nodes) == 1
    participant_node = participant_nodes[0]
    assert participant_node["id"] == "participant:100"
    assert participant_node["display_label"] == "A公司"
    assert participant_node["confirmed"] is True
    assert participant_node["pending_confirmation"] is False
    assert participant_node["source_conversation_ids"] == [679, 680]
    assert participant_node["raw_node_ids"] == ["4:graph:3408", "4:graph:6057"]
    assert participant_node["raw_labels"] == ["A公司"]
    assert all(node["id"] != "raw:4:graph:6057" for node in result["nodes"])
    assert all(node["id"] != "raw:4:graph:3408" for node in result["nodes"])
    assert raw_graph == original


def test_raw_graph_is_not_mutated() -> None:
    raw_graph = {
        "nodes": [
            {"id": "raw-674-a", "label": "A公司", "type": "organization", "conversation_id": 674},
            {"id": "event-674", "label": "解除通知", "type": "event", "conversation_id": 674},
        ],
        "edges": [
            {"id": "edge-1", "source": "raw-674-a", "target": "event-674", "label": "发出"},
        ],
    }
    original = deepcopy(raw_graph)

    build_dossier_display_graph(raw_graph, [], [])

    assert raw_graph == original


def test_edge_endpoints_are_remapped_to_participant_display_nodes() -> None:
    raw_graph = {
        "nodes": [
            {"id": "company-674", "label": "A公司", "type": "organization", "conversation_id": 674},
            {"id": "event-674", "label": "解除通知", "type": "event", "conversation_id": 674},
        ],
        "edges": [
            {
                "id": "edge-674-1",
                "source": "company-674",
                "target": "event-674",
                "label": "发出",
            }
        ],
    }
    confirmed_participants = [
        {"id": 12, "label": "A公司", "entity_type": "organization"},
    ]
    participant_mappings = [
        {
            "participant_id": 12,
            "participant_label": "A公司",
            "participant_entity_type": "organization",
            "conversation_id": 674,
            "raw_label": "A公司",
            "graph_node_id": "company-674",
        }
    ]

    result = build_dossier_display_graph(raw_graph, confirmed_participants, participant_mappings)

    assert len(result["edges"]) == 1
    edge = result["edges"][0]
    assert edge["source"] == "participant:12"
    assert edge["target"] == "raw:event-674"
    assert edge["source_raw_id"] == "company-674"
    assert edge["target_raw_id"] == "event-674"
    assert edge["source_conversation_ids"] == [674]
    assert edge["target_conversation_ids"] == [674]
    assert edge["conversation_ids"] == [674]


def test_self_anchor_display_label_uses_confirmed_participant_label() -> None:
    raw_graph = {
        "nodes": [
            {"id": "self-674", "label": "我", "type": "person", "conversation_id": 674},
            {"id": "self-675", "label": "本人", "type": "person", "conversation_id": 675},
        ],
        "edges": [],
    }
    confirmed_participants = [
        {
            "id": 21,
            "label": "劳动者本人",
            "entity_type": "person",
            "party_side": "claimant",
        }
    ]
    participant_mappings = [
        {
            "participant_id": 21,
            "participant_label": "劳动者本人",
            "participant_entity_type": "person",
            "conversation_id": 674,
            "raw_label": "我",
            "graph_node_id": "self-674",
        },
        {
            "participant_id": 21,
            "participant_label": "劳动者本人",
            "participant_entity_type": "person",
            "conversation_id": 675,
            "raw_label": "本人",
            "graph_node_id": "self-675",
        },
    ]

    result = build_dossier_display_graph(raw_graph, confirmed_participants, participant_mappings)

    node = _find_node(result, "participant:21")
    assert node["kind"] == DISPLAY_PARTICIPANT_KIND
    assert node["display_label"] == "劳动者本人"
    assert node["entity_type"] == "person"
    assert node["source_conversation_ids"] == [674, 675]
    assert node["raw_node_ids"] == ["self-674", "self-675"]
    assert set(node["raw_labels"]) == {"本人", "我"}
