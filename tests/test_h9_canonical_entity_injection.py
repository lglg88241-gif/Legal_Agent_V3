from __future__ import annotations

from app.services.dossier_state_snapshot import (
    build_dossier_state_snapshot,
    format_h9_canonical_entity_summary,
)


def test_h9_canonical_entity_summary_format() -> None:
    """Test that H9 canonical entity summary is formatted correctly."""
    dossier_entity_state = {
        "entities": [
            {
                "canonical_label": "张三",
                "entity_type": "person",
                "role": "current_party",
                "confidence": "high",
            },
            {
                "canonical_label": "B公司",
                "entity_type": "organization",
                "role": "employer",
                "confidence": "medium",
            },
        ],
        "relations": [
            {
                "source_label": "王经理",
                "label": "代表",
                "target_label": "B公司",
            }
        ],
    }

    h9_canonical_entity_summary = format_h9_canonical_entity_summary(dossier_entity_state)

    assert h9_canonical_entity_summary == "当前案卷已确认：当事人是张三；用人单位是B公司；王经理代表B公司。"


def test_h9_canonical_entity_summary_enrichment() -> None:
    """Test that H9 summary is prepended to dossier_graph_summary."""
    h9_canonical_entity_summary = "当前案卷已确认：当事人是张三；用人单位是B公司。"
    advisor_dossier_graph_summary = "案卷包含3个会话，涉及劳动争议。"

    enriched_dossier_graph_summary = (
        f"{h9_canonical_entity_summary}\n\n{advisor_dossier_graph_summary or ''}"
    ).strip()

    assert enriched_dossier_graph_summary.startswith("当前案卷已确认：当事人是张三")
    assert "案卷包含3个会话" in enriched_dossier_graph_summary
    assert "\n\n" in enriched_dossier_graph_summary


def test_h9_canonical_entity_summary_handles_none() -> None:
    """Test that enrichment handles None values gracefully."""
    h9_canonical_entity_summary = None
    advisor_dossier_graph_summary = "案卷包含3个会话。"

    enriched_dossier_graph_summary = advisor_dossier_graph_summary
    if h9_canonical_entity_summary:
        enriched_dossier_graph_summary = (
            f"{h9_canonical_entity_summary}\n\n{advisor_dossier_graph_summary or ''}"
        ).strip()

    assert enriched_dossier_graph_summary == "案卷包含3个会话。"


def test_h9_canonical_entity_summary_filters_low_confidence() -> None:
    """Test that low confidence entities are filtered out."""
    dossier_entity_state = {
        "entities": [
            {"canonical_label": "张三", "entity_type": "person", "role": "current_party", "confidence": "high"},
            {"canonical_label": "李四", "entity_type": "person", "role": "", "confidence": "low"},
            {"canonical_label": "B公司", "entity_type": "organization", "role": "employer", "confidence": "medium"},
        ],
        "relations": [],
    }

    h9_canonical_entity_summary = format_h9_canonical_entity_summary(dossier_entity_state)

    assert h9_canonical_entity_summary == "当前案卷已确认：当事人是张三；用人单位是B公司。"
    assert "李四" not in h9_canonical_entity_summary


def test_dossier_state_snapshot_builds_shared_outputs() -> None:
    snapshot = build_dossier_state_snapshot(
        conversation_entity_states=[
            {
                "should_build": True,
                "conversation_id": 701,
                "entities": [
                    {
                        "id": "conversation:701:entity:party",
                        "canonical_label": "张三",
                        "entity_type": "person",
                        "role": "current_party",
                        "confidence": "high",
                        "source": "conversation_graph",
                        "aliases": ["当事人"],
                        "mentions": [
                            {
                                "id": "conversation:701:mention:party",
                                "conversation_id": 701,
                                "raw_label": "张三",
                                "raw_node_id": "party-701",
                                "source": "conversation_graph",
                            }
                        ],
                    },
                    {
                        "id": "conversation:701:entity:company",
                        "canonical_label": "B公司",
                        "entity_type": "organization",
                        "role": "employer",
                        "confidence": "high",
                        "source": "conversation_graph",
                        "aliases": [],
                        "mentions": [
                            {
                                "id": "conversation:701:mention:company",
                                "conversation_id": 701,
                                "raw_label": "B公司",
                                "raw_node_id": "company-701",
                                "source": "conversation_graph",
                            }
                        ],
                    },
                ],
                "relations": [
                    {
                        "source": "conversation:701:entity:party",
                        "target": "conversation:701:entity:company",
                        "source_label": "张三",
                        "target_label": "B公司",
                        "relation": "签订劳动合同",
                        "relation_type": "contract",
                        "conversation_id": 701,
                        "confidence": "high",
                    }
                ],
            }
        ],
        dossier_id=51,
        user_id=7,
        resolution_decisions=[],
        conversation_summaries=[{"id": 701, "title": "劳动合同争议"}],
    )

    assert snapshot["canonical_entity_summary"] == "当前案卷已确认：当事人是张三；用人单位是B公司；张三签订劳动合同B公司。"
    assert snapshot["coordination_graph"]["stats"]["canonical_entities"] == 2
    assert sorted(entity["label"] for entity in snapshot["coordination_graph"]["entities"]) == ["B公司", "张三"]
    assert snapshot["dossier_entity_state"]["dossier_id"] == 51
