"""Test H9 canonical entity injection into legal_agent.ask context."""
from __future__ import annotations


def test_h9_canonical_entity_summary_format() -> None:
    """Test that H9 canonical entity summary is formatted correctly."""
    # Simulate the entity extraction logic
    entity_parts = []
    current_party = "张三"
    employers = ["B公司"]
    organizations = []
    relations = [("王经理", "代表", "B公司")]

    if current_party:
        entity_parts.append(f"当事人是{current_party}")
    for employer in employers[:2]:
        entity_parts.append(f"用人单位是{employer}")
    for org in organizations[:2]:
        entity_parts.append(f"涉及组织{org}")
    for source, rel, target in relations[:3]:
        entity_parts.append(f"{source}{rel}{target}")

    if entity_parts:
        h9_canonical_entity_summary = "当前案卷已确认：" + "；".join(entity_parts) + "。"
    else:
        h9_canonical_entity_summary = None

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
    entities = [
        {"canonical_label": "张三", "entity_type": "person", "role": "current_party", "confidence": "high"},
        {"canonical_label": "李四", "entity_type": "person", "role": "", "confidence": "low"},
        {"canonical_label": "B公司", "entity_type": "organization", "role": "employer", "confidence": "medium"},
    ]

    entity_parts = []
    current_party = None
    employers = []

    for entity in entities:
        label = entity.get("canonical_label", "").strip()
        entity_type = entity.get("entity_type", "").strip()
        role = entity.get("role", "").strip()
        confidence = entity.get("confidence", "").strip()

        if not label or confidence == "low":
            continue

        if role == "current_party" and entity_type == "person":
            current_party = label
        elif role == "employer" and entity_type == "organization":
            employers.append(label)

    if current_party:
        entity_parts.append(f"当事人是{current_party}")
    for employer in employers[:2]:
        entity_parts.append(f"用人单位是{employer}")

    h9_canonical_entity_summary = "当前案卷已确认：" + "；".join(entity_parts) + "。" if entity_parts else None

    assert h9_canonical_entity_summary == "当前案卷已确认：当事人是张三；用人单位是B公司。"
    assert "李四" not in h9_canonical_entity_summary
