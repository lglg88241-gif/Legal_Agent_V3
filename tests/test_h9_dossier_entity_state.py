from __future__ import annotations

from copy import deepcopy

from app.services.conversation_entity_state import build_conversation_entity_state
from app.services.dossier_entity_state import (
    build_dossier_entity_state,
    coordination_graph_from_dossier_entity_state,
)


def _state(message: str, conversation_id: int) -> dict:
    return build_conversation_entity_state(
        message_text=message,
        conversation_id=conversation_id,
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )


def _entity_by_label(dossier_state: dict, label: str) -> list[dict]:
    return [
        entity
        for entity in dossier_state["entities"]
        if entity["canonical_label"] == label
    ]


def test_dossier_agent_consumes_conversation_entity_state_not_party_naming_tasks() -> None:
    conversation_state = _state("我叫张三，是这起劳动争议里的劳动者。", 805)

    dossier_state = build_dossier_entity_state(
        conversation_entity_states=[conversation_state],
        dossier_id=51,
        user_id=7,
        resolution_decisions=[],
        conversation_summaries=[{"id": 805, "title": "劳动合同问题"}],
    )

    assert dossier_state["source"] == "dossier_cross_conversation_entity_agent"
    assert _entity_by_label(dossier_state, "张三")
    assert not _entity_by_label(dossier_state, "当事人")
    assert dossier_state["confirmation_tasks"] == []
    assert dossier_state["trace"]["steps"][0] == "read_conversation_entity_state"


def test_cross_conversation_same_entity_candidate_and_confirm_merge() -> None:
    state_805 = _state("我叫张三，是这起劳动争议里的劳动者。", 805)
    state_806 = _state("张三是本案申请人，B公司未缴纳社保。", 806)

    dossier_state = build_dossier_entity_state(
        conversation_entity_states=[state_805, state_806],
        dossier_id=51,
        user_id=7,
        resolution_decisions=[],
    )

    zhangsan_entities = _entity_by_label(dossier_state, "张三")
    assert len(zhangsan_entities) == 2
    task = dossier_state["confirmation_tasks"][0]
    assert task["task_type"] == "cross_conversation_same_entity"
    assert task["requires_user_confirmation"] is True
    assert set(task["source_conversation_ids"]) == {805, 806}

    merged_state = build_dossier_entity_state(
        conversation_entity_states=[state_805, state_806],
        dossier_id=51,
        user_id=7,
        resolution_decisions=[{"task_key": task["task_key"], "decision_type": "same"}],
    )

    merged_zhangsan_entities = _entity_by_label(merged_state, "张三")
    assert len(merged_zhangsan_entities) == 1
    assert merged_zhangsan_entities[0]["source_conversation_ids"] == [805, 806]
    assert {item["conversation_id"] for item in merged_zhangsan_entities[0]["source_conversations"]} == {805, 806}
    assert merged_state["confirmation_tasks"] == []


def test_reject_same_entity_keeps_separate_entities_and_suppresses_task() -> None:
    state_805 = _state("我叫张三，是这起劳动争议里的劳动者。", 805)
    state_806 = _state("张三是本案申请人，B公司未缴纳社保。", 806)
    initial_state = build_dossier_entity_state(
        conversation_entity_states=[state_805, state_806],
        dossier_id=51,
        user_id=7,
        resolution_decisions=[],
    )
    task_key = initial_state["confirmation_tasks"][0]["task_key"]

    rejected_state = build_dossier_entity_state(
        conversation_entity_states=[state_805, state_806],
        dossier_id=51,
        user_id=7,
        resolution_decisions=[{"task_key": task_key, "decision_type": "not_same"}],
    )

    assert len(_entity_by_label(rejected_state, "张三")) == 2
    assert rejected_state["confirmation_tasks"] == []


def test_person_to_company_never_identity_candidate() -> None:
    state = _state("B公司的人事王经理口头通知我不用来了。", 806)

    dossier_state = build_dossier_entity_state(
        conversation_entity_states=[state],
        dossier_id=51,
        user_id=7,
        resolution_decisions=[],
    )
    graph = coordination_graph_from_dossier_entity_state(dossier_state)

    labels = {entity["label"] for entity in graph["entities"]}
    assert {"B公司", "王经理"}.issubset(labels)
    assert not any(task["task_type"] == "cross_conversation_same_entity" for task in graph["tasks"])
    assert any(edge["relation_type"] == "affiliation" for edge in graph["edges"])


def test_unresolved_current_party_placeholder_stays_out_of_dossier_graph() -> None:
    conversation_state = _state("我和B公司签了劳动合同。", 806)
    assert _entity_by_label({"entities": conversation_state["entities"]}, "当前会话当事人")
    assert conversation_state["clarification_tasks"]

    dossier_state = build_dossier_entity_state(
        conversation_entity_states=[conversation_state],
        dossier_id=51,
        user_id=7,
        resolution_decisions=[],
    )
    graph = coordination_graph_from_dossier_entity_state(dossier_state)

    assert not _entity_by_label(dossier_state, "当前会话当事人")
    assert not any(entity["label"] == "当前会话当事人" for entity in graph["entities"])
    assert not any(
        task["source_entity"]["label"] == "当前会话当事人"
        or task["target_entity"]["label"] == "当前会话当事人"
        for task in dossier_state["confirmation_tasks"]
    )
    assert _entity_by_label(dossier_state, "B公司")


def test_confirmation_task_entities_include_guard_fields() -> None:
    state_805 = _state("我叫张三，是这起劳动争议里的劳动者。", 805)
    state_806 = _state("张三是本案申请人，B公司未缴纳社保。", 806)

    dossier_state = build_dossier_entity_state(
        conversation_entity_states=[state_805, state_806],
        dossier_id=51,
        user_id=7,
        resolution_decisions=[],
    )

    task = dossier_state["confirmation_tasks"][0]
    for side in ("source_entity", "target_entity"):
        entity = task[side]
        assert entity["dossier_id"] == 51
        assert "role" in entity
        assert "aliases" in entity
        assert entity["source_entity_ids"]
        assert entity["source_conversation_ids"]


def test_raw_graph_not_mutated_by_dossier_overlay() -> None:
    raw_graph = {
        "nodes": [
            {"id": "person-1", "label": "张三", "type": "person", "conversation_id": 805},
            {"id": "company-1", "label": "B公司", "type": "organization", "conversation_id": 805},
        ],
        "edges": [
            {"id": "edge-1", "source": "person-1", "target": "company-1", "label": "签订", "properties": {"conversation_id": 805}},
        ],
    }
    original_graph = deepcopy(raw_graph)
    conversation_state = build_conversation_entity_state(
        message_text="我叫张三，是这起劳动争议里的劳动者，我和B公司签了劳动合同。",
        raw_graph_payload={"graph": raw_graph},
        conversation_id=805,
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )

    dossier_state = build_dossier_entity_state(
        conversation_entity_states=[conversation_state],
        dossier_id=51,
        user_id=7,
        resolution_decisions=[],
    )
    coordination_graph_from_dossier_entity_state(dossier_state)

    assert raw_graph == original_graph
