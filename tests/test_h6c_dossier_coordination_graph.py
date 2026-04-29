from __future__ import annotations

from copy import deepcopy

from app.services.dossier_coordination_graph import build_dossier_coordination_graph
from app.services.dossier_display_graph import build_dossier_display_graph


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


def _build_display_graph(payload: dict) -> dict:
    return build_dossier_display_graph(
        raw_graph=payload["graph"],
        confirmed_participants=payload.get("confirmed_participants") or [],
        participant_mappings=payload.get("participant_mappings") or [],
    )


def _find_entity_by_label(payload: dict, label: str) -> dict:
    for entity in payload["entities"]:
        if entity.get("label") == label:
            return entity
    raise AssertionError(f"entity not found: {label}")


def _find_task(payload: dict, *, task_type: str) -> dict:
    for task in payload["tasks"]:
        if task.get("type") == task_type:
            return task
    raise AssertionError(f"task not found: {task_type}")


def _find_edge_between(
    payload: dict,
    *,
    source_label: str,
    target_label: str,
    relation_type: str | None = None,
) -> dict:
    entity_lookup = {entity["id"]: entity for entity in payload["entities"]}
    for edge in payload["edges"]:
        source_entity = entity_lookup.get(edge.get("source"))
        target_entity = entity_lookup.get(edge.get("target"))
        if not source_entity or not target_entity:
            continue
        if source_entity.get("label") != source_label or target_entity.get("label") != target_label:
            continue
        if relation_type and edge.get("relation_type") != relation_type:
            continue
        return edge
    raise AssertionError(f"edge not found: {source_label} -> {target_label} ({relation_type or 'any'})")


