from __future__ import annotations

from app.services.conversation_entity_state import build_conversation_entity_state


def _labels(state: dict) -> set[str]:
    return {entity["canonical_label"] for entity in state["entities"]}


def _entity(state: dict, label: str) -> dict:
    for entity in state["entities"]:
        if entity["canonical_label"] == label:
            return entity
    raise AssertionError(f"entity not found: {label}")


def _graph(nodes: list[dict], edges: list[dict] | None = None) -> dict:
    return {"graph": {"nodes": nodes, "edges": edges or []}}


def test_self_named_party_becomes_conversation_current_party_entity_from_graph() -> None:
    state = build_conversation_entity_state(
        message_text="我叫张三，是这起劳动争议里的劳动者。",
        raw_graph_payload=_graph(
            [
                {
                    "id": "person-806",
                    "label": "张三",
                    "type": "person",
                    "conversation_id": 806,
                    "properties": {"role": "current_party"},
                },
                {"id": "self-806", "label": "我", "type": "person", "conversation_id": 806},
                {"id": "worker-806", "label": "劳动者", "type": "person", "conversation_id": 806},
            ]
        ),
        conversation_id=806,
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )

    assert state["should_build"] is True
    assert state["entity_source"] == "conversation_graph"
    assert _labels(state) == {"张三"}
    zhangsan = _entity(state, "张三")
    assert zhangsan["source"] == "conversation_graph"
    assert zhangsan["role"] == "current_party"
    assert {"我", "劳动者"}.issubset(set(zhangsan["aliases"]))
    assert state["party_naming_decision"]["should_create_party_naming"] is False
    assert state["clarification_tasks"] == []


def test_manager_company_affiliation_resolved_from_graph_relations() -> None:
    state = build_conversation_entity_state(
        message_text="B公司的人事王经理口头通知我不用来了。",
        raw_graph_payload=_graph(
            [
                {"id": "company-806", "label": "B公司", "type": "organization", "conversation_id": 806},
                {"id": "manager-806", "label": "王经理", "type": "person", "conversation_id": 806},
            ],
            [
                {
                    "id": "edge-806",
                    "source": "manager-806",
                    "target": "company-806",
                    "label": "人事/代表/隶属",
                    "conversation_id": 806,
                }
            ],
        ),
        conversation_id=806,
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )

    assert {"B公司", "王经理"}.issubset(_labels(state))
    assert "当前会话当事人" not in _labels(state)
    company = _entity(state, "B公司")
    manager = _entity(state, "王经理")
    assert company["entity_type"] == "organization"
    assert manager["entity_type"] == "person"
    assert any(
        relation["source_label"] == "王经理"
        and relation["target_label"] == "B公司"
        and relation["relation_type"] == "affiliation"
        for relation in state["relations"]
    )
    assert not any(
        relation["source_label"] == "王经理"
        and relation["target_label"] == "B公司"
        and relation["relation_type"] == "identity"
        for relation in state["relations"]
    )


def test_third_party_retelling_uses_graph_state_not_message_self_anchor() -> None:
    state = build_conversation_entity_state(
        message_text="我的委托人张三称，B公司未给其缴纳社保。",
        raw_graph_payload=_graph(
            [
                {
                    "id": "person-807",
                    "label": "张三",
                    "type": "person",
                    "conversation_id": 807,
                    "properties": {"role": "represented_party"},
                },
                {"id": "company-807", "label": "B公司", "type": "organization", "conversation_id": 807},
            ]
        ),
        conversation_id=807,
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )

    assert {"张三", "B公司"}.issubset(_labels(state))
    zhangsan = _entity(state, "张三")
    assert zhangsan["role"] == "represented_party"
    assert "current_party" not in {entity["role"] for entity in state["entities"]}
    assert state["party_naming_decision"]["should_create_party_naming"] is False


def test_third_party_retelling_does_not_globally_override_later_graph_self_name() -> None:
    state = build_conversation_entity_state(
        message_text="我的委托人张三称，B公司未给其缴纳社保。\n我叫李四，是这起劳动争议里的劳动者。",
        raw_graph_payload=_graph(
            [
                {
                    "id": "person-client-809",
                    "label": "张三",
                    "type": "person",
                    "conversation_id": 809,
                    "properties": {"role": "represented_party"},
                },
                {
                    "id": "person-self-809",
                    "label": "李四",
                    "type": "person",
                    "conversation_id": 809,
                    "properties": {"role": "current_party"},
                },
                {"id": "company-809", "label": "B公司", "type": "organization", "conversation_id": 809},
            ]
        ),
        conversation_id=809,
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )

    assert {"张三", "李四", "B公司"}.issubset(_labels(state))
    assert _entity(state, "张三")["role"] == "represented_party"
    assert _entity(state, "李四")["role"] == "current_party"
    assert state["party_naming_decision"]["should_create_party_naming"] is False
    assert state["clarification_tasks"] == []


