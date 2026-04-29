from __future__ import annotations

import asyncio
import json
from datetime import datetime

import pytest

from app.models import Dossier, User
from app.services import dossier_intelligence


class DummyDB:
    pass


def _make_user(user_id: int = 7) -> User:
    return User(id=user_id, username=f"user-{user_id}", hashed_password="hashed")


def _make_dossier() -> Dossier:
    return Dossier(
        id=51,
        user_id=7,
        title="劳动争议案卷",
        case_type="劳动争议",
        summary="围绕用工关系和通知事实存在争议",
        auto_summary="围绕用工关系和通知事实存在争议",
        auto_summary_updated_at=datetime(2026, 4, 29, 9, 30, 0),
        status="active",
        updated_at=datetime(2026, 4, 29, 10, 0, 0),
    )


def _node(
    label: str,
    *,
    conversation_id: int = 11,
    graph_node_id: str | None = None,
    graph_node_type: str = "person",
    is_role_person: bool = False,
    is_self_anchor: bool = False,
    is_organization: bool = False,
) -> dict:
    return {
        "conversation_id": conversation_id,
        "graph_node_id": graph_node_id or f"{conversation_id}:{label}",
        "raw_label": label,
        "graph_node_label": label,
        "graph_node_type": graph_node_type,
        "is_role_person": is_role_person,
        "is_self_anchor": is_self_anchor,
        "is_organization": is_organization,
    }


def _candidate(
    candidate_key: str,
    *,
    task_type_rule_hint: str,
    subject: dict,
    object_node: dict | None = None,
    prior_decisions: list[dict] | None = None,
    co_occurrence_relations: list[str] | None = None,
) -> dict:
    return {
        "candidate_key": candidate_key,
        "task_type_rule_hint": task_type_rule_hint,
        "subject": subject,
        "object": object_node,
        "evidence": {
            "co_occurrence_relations": co_occurrence_relations or [],
            "shared_conversation_ids": [subject.get("conversation_id")],
            "participant_mappings": [],
            "prior_decisions": prior_decisions or [],
        },
    }


def _evidence(*candidates: dict, recent_facts: list[str] | None = None) -> dict:
    return {
        "dossier_id": 51,
        "candidates": list(candidates),
        "confirmed_participants": [],
        "recent_facts": recent_facts or [],
    }


def _llm_result(tasks: list[dict], *, provider: str = "openai", model: str = "gpt-5.1") -> dict[str, str]:
    return {
        "provider": provider,
        "model": model,
        "content": json.dumps({"tasks": tasks}, ensure_ascii=False),
    }


def _patch_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        assert user_id == 7
        assert dossier_id == 51
        return _make_dossier()

    monkeypatch.setattr(dossier_intelligence, "get_dossier_for_user", fake_get_dossier_for_user)


def _patch_display_graph_context(
    monkeypatch: pytest.MonkeyPatch,
    *,
    raw_nodes: list[dict] | None = None,
    display_nodes: list[dict] | None = None,
    participant_candidates: list[dict] | None = None,
    confirmed_participants: list[dict] | None = None,
    participant_mappings: list[dict] | None = None,
    entity_resolution_decisions: list[dict] | None = None,
) -> None:
    async def fake_load_display_graph_context(db, *, user_id: int, dossier: Dossier):
        assert user_id == 7
        assert dossier.id == 51
        return (
            {
                "participant_aliases": {},
                "participant_candidates": participant_candidates or [],
                "confirmed_participants": confirmed_participants or [],
                "participant_mappings": participant_mappings or [],
                "entity_resolution_decisions": entity_resolution_decisions or [],
                "graph": {
                    "stats": {"conversation_count": 2, "node_count": len(raw_nodes or []), "edge_count": 0},
                    "nodes": raw_nodes or [],
                    "edges": [],
                },
            },
            {
                "nodes": display_nodes or [],
                "edges": [],
                "stats": {
                    "display_node_count": len(display_nodes or []),
                    "merged_participant_count": 0,
                    "pending_confirmation_count": len(display_nodes or []),
                },
            },
        )

    monkeypatch.setattr(
        dossier_intelligence,
        "_load_display_graph_context",
        fake_load_display_graph_context,
    )