def test_responsible_coordination_graph_filters_fact_nodes_and_derives_subject_edges() -> None:
    raw_graph = {
        "nodes": [
            {"id": "zhangsan-101", "label": "张三", "type": "person", "conversation_id": 101},
            {"id": "company-101", "label": "B公司", "type": "organization", "conversation_id": 101},
            {"id": "hr-101", "label": "B公司的人事", "type": "role", "conversation_id": 101},
            {"id": "contract-101", "label": "劳动合同", "type": "document", "conversation_id": 101},
            {"id": "insurance-101", "label": "社保", "type": "benefit", "conversation_id": 101},
            {"id": "salary-101", "label": "月薪8000", "type": "amount", "conversation_id": 101},
            {"id": "notice-101", "label": "人事通知", "type": "event", "conversation_id": 101},
            {"id": "statement-101", "label": "确认书", "type": "document", "conversation_id": 101},
            {"id": "zhangsan-102", "label": "张三", "type": "person", "conversation_id": 102},
            {"id": "company-102", "label": "B公司", "type": "organization", "conversation_id": 102},
            {"id": "contract-102", "label": "劳动合同", "type": "document", "conversation_id": 102},
            {"id": "lisi-103", "label": "李四", "type": "person", "conversation_id": 103},
        ],
        "edges": [
            {
                "id": "edge-101-contract-person",
                "source": "zhangsan-101",
                "target": "contract-101",
                "label": "签订",
                "properties": {"conversation_id": 101},
            },
            {
                "id": "edge-101-contract-company",
                "source": "company-101",
                "target": "contract-101",
                "label": "签约方",
                "properties": {"conversation_id": 101},
            },
            {
                "id": "edge-101-insurance-company",
                "source": "company-101",
                "target": "insurance-101",
                "label": "未缴纳",
                "properties": {"conversation_id": 101},
            },
            {
                "id": "edge-101-insurance-person",
                "source": "zhangsan-101",
                "target": "insurance-101",
                "label": "主张",
                "properties": {"conversation_id": 101},
            },
            {
                "id": "edge-101-notice-role",
                "source": "hr-101",
                "target": "notice-101",
                "label": "发送",
                "properties": {"conversation_id": 101},
            },
            {
                "id": "edge-101-notice-person",
                "source": "notice-101",
                "target": "zhangsan-101",
                "label": "通知",
                "properties": {"conversation_id": 101},
            },
            {
                "id": "edge-101-company-statement",
                "source": "company-101",
                "target": "statement-101",
                "label": "出具",
                "properties": {"conversation_id": 101},
            },
            {
                "id": "edge-102-contract-person",
                "source": "zhangsan-102",
                "target": "contract-102",
                "label": "续签",
                "properties": {"conversation_id": 102},
            },
            {
                "id": "edge-102-contract-company",
                "source": "company-102",
                "target": "contract-102",
                "label": "签约方",
                "properties": {"conversation_id": 102},
            },
        ],
    }
    original_graph = deepcopy(raw_graph)
    confirmed_participants = [
        {"id": 11, "label": "张三", "entity_type": "person", "party_side": "claimant"},
        {"id": 12, "label": "B公司", "entity_type": "organization", "party_side": "opponent"},
    ]
    participant_mappings = [
        {
            "participant_id": 11,
            "participant_label": "张三",
            "participant_entity_type": "person",
            "conversation_id": 101,
            "raw_label": "张三",
            "graph_node_id": "zhangsan-101",
            "graph_node_label": "张三",
            "graph_node_type": "person",
        },
        {
            "participant_id": 11,
            "participant_label": "张三",
            "participant_entity_type": "person",
            "conversation_id": 102,
            "raw_label": "张三",
            "graph_node_id": "zhangsan-102",
            "graph_node_label": "张三",
            "graph_node_type": "person",
        },
        {
            "participant_id": 12,
            "participant_label": "B公司",
            "participant_entity_type": "organization",
            "conversation_id": 101,
            "raw_label": "B公司",
            "graph_node_id": "company-101",
            "graph_node_label": "B公司",
            "graph_node_type": "organization",
        },
        {
            "participant_id": 12,
            "participant_label": "B公司",
            "participant_entity_type": "organization",
            "conversation_id": 102,
            "raw_label": "B公司",
            "graph_node_id": "company-102",
            "graph_node_label": "B公司",
            "graph_node_type": "organization",
        },
    ]

    raw_graph_payload = _make_raw_graph_payload(
        raw_graph,
        confirmed_participants=confirmed_participants,
        participant_mappings=participant_mappings,
    )
    display_graph = _build_display_graph(raw_graph_payload)

    result = build_dossier_coordination_graph(
        raw_graph_payload=raw_graph_payload,
        display_graph=display_graph,
        participant_candidates=[],
        intelligence_payload={"confirmation_tasks": []},
        bound_conversation_ids=[101, 102, 103],
    )

    assert raw_graph == original_graph

    labels = {entity["label"] for entity in result["entities"]}
    assert {"张三", "B公司", "B公司的人事", "李四"}.issubset(labels)
    assert "劳动合同" not in labels
    assert "社保" not in labels
    assert "月薪8000" not in labels
    assert "人事通知" not in labels
    assert "确认书" not in labels

    zhangsan = _find_entity_by_label(result, "张三")
    company = _find_entity_by_label(result, "B公司")
    hr_role = _find_entity_by_label(result, "B公司的人事")
    lisi = _find_entity_by_label(result, "李四")

    assert zhangsan["status"] == "confirmed"
    assert zhangsan["source_conversation_ids"] == [101, 102]
    assert zhangsan["raw_node_ids"] == ["zhangsan-101", "zhangsan-102"]
    assert zhangsan["best_conversation_id"] == 101
    assert zhangsan["best_conversation_reason"] == "该会话包含该实体最多的相关关系"
    assert zhangsan["edge_count_by_conversation"] == {"101": 3, "102": 1}
    assert [item["conversation_id"] for item in zhangsan["source_conversations"]] == [101, 102]
    assert zhangsan["source_conversations"][0]["relation_count"] == 3
    assert zhangsan["source_conversations"][0]["fact_count"] == 3
    assert zhangsan["source_conversations"][0]["is_best"] is True
    assert zhangsan["source_conversations"][1]["relation_count"] == 1
    assert zhangsan["source_conversations"][1]["is_best"] is False

    assert company["status"] == "confirmed"
    assert company["source_conversation_ids"] == [101, 102]
    assert company["raw_node_ids"] == ["company-101", "company-102"]
    assert company["source_conversations"][0]["conversation_id"] == 101
    assert "source_labels" in company["source_conversations"][0]

    assert hr_role["status"] == "isolated_anchor"
    assert hr_role["entity_type"] == "role"
    assert hr_role["source_conversation_ids"] == [101]

    assert lisi["status"] == "isolated_anchor"
    assert lisi["entity_type"] == "person"
    assert lisi["best_conversation_id"] == 103

    employment_edge = _find_edge_between(
        result,
        source_label="张三",
        target_label="B公司",
    )
    assert employment_edge["relation_type"] in {"employment_relation", "employment_dispute"}
    assert employment_edge["evidence"]
    assert employment_edge["evidence"][0]["conversation_id"] in {101, 102}
    assert employment_edge["evidence"][0]["summary"]

    affiliation_edge = _find_edge_between(
        result,
        source_label="B公司的人事",
        target_label="B公司",
        relation_type="affiliation",
    )
    assert affiliation_edge["label"] == "代表/隶属"
    assert affiliation_edge["evidence"]

    assert result["stats"]["connected_entities"] == 2
    assert result["stats"]["isolated_anchors"] >= 2
    assert result["trace"]["model"] == "dossier-coordination-rules-v1"
    assert (
        result["trace"]["fallback_reason"]
        == "llm_not_enabled_in_responsible_entity_graph"
    )

    assert all(not str(entity["id"]).startswith("conversation:") for entity in result["entities"])
    assert all(
        not str(edge.get("source") or "").startswith("conversation:")
        and not str(edge.get("target") or "").startswith("conversation:")
        for edge in result["edges"]
    )