def test_raw_graph_without_conversation_id_must_have_current_incident_edge_scope() -> None:
    raw_graph = {
        "nodes": [
            {"id": "company-805", "label": "A公司", "type": "organization"},
            {"id": "company-806", "label": "B公司", "type": "organization"},
            {"id": "manager-806", "label": "王经理", "type": "person"},
        ],
        "edges": [
            {"id": "edge-805", "source": "company-805", "target": "x-805", "label": "关联", "conversation_id": 805},
            {"id": "edge-806", "source": "company-806", "target": "manager-806", "label": "人事", "conversation_id": 806},
        ],
    }

    state = build_conversation_entity_state(
        message_text="B公司的人事王经理口头通知我不用来了。",
        raw_graph_payload={"graph": raw_graph},
        conversation_id=806,
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )

    assert "A公司" not in _labels(state)
    assert {"B公司", "王经理"}.issubset(_labels(state))


def test_conversation_entity_state_prefers_graph_over_message_regex() -> None:
    state = build_conversation_entity_state(
        message_text="我叫张三，是这起劳动争议里的劳动者。我和B公司签了劳动合同，但B公司一直没有给我缴纳社保。",
        raw_graph_payload=_graph(
            [
                {
                    "id": "person-810",
                    "label": "张三",
                    "type": "person",
                    "conversation_id": 810,
                    "properties": {"role": "current_party"},
                },
                {"id": "company-810", "label": "B公司", "type": "organization", "conversation_id": 810},
            ]
        ),
        conversation_id=810,
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )

    assert state["entity_source"] == "conversation_graph"
    assert "B公司" in _labels(state)
    assert "但B公司" not in _labels(state)
    assert all(entity["source"] == "conversation_graph" for entity in state["entities"])
    assert "read_conversation_graph" in state["trace"]["steps"]


def test_conversation_entity_state_reports_prefixed_org_if_graph_contains_it() -> None:
    state = build_conversation_entity_state(
        message_text="我叫张三，是这起劳动争议里的劳动者。我和B公司签了劳动合同，但B公司一直没有给我缴纳社保。",
        raw_graph_payload=_graph(
            [
                {
                    "id": "person-811",
                    "label": "张三",
                    "type": "person",
                    "conversation_id": 811,
                    "properties": {"role": "current_party"},
                },
                {"id": "company-811", "label": "但B公司", "type": "organization", "conversation_id": 811},
            ]
        ),
        conversation_id=811,
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )

    assert state["entity_source"] == "conversation_graph"
    assert "但B公司" in _labels(state)
    assert _entity(state, "但B公司")["source"] == "conversation_graph"
    assert any(
        mention["raw_label"] == "但B公司"
        for mention in _entity(state, "但B公司")["mentions"]
    )
    assert any("graph_extractor_prefixed_org_label:但B公司" in item for item in state["trace"]["warnings"])


def test_message_regex_fallback_is_low_confidence_when_graph_unavailable() -> None:
    state = build_conversation_entity_state(
        message_text="我叫张三，是这起劳动争议里的劳动者。我和B公司签了劳动合同。",
        conversation_id=812,
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )

    assert state["entity_source"] == "fallback_rules"
    assert state["fallback_reason"] == "conversation_graph_unavailable"
    assert {entity["source"] for entity in state["entities"]} == {"fallback_rules"}
    assert {entity["confidence"] for entity in state["entities"]} == {"low"}


def test_legal_qa_does_not_build_conversation_entity_state() -> None:
    state = build_conversation_entity_state(
        message_text="劳动合同法第八十七条规定了什么？",
        message_understanding={"intent": "legal_question", "route_hint": "LEGAL_QA"},
        conversation_id=808,
        conversation_mode="LEGAL_QA",
        persistence_consent="unknown",
    )

    assert state["should_build"] is False
    assert state["skip_reason"] == "legal_qa"
    assert state["entities"] == []
    assert state["party_naming_decision"]["should_create_party_naming"] is False
