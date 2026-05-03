from __future__ import annotations

from copy import deepcopy

import pytest

from app.services.conversation_entity_state import build_conversation_entity_state
from app.services.dossier_entity_state import (
    build_dossier_entity_state,
    coordination_graph_from_dossier_entity_state,
)


def _graph(nodes: list[dict], edges: list[dict] | None = None) -> dict:
    return {"graph": {"nodes": nodes, "edges": edges or []}}


def _conversation_state(conversation_id: int, nodes: list[dict], edges: list[dict] | None = None, message: str = "") -> dict:
    return build_conversation_entity_state(
        message_text=message,
        raw_graph_payload=_graph(nodes, edges),
        conversation_id=conversation_id,
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )


def _named_party_state(conversation_id: int, label: str = "张三") -> dict:
    return _conversation_state(
        conversation_id,
        [
            {
                "id": f"person-{conversation_id}",
                "label": label,
                "type": "person",
                "conversation_id": conversation_id,
                "properties": {"role": "current_party"},
            }
        ],
        message=f"我叫{label}，是这起劳动争议里的劳动者。",
    )


def _entity_by_label(dossier_state: dict, label: str) -> list[dict]:
    return [
        entity
        for entity in dossier_state["entities"]
        if entity["canonical_label"] == label
    ]


def test_dossier_agent_consumes_conversation_entity_state_not_party_naming_tasks() -> None:
    conversation_state = _named_party_state(805)

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


def test_dossier_agent_filters_attendance_action_even_if_marked_subject() -> None:
    conversation_state = {
        "conversation_id": 901,
        "should_build": True,
        "entities": [
            {
                "id": "conversation:901:entity:zhang",
                "canonical_label": "\u5f20\u4e09",
                "entity_type": "person",
                "role": "current_party",
                "confidence": "high",
                "source": "conversation_graph",
                "mentions": [{"raw_label": "\u5f20\u4e09", "conversation_id": 901}],
            },
            {
                "id": "conversation:901:entity:clock-in",
                "canonical_label": "\u6253\u5361",
                "entity_type": "\u4e3b\u4f53",
                "role": "",
                "confidence": "high",
                "source": "conversation_graph",
                "mentions": [{"raw_label": "\u6253\u5361", "conversation_id": 901}],
            },
        ],
        "relations": [],
    }

    dossier_state = build_dossier_entity_state(
        conversation_entity_states=[conversation_state],
        dossier_id=51,
        user_id=7,
        resolution_decisions=[],
    )

    assert _entity_by_label(dossier_state, "\u5f20\u4e09")
    assert not _entity_by_label(dossier_state, "\u6253\u5361")


@pytest.mark.parametrize(
    "label",
    [
        "\u878d\u8d44\u5230\u8d26",
        "\u53d1\u653e\u5de5\u8d44",
        "\u79bb\u804c\u901a\u77e5",
        "\u8f9e\u9000\u4e8b\u5b9e",
        "\u52b3\u52a8\u5173\u7cfb",
        "\u8865\u7b7e\u534f\u8bae",
    ],
)
def test_dossier_agent_filters_abstract_fact_even_if_marked_subject(label: str) -> None:
    conversation_state = {
        "conversation_id": 904,
        "should_build": True,
        "entities": [
            {
                "id": "conversation:904:entity:zhang",
                "canonical_label": "\u5f20\u4e09",
                "entity_type": "person",
                "role": "current_party",
                "confidence": "high",
                "source": "conversation_graph",
                "mentions": [{"raw_label": "\u5f20\u4e09", "conversation_id": 904}],
            },
            {
                "id": f"conversation:904:entity:{label}",
                "canonical_label": label,
                "entity_type": "\u4e3b\u4f53",
                "role": "",
                "confidence": "high",
                "source": "conversation_graph",
                "mentions": [{"raw_label": label, "conversation_id": 904}],
            },
        ],
        "relations": [],
    }

    dossier_state = build_dossier_entity_state(
        conversation_entity_states=[conversation_state],
        dossier_id=51,
        user_id=7,
        resolution_decisions=[],
    )

    assert _entity_by_label(dossier_state, "\u5f20\u4e09")
    assert not _entity_by_label(dossier_state, label)