def test_pending_identity_candidate_becomes_task() -> None:
    raw_graph = {
        "nodes": [
            {"id": "company-201", "label": "A公司", "type": "organization", "conversation_id": 201},
            {"id": "company-202", "label": "A公司", "type": "organization", "conversation_id": 202},
        ],
        "edges": [],
    }
    raw_graph_payload = _make_raw_graph_payload(raw_graph)
    display_graph = _build_display_graph(raw_graph_payload)
    participant_candidates = [
        {
            "task_type": "identity",
            "task_key": "candidate:a-company",
            "suggested_label": "A公司",
            "raw_labels": ["A公司"],
            "reason": "两个会话都出现同名公司，需确认是否为同一主体。",
            "confidence": "medium",
            "source_node": {
                "conversation_id": 201,
                "graph_node_id": "company-201",
                "graph_node_label": "A公司",
                "graph_node_type": "organization",
            },
            "target_node": {
                "conversation_id": 202,
                "graph_node_id": "company-202",
                "graph_node_label": "A公司",
                "graph_node_type": "organization",
            },
        }
    ]

    result = build_dossier_coordination_graph(
        raw_graph_payload=raw_graph_payload,
        display_graph=display_graph,
        participant_candidates=participant_candidates,
        intelligence_payload={"confirmation_tasks": []},
        bound_conversation_ids=[201, 202],
    )

    entity = _find_entity_by_label(result, "A公司")
    task = _find_task(result, task_type="entity_identity")

    assert entity["status"] == "pending_identity"
    assert entity["source_conversation_ids"] == [201, 202]
    assert entity["suggested_action"] == "confirm_identity"
    assert task["status"] == "pending"
    assert task["reason"] == "两个会话都出现同名公司，需确认是否为同一主体。"
    assert task["source_conversation_ids"] == [201, 202]


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
                "label": "签订",
                "properties": {"conversation_id": 301},
            }
        ],
    }
    raw_graph_payload = _make_raw_graph_payload(raw_graph)
    display_graph = _build_display_graph(raw_graph_payload)
    party_naming_task = {
        "task_type": "party_naming",
        "current_label": "当事人",
        "reason": "检测到泛称主体，需要用户确认其在本案中的名称。",
        "evidence": {
            "source_conversation_ids": [301],
            "raw_node_ids": ["self-301"],
            "raw_labels": ["当事人"],
        },
    }

    result = build_dossier_coordination_graph(
        raw_graph_payload=raw_graph_payload,
        display_graph=display_graph,
        participant_candidates=[],
        intelligence_payload={"confirmation_tasks": [party_naming_task]},
        bound_conversation_ids=[301],
    )

    entity = _find_entity_by_label(result, "当事人")
    task = _find_task(result, task_type="party_naming")

    assert entity["status"] == "pending_naming"
    assert entity["entity_type"] == "party"
    assert entity["source_conversation_ids"] == [301]
    assert entity["raw_node_ids"] == ["self-301"]
    assert entity["suggested_action"] == "name_party"
    assert task["reason"] == "检测到泛称主体，需要用户确认其在本案中的名称。"