@pytest.mark.parametrize(
    ("label", "object_node", "expected_task_type"),
    [
        ("王经理", _node("B公司", graph_node_type="organization", is_organization=True), "affiliation_relation"),
        ("李主管", None, "relation_only"),
        ("HR", None, "relation_only"),
    ],
)
def test_classifier_role_person_never_survives_as_identity(
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    object_node: dict | None,
    expected_task_type: str,
) -> None:
    evidence = _evidence(
        _candidate(
            f"cand:{label}",
            task_type_rule_hint="affiliation_relation" if object_node else "relation_only",
            subject=_node(label, is_role_person=True),
            object_node=object_node,
            co_occurrence_relations=["角色节点与公司共现"],
        )
    )

    async def fake_llm(*, messages, provider_hint=None):
        return _llm_result(
            [
                {
                    "task_type": "identity",
                    "confidence": "high",
                    "should_show_to_user": True,
                    "reason": "模型初判同一主体",
                    "subject": evidence["candidates"][0]["subject"],
                    "object": evidence["candidates"][0]["object"],
                    "evidence_summary": "角色与个人共现",
                    "candidate_key": evidence["candidates"][0]["candidate_key"],
                }
            ]
        )

    monkeypatch.setattr(dossier_intelligence, "_call_dossier_intelligence_classifier_llm", fake_llm)

    result = asyncio.run(dossier_intelligence._classify_candidates_with_agent(evidence))

    assert result["fallback_used"] is False
    assert result["tasks"][0]["task_type"] == expected_task_type


