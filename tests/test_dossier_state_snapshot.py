from __future__ import annotations

from app.services.dossier_state_snapshot import build_dossier_state_snapshot


def test_build_dossier_state_snapshot_returns_canonical_state_and_coordination_graph() -> None:
    snapshot = build_dossier_state_snapshot(
        conversation_entity_states=[
            {
                "should_build": True,
                "conversation_id": 808,
                "entities": [
                    {
                        "id": "conversation:808:entity:zhangsan",
                        "canonical_label": "张三",
                        "entity_type": "person",
                        "role": "current_party",
                        "confidence": "high",
                        "source": "conversation_graph",
                        "aliases": ["我", "劳动者"],
                        "mentions": [
                            {
                                "id": "conversation:808:mention:zhangsan",
                                "conversation_id": 808,
                                "raw_label": "张三",
                                "raw_node_id": "person-808",
                                "source": "conversation_graph",
                            }
                        ],
                    },
                    {
                        "id": "conversation:808:entity:company",
                        "canonical_label": "B公司",
                        "entity_type": "organization",
                        "role": "employer",
                        "confidence": "high",
                        "source": "conversation_graph",
                        "aliases": [],
                        "mentions": [
                            {
                                "id": "conversation:808:mention:company",
                                "conversation_id": 808,
                                "raw_label": "B公司",
                                "raw_node_id": "company-808",
                                "source": "conversation_graph",
                            }
                        ],
                    },
                ],
                "relations": [
                    {
                        "source": "conversation:808:entity:zhangsan",
                        "target": "conversation:808:entity:company",
                        "source_label": "张三",
                        "target_label": "B公司",
                        "relation": "入职",
                        "relation_type": "employment",
                        "conversation_id": 808,
                        "confidence": "high",
                    }
                ],
            }
        ],
        dossier_id=51,
        user_id=7,
        resolution_decisions=[],
        conversation_summaries=[{"id": 808, "title": "入职争议"}],
    )

    assert snapshot["dossier_entity_state"]["dossier_id"] == 51
    assert snapshot["coordination_graph"]["trace"]["mode"] == "agentic_dossier_entity_state"
    assert snapshot["canonical_entity_summary"] == "当前案卷已确认：当事人是张三；用人单位是B公司；张三入职B公司。"
    assert sorted(entity["label"] for entity in snapshot["coordination_graph"]["entities"]) == ["B公司", "张三"]