def test_single_conversation_responsible_entities_enter_without_forced_naming() -> None:
    raw_graph = {
        "nodes": [
            {"id": "self-501", "label": "我", "type": "person", "conversation_id": 501},
            {"id": "company-501", "label": "B公司", "type": "organization", "conversation_id": 501},
            {"id": "manager-501", "label": "王经理", "type": "person", "conversation_id": 501},
            {"id": "contract-501", "label": "劳动合同", "type": "document", "conversation_id": 501},
            {"id": "notice-501", "label": "通知不用来了", "type": "event", "conversation_id": 501},
        ],
        "edges": [
            {
                "id": "edge-501-self-contract",
                "source": "self-501",
                "target": "contract-501",
                "label": "签订",
                "properties": {"conversation_id": 501},
            },
            {
                "id": "edge-501-company-contract",
                "source": "company-501",
                "target": "contract-501",
                "label": "签约方",
                "properties": {"conversation_id": 501},
            },
            {
                "id": "edge-501-manager-notice",
                "source": "manager-501",
                "target": "notice-501",
                "label": "口头通知",
                "properties": {"conversation_id": 501},
            },
            {
                "id": "edge-501-notice-self",
                "source": "notice-501",
                "target": "self-501",
                "label": "通知",
                "properties": {"conversation_id": 501},
            },
        ],
    }
    raw_graph_payload = _make_raw_graph_payload(raw_graph)
    display_graph = _build_display_graph(raw_graph_payload)
    party_naming_task = {
        "task_type": "party_naming",
        "current_label": "我",
        "reason": "检测到当前会话当事人代称，需要用户确认名称。",
        "evidence": {
            "source_conversation_ids": [501],
            "raw_node_ids": ["self-501"],
            "raw_labels": ["我"],
        },
    }

    result = build_dossier_coordination_graph(
        raw_graph_payload=raw_graph_payload,
        display_graph=display_graph,
        participant_candidates=[],
        intelligence_payload={"confirmation_tasks": [party_naming_task]},
        bound_conversation_ids=[501],
        conversation_summaries=[{"id": 501, "title": "王经理通知不用来了"}],
    )

    labels = {entity["label"] for entity in result["entities"]}
    assert {"B公司", "王经理"}.issubset(labels)
    assert {
        entity["label"]
        for entity in result["entities"]
        if entity["status"] == "pending_naming"
    } == {"我"}
    assert all(
        entity["status"] != "pending_naming"
        for entity in result["entities"]
        if entity["label"] in {"B公司", "王经理"}
    )

    company = _find_entity_by_label(result, "B公司")
    assert company["source_conversations"][0]["conversation_id"] == 501
    assert company["source_conversations"][0]["title"] == "王经理通知不用来了"
    assert company["source_conversations"][0]["source_labels"] == ["B公司"]
    assert company["source_conversations"][0]["relation_count"] >= 1
    assert company["best_conversation_id"] == 501


def test_confirmed_self_alias_becomes_named_entity_without_duplicate_anchor() -> None:
    raw_graph = {
        "nodes": [
            {"id": "self-601", "label": "当事人", "type": "person", "conversation_id": 601},
            {"id": "company-601", "label": "B公司", "type": "organization", "conversation_id": 601},
        ],
        "edges": [
            {
                "id": "edge-601-1",
                "source": "self-601",
                "target": "company-601",
                "label": "签订劳动合同",
                "properties": {"conversation_id": 601},
            }
        ],
    }
    confirmed_participants = [
        {"id": 21, "label": "张三", "entity_type": "person", "party_side": "claimant"},
    ]
    participant_mappings = [
        {
            "participant_id": 21,
            "participant_label": "张三",
            "participant_entity_type": "person",
            "conversation_id": 601,
            "raw_label": "当事人",
            "graph_node_id": "self-601",
            "graph_node_label": "当事人",
            "graph_node_type": "person",
        }
    ]
    raw_graph_payload = _make_raw_graph_payload(
        raw_graph,
        confirmed_participants=confirmed_participants,
        participant_mappings=participant_mappings,
    )
    display_graph = _build_display_graph(raw_graph_payload)

    result = build_dossier_coordination_graph(
        raw_graph_payload=raw_graph_payload,
        display_graph=display_graph,
        participant_candidates=[],
        intelligence_payload={"confirmation_tasks": []},
        bound_conversation_ids=[601],
        conversation_summaries=[{"id": 601, "title": "劳动合同和社保问题"}],
    )

    zhangsan = _find_entity_by_label(result, "张三")
    labels = [entity["label"] for entity in result["entities"]]
    assert zhangsan["status"] == "confirmed"
    assert zhangsan["participant_id"] == 21
    assert "当事人" in zhangsan["aliases"]
    assert {"张三", "当事人"}.issubset(set(zhangsan["source_labels"]))
    assert "当事人" not in [label for label in labels if label != "张三"]
    assert zhangsan["source_conversations"] == [
        {
            "conversation_id": 601,
            "title": "劳动合同和社保问题",
            "source_labels": ["当事人"],
            "raw_node_ids": ["self-601"],
            "fact_count": 0,
            "relation_count": 1,
            "is_best": True,
        }
    ]