@pytest.mark.parametrize("label", ["当事人", "劳动者", "申请人", "当前会话当事人"])
def test_dossier_agent_rejects_unresolved_party_placeholders_as_canonical_entities(label: str) -> None:
    conversation_state = {
        "conversation_id": 905,
        "should_build": True,
        "entities": [
            {
                "id": f"conversation:905:entity:{label}",
                "canonical_label": label,
                "entity_type": "person",
                "role": "current_party",
                "confidence": "high",
                "source": "conversation_graph",
                "mentions": [{"raw_label": label, "conversation_id": 905}],
            },
            {
                "id": "conversation:905:entity:company",
                "canonical_label": "A公司",
                "entity_type": "organization",
                "role": "employer",
                "confidence": "high",
                "source": "conversation_graph",
                "mentions": [{"raw_label": "A公司", "conversation_id": 905}],
            },
        ],
        "relations": [],
    }

    dossier_state = build_dossier_entity_state(
        conversation_entity_states=[conversation_state],
        dossier_id=51,
        user_id=7,
        resolution_decisions=[],
    )

    assert _entity_by_label(dossier_state, "A公司")
    assert not _entity_by_label(dossier_state, label)


def test_laborer_alias_does_not_create_separate_dossier_entity() -> None:
    conversation_state = _conversation_state(
        808,
        [
            {
                "id": "person-808",
                "label": "张三",
                "type": "person",
                "conversation_id": 808,
                "properties": {"role": "current_party"},
            },
            {"id": "worker-808", "label": "劳动者", "type": "person", "conversation_id": 808},
            {"id": "company-808", "label": "B公司", "type": "organization", "conversation_id": 808},
        ],
        message="我叫张三，是这起劳动争议里的劳动者。我和B公司签了劳动合同，但B公司一直没有给我缴纳社保。",
    )

    dossier_state = build_dossier_entity_state(
        conversation_entity_states=[conversation_state],
        dossier_id=51,
        user_id=7,
        resolution_decisions=[],
    )

    zhangsan_entities = _entity_by_label(dossier_state, "张三")
    assert len(zhangsan_entities) == 1
    assert "劳动者" in zhangsan_entities[0]["aliases"]
    assert not _entity_by_label(dossier_state, "劳动者")


def test_cross_conversation_same_entity_auto_merges_only_when_obviously_same() -> None:
    state_805 = _named_party_state(805)
    state_806 = _conversation_state(
        806,
        [
            {
                "id": "person-806",
                "label": "张三",
                "type": "person",
                "conversation_id": 806,
                "properties": {"role": "current_party"},
            },
            {"id": "company-806", "label": "B公司", "type": "organization", "conversation_id": 806},
        ],
        message="张三是本案申请人，B公司未缴纳社保。",
    )

    dossier_state = build_dossier_entity_state(
        conversation_entity_states=[state_805, state_806],
        dossier_id=51,
        user_id=7,
        resolution_decisions=[],
    )

    zhangsan_entities = _entity_by_label(dossier_state, "张三")
    assert len(zhangsan_entities) == 1
    assert zhangsan_entities[0]["source_conversation_ids"] == [805, 806]
    assert {item["conversation_id"] for item in zhangsan_entities[0]["source_conversations"]} == {805, 806}
    assert dossier_state["confirmation_tasks"] == []


def test_high_confidence_non_auto_pair_surfaces_priority_confirmation_task() -> None:
    state_805 = _conversation_state(
        805,
        [
            {
                "id": "manager-805",
                "label": "张三",
                "type": "person",
                "conversation_id": 805,
                "properties": {"role": "manager_or_hr"},
            },
        ],
        message="B公司的人事张三通知我不用来了。",
    )
    state_806 = _conversation_state(
        806,
        [
            {
                "id": "manager-806",
                "label": "张三",
                "type": "person",
                "conversation_id": 806,
                "properties": {"role": "representative"},
            },
        ],
        message="第二次沟通还是张三出面。",
    )

    dossier_state = build_dossier_entity_state(
        conversation_entity_states=[state_805, state_806],
        dossier_id=51,
        user_id=7,
        resolution_decisions=[],
    )

    assert len(dossier_state["entities"]) == 2
    tasks = dossier_state["confirmation_tasks"]
    assert len(tasks) == 1
    task = tasks[0]
    assert task["task_type"] == "cross_conversation_same_entity"
    assert task["confidence"] == "high"
    assert task["high_priority"] is True
    assert task["priority"] == "high"
    assert task["auto_merge_recommended"] is False
    assert task["requires_user_confirmation"] is True
    assert task["score"] >= 0.92