def test_classifier_cross_conversation_same_name_organizations_still_identity_high(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = _evidence(
        _candidate(
            "cand:a-company",
            task_type_rule_hint="identity",
            subject=_node("A公司", conversation_id=11, graph_node_id="conv1-a", graph_node_type="organization", is_organization=True),
            object_node=_node("A公司", conversation_id=12, graph_node_id="conv2-a", graph_node_type="organization", is_organization=True),
        ),
        _candidate(
            "cand:b-company",
            task_type_rule_hint="identity",
            subject=_node("B公司", conversation_id=21, graph_node_id="conv1-b", graph_node_type="organization", is_organization=True),
            object_node=_node("B公司", conversation_id=22, graph_node_id="conv2-b", graph_node_type="organization", is_organization=True),
        ),
    )

    async def fake_llm(*, messages, provider_hint=None):
        return _llm_result(
            [
                {
                    "task_type": "identity",
                    "confidence": "high",
                    "should_show_to_user": True,
                    "reason": "跨会话同名公司",
                    "subject": candidate["subject"],
                    "object": candidate["object"],
                    "evidence_summary": "同名组织跨会话出现",
                    "candidate_key": candidate["candidate_key"],
                }
                for candidate in evidence["candidates"]
            ]
        )

    monkeypatch.setattr(dossier_intelligence, "_call_dossier_intelligence_classifier_llm", fake_llm)

    result = asyncio.run(dossier_intelligence._classify_candidates_with_agent(evidence))

    assert [task["task_type"] for task in result["tasks"]] == ["identity", "identity"]
    assert all(task["confidence"] == "high" for task in result["tasks"])


@pytest.mark.parametrize("label", ["当事人", "我", "本人", "劳动者本人"])
def test_classifier_self_anchor_defaults_to_party_naming(
    monkeypatch: pytest.MonkeyPatch,
    label: str,
) -> None:
    evidence = _evidence(
        _candidate(
            f"cand:self:{label}",
            task_type_rule_hint="party_naming",
            subject=_node(label, is_self_anchor=True),
        )
    )

    async def fake_llm(*, messages, provider_hint=None):
        return _llm_result(
            [
                {
                    "task_type": "identity",
                    "confidence": "medium",
                    "should_show_to_user": True,
                    "reason": "模型误判为 identity",
                    "subject": evidence["candidates"][0]["subject"],
                    "object": None,
                    "evidence_summary": "自我指代",
                    "candidate_key": evidence["candidates"][0]["candidate_key"],
                }
            ]
        )

    monkeypatch.setattr(dossier_intelligence, "_call_dossier_intelligence_classifier_llm", fake_llm)

    result = asyncio.run(dossier_intelligence._classify_candidates_with_agent(evidence))

    assert result["tasks"][0]["task_type"] == "party_naming"


def test_classifier_fact_object_nodes_are_hidden_not_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = _evidence(
        _candidate(
            "cand:confirm-book",
            task_type_rule_hint="not_actionable",
            subject=_node("确认书", graph_node_type="document"),
        ),
        _candidate(
            "cand:salary",
            task_type_rule_hint="not_actionable",
            subject=_node("月薪8000", graph_node_type="amount"),
        ),
        _candidate(
            "cand:date",
            task_type_rule_hint="not_actionable",
            subject=_node("2024年4月", graph_node_type="time"),
        ),
    )

    async def fake_llm(*, messages, provider_hint=None):
        return _llm_result(
            [
                {
                    "task_type": "identity",
                    "confidence": "low",
                    "should_show_to_user": True,
                    "reason": "模型误判",
                    "subject": candidate["subject"],
                    "object": None,
                    "evidence_summary": "事实对象",
                    "candidate_key": candidate["candidate_key"],
                }
                for candidate in evidence["candidates"]
            ]
        )

    monkeypatch.setattr(dossier_intelligence, "_call_dossier_intelligence_classifier_llm", fake_llm)

    result = asyncio.run(dossier_intelligence._classify_candidates_with_agent(evidence))

    assert [task["task_type"] for task in result["tasks"]] == ["not_actionable", "not_actionable", "not_actionable"]
    assert all(task["should_show_to_user"] is False for task in result["tasks"])


def test_classifier_prior_decisions_block_identity_and_affiliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = _evidence(
        _candidate(
            "cand:not-same",
            task_type_rule_hint="identity",
            subject=_node("A公司", conversation_id=11, graph_node_id="conv1-a", graph_node_type="organization", is_organization=True),
            object_node=_node("A公司", conversation_id=12, graph_node_id="conv2-a", graph_node_type="organization", is_organization=True),
            prior_decisions=[{"decision_type": "not_same", "subject": "cross_conversation_identity"}],
        ),
        _candidate(
            "cand:same",
            task_type_rule_hint="identity",
            subject=_node("B公司", conversation_id=21, graph_node_id="conv1-b", graph_node_type="organization", is_organization=True),
            object_node=_node("B公司", conversation_id=22, graph_node_id="conv2-b", graph_node_type="organization", is_organization=True),
            prior_decisions=[{"decision_type": "same", "subject": "cross_conversation_identity"}],
        ),
        _candidate(
            "cand:not-actionable",
            task_type_rule_hint="affiliation_relation",
            subject=_node("王经理", is_role_person=True),
            object_node=_node("B公司", graph_node_type="organization", is_organization=True),
            prior_decisions=[{"decision_type": "not_actionable", "subject": "cross_conversation_identity"}],
        ),
    )

    async def fake_llm(*, messages, provider_hint=None):
        return _llm_result(
            [
                {
                    "task_type": "identity" if index < 2 else "affiliation_relation",
                    "confidence": "high",
                    "should_show_to_user": True,
                    "reason": "模型生成",
                    "subject": candidate["subject"],
                    "object": candidate["object"],
                    "evidence_summary": "已有历史决策",
                    "candidate_key": candidate["candidate_key"],
                }
                for index, candidate in enumerate(evidence["candidates"])
            ]
        )

    monkeypatch.setattr(dossier_intelligence, "_call_dossier_intelligence_classifier_llm", fake_llm)

    result = asyncio.run(dossier_intelligence._classify_candidates_with_agent(evidence))

    assert result["tasks"] == []


def test_classifier_fallback_on_llm_failure_uses_rule_buckets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = _evidence(
        _candidate(
            "cand:a-company",
            task_type_rule_hint="identity",
            subject=_node("A公司", conversation_id=11, graph_node_id="conv1-a", graph_node_type="organization", is_organization=True),
            object_node=_node("A公司", conversation_id=12, graph_node_id="conv2-a", graph_node_type="organization", is_organization=True),
        ),
        _candidate(
            "cand:wang-manager",
            task_type_rule_hint="affiliation_relation",
            subject=_node("王经理", is_role_person=True),
            object_node=_node("B公司", graph_node_type="organization", is_organization=True),
        ),
    )

    async def fake_llm(*, messages, provider_hint=None):
        raise RuntimeError("network unavailable")

    monkeypatch.setattr(dossier_intelligence, "_call_dossier_intelligence_classifier_llm", fake_llm)

    result = asyncio.run(dossier_intelligence._classify_candidates_with_agent(evidence))

    assert result["provider"] == "rules"
    assert result["fallback_used"] is True
    assert result["fallback_reason"] == "llm_call_failed"
    assert [task["task_type"] for task in result["tasks"]] == ["identity", "affiliation_relation"]


def test_classifier_fallback_on_invalid_json_uses_rule_buckets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = _evidence(
        _candidate(
            "cand:hr-role",
            task_type_rule_hint="relation_only",
            subject=_node("HR王某", is_role_person=True),
            object_node=None,
        )
    )

    async def fake_llm(*, messages, provider_hint=None):
        return {"provider": "openai", "model": "gpt-5.1", "content": "not-json"}

    monkeypatch.setattr(dossier_intelligence, "_call_dossier_intelligence_classifier_llm", fake_llm)

    result = asyncio.run(dossier_intelligence._classify_candidates_with_agent(evidence))

    assert result["provider"] == "rules"
    assert result["fallback_used"] is True
    assert result["fallback_reason"] == "invalid_llm_json"
    assert result["tasks"][0]["task_type"] == "relation_only"


@pytest.mark.parametrize(
    "fact_text",
    [
        "王经理通知我不用来了。",
        "HR王某口头要求我离职。",
    ],
)
def test_classifier_paraphrase_examples_do_not_become_identity(
    monkeypatch: pytest.MonkeyPatch,
    fact_text: str,
) -> None:
    evidence = _evidence(
        _candidate(
            "cand:role-fact",
            task_type_rule_hint="affiliation_relation",
            subject=_node("王经理", is_role_person=True),
            object_node=_node("B公司", graph_node_type="organization", is_organization=True),
        ),
        recent_facts=[fact_text],
    )

    async def fake_llm(*, messages, provider_hint=None):
        return _llm_result(
            [
                {
                    "task_type": "affiliation_relation",
                    "confidence": "medium",
                    "should_show_to_user": True,
                    "reason": "角色主体与公司存在管理/代表关系线索",
                    "subject": evidence["candidates"][0]["subject"],
                    "object": evidence["candidates"][0]["object"],
                    "evidence_summary": fact_text,
                    "candidate_key": evidence["candidates"][0]["candidate_key"],
                }
            ]
        )

    monkeypatch.setattr(dossier_intelligence, "_call_dossier_intelligence_classifier_llm", fake_llm)

    result = asyncio.run(dossier_intelligence._classify_candidates_with_agent(evidence))

    assert result["tasks"][0]["task_type"] == "affiliation_relation"


def test_build_dossier_intelligence_adds_classifier_confirmation_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_owner(monkeypatch)
    _patch_display_graph_context(
        monkeypatch,
        raw_nodes=[
            {"id": "conv1-a", "label": "A公司", "type": "organization", "conversation_id": 11},
            {"id": "conv2-a", "label": "A公司", "type": "organization", "conversation_id": 12},
        ],
        display_nodes=[
            {
                "id": "raw:conv1-a",
                "kind": "raw_entity",
                "display_label": "A公司",
                "raw_labels": ["A公司"],
                "raw_node_ids": ["conv1-a"],
                "source_conversation_ids": [11],
            }
        ],
        participant_candidates=[
            {
                "candidate_key": "cand:a-company",
                "task_key": "cand:a-company",
                "task_type": "identity",
                "confidence": "high",
                "source_node": {
                    "conversation_id": 11,
                    "graph_node_id": "conv1-a",
                    "raw_label": "A公司",
                    "graph_node_label": "A公司",
                    "graph_node_type": "organization",
                },
                "target_node": {
                    "conversation_id": 12,
                    "graph_node_id": "conv2-a",
                    "raw_label": "A公司",
                    "graph_node_label": "A公司",
                    "graph_node_type": "organization",
                },
            }
        ],
    )

    async def fake_llm(*, messages, provider_hint=None):
        return _llm_result(
            [
                {
                    "task_type": "identity",
                    "confidence": "high",
                    "should_show_to_user": True,
                    "reason": "跨会话同名组织",
                    "subject": _node("A公司", conversation_id=11, graph_node_id="conv1-a", graph_node_type="organization", is_organization=True),
                    "object": _node("A公司", conversation_id=12, graph_node_id="conv2-a", graph_node_type="organization", is_organization=True),
                    "evidence_summary": "A公司在两个会话中同名出现",
                    "candidate_key": "cand:a-company",
                }
            ]
        )

    monkeypatch.setattr(dossier_intelligence, "_call_dossier_intelligence_classifier_llm", fake_llm)

    result = asyncio.run(
        dossier_intelligence.build_dossier_intelligence(
            DummyDB(),
            current_user=_make_user(),
            dossier_id=51,
        )
    )

    identity_task = next(task for task in result["confirmation_tasks"] if task.get("task_type") == "identity")
    assert identity_task["origin"] == "intelligence_classifier"
    assert identity_task["should_show_to_user"] is True
    assert result["candidate_classifier"]["provider"] == "openai"
    assert result["candidate_classifier"]["fallback_used"] is False
    assert "build_candidate_evidence" in [step["step"] for step in result["trace"]["steps"]]
    assert "classify_candidates_with_agent" in [step["step"] for step in result["trace"]["steps"]]


def test_build_dossier_intelligence_dedupes_self_anchor_rule_over_classifier_party_naming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_owner(monkeypatch)
    _patch_display_graph_context(
        monkeypatch,
        raw_nodes=[
            {"id": "self-1", "label": "当事人", "type": "person", "conversation_id": 11},
        ],
        display_nodes=[
            {
                "id": "raw:self-1",
                "kind": "raw_entity",
                "display_label": "当事人",
                "raw_labels": ["当事人"],
                "raw_node_ids": ["self-1"],
                "source_conversation_ids": [11],
            }
        ],
        participant_candidates=[
            {
                "candidate_key": "cand:self-anchor",
                "task_key": "cand:self-anchor",
                "task_type": "party_naming",
                "raw_label": "当事人",
                "mentions": [
                    {
                        "conversation_id": 11,
                        "graph_node_id": "self-1",
                        "raw_label": "当事人",
                        "graph_node_label": "当事人",
                        "graph_node_type": "person",
                    }
                ],
            }
        ],
    )

    async def fake_llm(*, messages, provider_hint=None):
        return _llm_result(
            [
                {
                    "task_type": "party_naming",
                    "confidence": "high",
                    "should_show_to_user": True,
                    "reason": "需要确认当事人的对外称呼",
                    "subject": _node("当事人", graph_node_id="self-1", is_self_anchor=True),
                    "object": None,
                    "evidence_summary": "案卷出现当事人自我指代",
                    "candidate_key": "cand:self-anchor",
                }
            ]
        )

    monkeypatch.setattr(dossier_intelligence, "_call_dossier_intelligence_classifier_llm", fake_llm)

    result = asyncio.run(
        dossier_intelligence.build_dossier_intelligence(
            DummyDB(),
            current_user=_make_user(),
            dossier_id=51,
        )
    )

    party_naming_tasks = [task for task in result["confirmation_tasks"] if task.get("task_type") == "party_naming"]
    assert len(party_naming_tasks) == 1
    assert party_naming_tasks[0]["origin"] == "self_anchor_rule"
