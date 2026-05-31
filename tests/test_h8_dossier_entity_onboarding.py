from __future__ import annotations

from app.services.dossier_entity_onboarding_agent import (
    build_dossier_entity_onboarding_decision,
)
from app.services.dossier_intelligence import _build_party_naming_task


def _raw_graph(nodes: list[dict], edges: list[dict] | None = None) -> dict:
    return {
        "status": "success",
        "dossier_id": 88,
        "participant_aliases": {},
        "confirmed_participants": [],
        "participant_mappings": [],
        "graph": {
            "nodes": nodes,
            "edges": edges or [],
            "stats": {
                "conversation_count": 1,
                "node_count": len(nodes),
                "edge_count": len(edges or []),
            },
        },
    }


def test_explicit_user_name_resolves_current_party_without_blank_party_naming() -> None:
    raw_graph_payload = _raw_graph(
        [
            {"id": "self-1", "label": "我", "type": "person", "conversation_id": 101},
            {"id": "person-1", "label": "张三", "type": "person", "conversation_id": 101},
        ]
    )

    decision = build_dossier_entity_onboarding_decision(
        message_text="我叫张三，是这起劳动争议里的劳动者。",
        raw_graph_payload=raw_graph_payload,
        conversation_id=101,
        dossier_id=88,
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )
    task = _build_party_naming_task(
        raw_graph_payload,
        {"nodes": []},
        onboarding_decision=decision,
        conversation_id=101,
    )

    assert decision["current_party_resolution"]["status"] == "resolved"
    assert decision["current_party_resolution"]["resolved_name"] == "张三"
    assert decision["needs_current_party_confirmation"] is False
    assert decision["party_naming_decision"]["should_create_party_naming"] is False
    assert task is None


def test_first_person_fact_needs_current_party_confirmation_and_keeps_company_responsible_entity() -> None:
    raw_graph_payload = _raw_graph(
        [
            {"id": "self-2", "label": "我", "type": "person", "conversation_id": 102},
            {"id": "company-2", "label": "B公司", "type": "organization", "conversation_id": 102},
            {"id": "contract-2", "label": "劳动合同", "type": "document", "conversation_id": 102},
        ]
    )

    decision = build_dossier_entity_onboarding_decision(
        message_text="我和B公司签了劳动合同，但B公司一直没给我缴纳社保。",
        raw_graph_payload=raw_graph_payload,
        conversation_id=102,
        dossier_id=88,
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )
    task = _build_party_naming_task(
        raw_graph_payload,
        {"nodes": []},
        onboarding_decision=decision,
        conversation_id=102,
    )

    assert decision["current_party_resolution"]["status"] == "needs_confirmation"
    assert decision["needs_current_party_confirmation"] is True
    assert decision["party_naming_decision"]["should_create_party_naming"] is True
    assert {entity["label"] for entity in decision["responsible_entities"]} >= {"B公司"}
    assert all(
        entity["needs_naming"] is False
        for entity in decision["responsible_entities"]
        if entity["label"] == "B公司"
    )
    assert task is not None
    assert task["task_type"] == "party_naming"
    assert task["current_label"] == "我"


def test_third_party_retelling_does_not_create_user_self_anchor_party_naming() -> None:
    raw_graph_payload = _raw_graph(
        [
            {"id": "party-3", "label": "当事人", "type": "person", "conversation_id": 103},
            {"id": "zhangsan-3", "label": "张三", "type": "person", "conversation_id": 103},
            {"id": "company-3", "label": "B公司", "type": "organization", "conversation_id": 103},
        ]
    )

    decision = build_dossier_entity_onboarding_decision(
        message_text="我的委托人张三称，B公司未给其缴纳社保。",
        raw_graph_payload=raw_graph_payload,
        conversation_id=103,
        dossier_id=88,
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )
    task = _build_party_naming_task(
        raw_graph_payload,
        {"nodes": []},
        onboarding_decision=decision,
        conversation_id=103,
    )

    assert decision["third_party_retelling"]["is_third_party"] is True
    assert decision["third_party_retelling"]["represented_party_name"] == "张三"
    assert decision["current_party_resolution"]["status"] == "not_applicable"
    assert decision["party_naming_decision"]["should_create_party_naming"] is False
    assert task is None


def test_manager_and_company_generate_responsible_entities_and_affiliation_candidate() -> None:
    raw_graph_payload = _raw_graph(
        [
            {"id": "company-4", "label": "B公司", "type": "organization", "conversation_id": 104},
            {"id": "manager-4", "label": "王经理", "type": "person", "conversation_id": 104},
            {"id": "notice-4", "label": "口头通知不用来了", "type": "event", "conversation_id": 104},
        ],
        [
            {
                "id": "edge-4",
                "source": "manager-4",
                "target": "notice-4",
                "label": "口头通知",
                "properties": {"conversation_id": 104},
            }
        ],
    )

    decision = build_dossier_entity_onboarding_decision(
        message_text="B公司的人事王经理口头通知我不用来了。",
        raw_graph_payload=raw_graph_payload,
        conversation_id=104,
        dossier_id=88,
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )

    entities = {entity["label"]: entity for entity in decision["responsible_entities"]}
    assert entities["B公司"]["entity_type"] == "organization"
    assert entities["王经理"]["entity_type"] in {"person", "role"}
    assert all(
        not (
            candidate.get("source_label") == "王经理"
            and candidate.get("target_label") == "B公司"
            and candidate.get("candidate_type") == "same_entity"
        )
        for candidate in decision["identity_candidates"]
    )
    assert any(
        candidate["source_label"] == "王经理"
        and candidate["target_label"] == "B公司"
        for candidate in decision["affiliation_candidates"]
    )


def test_legal_qa_does_not_trigger_onboarding_or_party_naming() -> None:
    raw_graph_payload = _raw_graph(
        [
            {"id": "self-5", "label": "我", "type": "person", "conversation_id": 105},
        ]
    )

    decision = build_dossier_entity_onboarding_decision(
        message_text="劳动合同法第八十七条规定了什么？",
        message_understanding={"intent": "legal_question", "route_hint": "LEGAL_QA"},
        raw_graph_payload=raw_graph_payload,
        conversation_id=105,
        dossier_id=88,
        conversation_mode="LEGAL_QA",
        persistence_consent="unknown",
    )
    task = _build_party_naming_task(
        raw_graph_payload,
        {"nodes": []},
        onboarding_decision=decision,
        conversation_id=105,
    )

    assert decision["should_run_onboarding"] is False
    assert decision["current_party_resolution"]["status"] == "not_applicable"
    assert decision["party_naming_decision"]["should_create_party_naming"] is False
    assert task is None