def test_cross_conversation_same_entity_candidate_and_confirm_merge() -> None:
    state_901 = _conversation_state(
        901,
        [
            {"id": "company-901", "label": "B公司", "type": "organization", "conversation_id": 901},
        ],
        message="B公司拖欠工资。",
    )
    state_902 = _conversation_state(
        902,
        [
            {"id": "company-902", "label": "B有限公司", "type": "organization", "conversation_id": 902},
        ],
        message="B有限公司未缴纳社保。",
    )

    dossier_state = build_dossier_entity_state(
        conversation_entity_states=[state_901, state_902],
        dossier_id=51,
        user_id=7,
        resolution_decisions=[],
    )

    assert len(dossier_state["entities"]) == 2
    task = dossier_state["confirmation_tasks"][0]
    assert task["task_type"] == "cross_conversation_same_entity"
    assert task["requires_user_confirmation"] is True
    assert set(task["source_conversation_ids"]) == {901, 902}
    assert task["confidence"] == "medium"
    assert task["score"] >= 0.55
    assert task["score"] < 0.92
    assert task["auto_merge_recommended"] is False
    assert task["scoring_reasons"]

    merged_state = build_dossier_entity_state(
        conversation_entity_states=[state_901, state_902],
        dossier_id=51,
        user_id=7,
        resolution_decisions=[{"task_key": task["task_key"], "decision_type": "same"}],
    )

    merged_company_entities = _entity_by_label(merged_state, "B公司")
    assert len(merged_company_entities) == 1
    assert merged_company_entities[0]["source_conversation_ids"] == [901, 902]
    assert {item["conversation_id"] for item in merged_company_entities[0]["source_conversations"]} == {901, 902}
    assert merged_state["confirmation_tasks"] == []


def test_cross_conversation_similar_company_names_surface_candidate() -> None:
    state_901 = _conversation_state(
        901,
        [
            {"id": "company-901", "label": "B公司", "type": "organization", "conversation_id": 901},
        ],
        message="B公司拖欠工资。",
    )
    state_902 = _conversation_state(
        902,
        [
            {"id": "company-902", "label": "B有限公司", "type": "organization", "conversation_id": 902},
        ],
        message="B有限公司未缴纳社保。",
    )

    dossier_state = build_dossier_entity_state(
        conversation_entity_states=[state_901, state_902],
        dossier_id=51,
        user_id=7,
        resolution_decisions=[],
    )

    tasks = dossier_state["confirmation_tasks"]
    assert len(tasks) == 1
    task = tasks[0]
    assert task["task_type"] == "cross_conversation_same_entity"
    assert {task["source_entity"]["label"], task["target_entity"]["label"]} == {"B公司", "B有限公司"}
    assert task["confidence"] == "medium"
    assert task["score"] >= 0.55
    assert task["score"] < 0.92
    assert task["label_similarity"] >= 0.99
    assert task["auto_merge_recommended"] is False
    assert "entity_type_family:organization" in task["scoring_reasons"]


def test_reject_same_entity_keeps_separate_entities_and_suppresses_task() -> None:
    state_901 = _conversation_state(
        901,
        [
            {"id": "company-901", "label": "B公司", "type": "organization", "conversation_id": 901},
        ],
        message="B公司拖欠工资。",
    )
    state_902 = _conversation_state(
        902,
        [
            {"id": "company-902", "label": "B有限公司", "type": "organization", "conversation_id": 902},
        ],
        message="B有限公司未缴纳社保。",
    )
    initial_state = build_dossier_entity_state(
        conversation_entity_states=[state_901, state_902],
        dossier_id=51,
        user_id=7,
        resolution_decisions=[],
    )
    task_key = initial_state["confirmation_tasks"][0]["task_key"]

    rejected_state = build_dossier_entity_state(
        conversation_entity_states=[state_901, state_902],
        dossier_id=51,
        user_id=7,
        resolution_decisions=[{"task_key": task_key, "decision_type": "not_same"}],
    )

    assert len(rejected_state["entities"]) == 2
    assert rejected_state["confirmation_tasks"] == []


def test_person_to_company_never_identity_candidate() -> None:
    state = _conversation_state(
        806,
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
        message="B公司的人事王经理口头通知我不用来了。",
    )

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
    conversation_state = _conversation_state(
        806,
        [
            {"id": "self-806", "label": "我", "type": "person", "conversation_id": 806},
            {"id": "company-806", "label": "B公司", "type": "organization", "conversation_id": 806},
        ],
        message="我和B公司签了劳动合同。",
    )
    assert not _entity_by_label({"entities": conversation_state["entities"]}, "当前会话当事人")
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


