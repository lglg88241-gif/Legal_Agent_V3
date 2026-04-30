from __future__ import annotations

from app.services.conversation_entity_state import build_conversation_entity_state


def _labels(state: dict) -> set[str]:
    return {entity["canonical_label"] for entity in state["entities"]}


def _entity(state: dict, label: str) -> dict:
    for entity in state["entities"]:
        if entity["canonical_label"] == label:
            return entity
    raise AssertionError(f"entity not found: {label}")


def test_self_named_party_becomes_conversation_current_party_entity() -> None:
    state = build_conversation_entity_state(
        message_text="我叫张三，是这起劳动争议里的劳动者。",
        conversation_id=806,
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )

    assert state["should_build"] is True
    assert _labels(state) == {"张三"}
    zhangsan = _entity(state, "张三")
    assert zhangsan["role"] == "current_party"
    assert {"我", "本人", "当事人", "劳动者"}.issubset(set(zhangsan["aliases"]))
    assert state["party_naming_decision"]["should_create_party_naming"] is False
    assert state["clarification_tasks"] == []


def test_manager_company_affiliation_resolved_in_conversation_state() -> None:
    state = build_conversation_entity_state(
        message_text="B公司的人事王经理口头通知我不用来了。",
        conversation_id=806,
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )

    assert {"B公司", "王经理", "当前会话当事人"}.issubset(_labels(state))
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


def test_third_party_retelling_does_not_create_self_party_task() -> None:
    state = build_conversation_entity_state(
        message_text="我的委托人张三称，B公司未给其缴纳社保。",
        conversation_id=807,
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )

    assert {"张三", "B公司"}.issubset(_labels(state))
    zhangsan = _entity(state, "张三")
    assert zhangsan["role"] == "represented_party"
    assert "current_party" not in {entity["role"] for entity in state["entities"]}
    assert state["party_naming_decision"]["should_create_party_naming"] is False
    assert state["party_naming_decision"]["reason"] == "third_party_retelling_not_user_self_anchor"


def test_third_party_retelling_does_not_globally_override_later_self_name() -> None:
    state = build_conversation_entity_state(
        message_text="我的委托人张三称，B公司未给其缴纳社保。\n我叫李四，是这起劳动争议里的劳动者。",
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