def test_consultation_topic_subject_stays_out_of_dossier_graph() -> None:
    conversation_state = _conversation_state(
        925,
        [
            {
                "id": "person-925",
                "label": "张三",
                "type": "person",
                "conversation_id": 925,
                "properties": {"role": "current_party"},
            },
            {"id": "company-925", "label": "蓝海科技", "type": "organization", "conversation_id": 925},
            {"id": "topic-925", "label": "劳动纠纷", "type": "主体", "conversation_id": 925},
        ],
        message="我想咨询一个劳动纠纷。我叫张三，在蓝海科技工作。",
    )

    dossier_state = build_dossier_entity_state(
        conversation_entity_states=[conversation_state],
        dossier_id=51,
        user_id=7,
        resolution_decisions=[],
    )
    graph = coordination_graph_from_dossier_entity_state(dossier_state)

    assert _entity_by_label(dossier_state, "张三")
    assert _entity_by_label(dossier_state, "蓝海科技")
    assert not _entity_by_label(dossier_state, "劳动纠纷")
    assert not any(entity["label"] == "劳动纠纷" for entity in graph["entities"])


def test_confirmation_task_entities_include_guard_fields() -> None:
    state_901 = _conversation_state(
        901,
        [
            {"id": "company-901", "label": "B公司", "type": "organization", "conversation_id": 901},
        ],
        message="B公司拖欠工资。",
    )
    state_902 = _conversation_state(
        902,
        [
            {"id": "company-902", "label": "B有限公司", "type": "organization", "conversation_id": 902},
        ],
        message="B有限公司未缴纳社保。",
    )

    dossier_state = build_dossier_entity_state(
        conversation_entity_states=[state_901, state_902],
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


def test_dossier_entity_state_does_not_extract_entities_from_message_text() -> None:
    conversation_state = build_conversation_entity_state(
        message_text="我叫张三，是这起劳动争议里的劳动者。我和B公司签了劳动合同，但B公司一直没有给我缴纳社保。",
        raw_graph_payload=_graph(
            [
                {"id": "person-813", "label": "张三", "type": "person", "conversation_id": 813},
                {"id": "company-813", "label": "B公司", "type": "organization", "conversation_id": 813},
            ]
        ),
        conversation_id=813,
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )

    dossier_state = build_dossier_entity_state(
        conversation_entity_states=[conversation_state],
        dossier_id=51,
        user_id=7,
        resolution_decisions=[],
    )

    assert _entity_by_label(dossier_state, "B公司")
    assert not _entity_by_label(dossier_state, "但B公司")


def test_dynamic_case_non_subject_nodes_do_not_enter_dossier_canonical_graph() -> None:
    conversation_state = _conversation_state(
        901,
        [
            {
                "id": "person-901",
                "label": "张三",
                "type": "DynamicCaseEntity",
                "conversation_id": 901,
                "properties": {"category": "主体", "role": "current_party"},
            },
            {
                "id": "company-901",
                "label": "蓝海科技",
                "type": "DynamicCaseEntity",
                "conversation_id": 901,
                "properties": {"category": "主体"},
            },
            {
                "id": "arrears-901",
                "label": "欠薪",
                "type": "DynamicCaseEntity",
                "conversation_id": 901,
                "properties": {"category": "后果"},
            },
            {
                "id": "dismissal-901",
                "label": "违法辞退",
                "type": "DynamicCaseEntity",
                "conversation_id": 901,
                "properties": {"category": "事件"},
            },
            {
                "id": "arbitration-901",
                "label": "仲裁申请",
                "type": "DynamicCaseEntity",
                "conversation_id": 901,
                "properties": {"category": "客体"},
            },
        ],
        message="我叫张三，在蓝海科技做后端开发，公司拖欠我工资约6万元，后来违法辞退我，我准备申请仲裁。",
    )

    dossier_state = build_dossier_entity_state(
        conversation_entity_states=[conversation_state],
        dossier_id=51,
        user_id=7,
        resolution_decisions=[],
    )
    graph = coordination_graph_from_dossier_entity_state(dossier_state)

    labels = {entity["canonical_label"] for entity in dossier_state["entities"]}
    graph_labels = {entity["label"] for entity in graph["entities"]}
    assert {"张三", "蓝海科技"}.issubset(labels)
    assert "欠薪" not in labels
    assert "违法辞退" not in labels
    assert "仲裁申请" not in labels
    assert "欠薪" not in graph_labels
    assert "违法辞退" not in graph_labels
    assert "仲裁申请" not in graph_labels


def test_dossier_entity_state_filters_legacy_case_graph_categories() -> None:
    conversation_state = {
        "conversation_id": 902,
        "should_build": True,
        "entities": [
            {
                "id": "person-902",
                "canonical_label": "张三",
                "entity_type": "主体",
                "role": "current_party",
                "source": "conversation_graph",
                "mentions": [{"conversation_id": 902, "raw_label": "张三", "raw_node_id": "person-902"}],
            },
            {
                "id": "company-902",
                "canonical_label": "蓝海科技",
                "entity_type": "主体",
                "source": "conversation_graph",
                "mentions": [{"conversation_id": 902, "raw_label": "蓝海科技", "raw_node_id": "company-902"}],
            },
            {
                "id": "legacy-company-902",
                "canonical_label": "旧图谱公司",
                "entity_type": "DynamicCaseEntity",
                "source": "conversation_graph",
                "mentions": [{"conversation_id": 902, "raw_label": "旧图谱公司", "raw_node_id": "legacy-company-902"}],
            },
            {
                "id": "arrears-902",
                "canonical_label": "欠薪",
                "entity_type": "后果",
                "source": "conversation_graph",
                "mentions": [{"conversation_id": 902, "raw_label": "欠薪", "raw_node_id": "arrears-902"}],
            },
            {
                "id": "dismissal-902",
                "canonical_label": "违法辞退",
                "entity_type": "事件",
                "source": "conversation_graph",
                "mentions": [{"conversation_id": 902, "raw_label": "违法辞退", "raw_node_id": "dismissal-902"}],
            },
            {
                "id": "arbitration-902",
                "canonical_label": "仲裁申请",
                "entity_type": "客体",
                "source": "conversation_graph",
                "mentions": [{"conversation_id": 902, "raw_label": "仲裁申请", "raw_node_id": "arbitration-902"}],
            },
        ],
        "relations": [],
    }

    dossier_state = build_dossier_entity_state(
        conversation_entity_states=[conversation_state],
        dossier_id=51,
        user_id=7,
        resolution_decisions=[],
    )

    labels = {entity["canonical_label"] for entity in dossier_state["entities"]}
    assert {"张三", "蓝海科技"}.issubset(labels)
    assert "旧图谱公司" not in labels
    assert "欠薪" not in labels
    assert "违法辞退" not in labels
    assert "仲裁申请" not in labels


@pytest.mark.parametrize(
    ("label", "entity_type"),
    [
        ("欠薪", "后果"),
        ("经济损失", "后果"),
        ("双倍工资赔偿", "后果"),
        ("违法辞退", "事件"),
        ("仲裁申请", "客体"),
        ("工资流水", "客体"),
        ("微信聊天记录", "客体"),
        ("劳动合同", "contract"),
        ("2023年5月", "time"),
    ],
)
def test_dossier_entity_state_filters_non_responsible_entity_families(
    label: str,
    entity_type: str,
) -> None:
    conversation_state = {
        "conversation_id": 903,
        "should_build": True,
        "entities": [
            {
                "id": "person-903",
                "canonical_label": "张三",
                "entity_type": "person",
                "role": "current_party",
                "source": "conversation_graph",
                "mentions": [{"conversation_id": 903, "raw_label": "张三", "raw_node_id": "person-903"}],
            },
            {
                "id": "company-903",
                "canonical_label": "蓝海科技",
                "entity_type": "organization",
                "source": "conversation_graph",
                "mentions": [{"conversation_id": 903, "raw_label": "蓝海科技", "raw_node_id": "company-903"}],
            },
            {
                "id": f"fact-903-{label}",
                "canonical_label": label,
                "entity_type": entity_type,
                "source": "conversation_graph",
                "mentions": [{"conversation_id": 903, "raw_label": label, "raw_node_id": f"fact-903-{label}"}],
            },
        ],
        "relations": [],
    }

    dossier_state = build_dossier_entity_state(
        conversation_entity_states=[conversation_state],
        dossier_id=51,
        user_id=7,
        resolution_decisions=[],
    )
    graph = coordination_graph_from_dossier_entity_state(dossier_state)

    labels = {entity["canonical_label"] for entity in dossier_state["entities"]}
    graph_labels = {entity["label"] for entity in graph["entities"]}
    assert {"张三", "蓝海科技"}.issubset(labels)
    assert label not in labels
    assert label not in graph_labels


def test_fallback_rules_entities_do_not_enter_dossier_canonical_graph() -> None:
    conversation_state = build_conversation_entity_state(
        message_text="我叫张三，是这起劳动争议里的劳动者。我和B公司签了劳动合同。",
        conversation_id=814,
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )

    dossier_state = build_dossier_entity_state(
        conversation_entity_states=[conversation_state],
        dossier_id=51,
        user_id=7,
        resolution_decisions=[],
    )

    assert conversation_state["entity_source"] == "fallback_rules"
    assert dossier_state["entities"] == []
    assert coordination_graph_from_dossier_entity_state(dossier_state)["entities"] == []


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
