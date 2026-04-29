from __future__ import annotations

import asyncio
import importlib
import sys
from datetime import datetime
from types import ModuleType

import pytest

from app.core.security import AuthenticatedUser
from app.models import Dossier, DossierEntityResolutionDecision
from app.services.conversation_store import (
    build_cross_conversation_resolution_pair_hash,
    build_cross_conversation_resolution_pair_key,
    build_dossier_participant_candidates,
)


def _install_dossiers_api_stubs() -> None:
    fake_dossier_summary = ModuleType("app.services.dossier_summary")

    async def _generate_dossier_auto_summary(*args, **kwargs):
        return {"auto_summary": "", "auto_summary_source_count": 0}

    fake_dossier_summary.generate_dossier_auto_summary = _generate_dossier_auto_summary
    sys.modules["app.services.dossier_summary"] = fake_dossier_summary


def _load_dossiers_api_module():
    _install_dossiers_api_stubs()
    sys.modules.pop("app.api.dossiers", None)
    module = importlib.import_module("app.api.dossiers")
    sys.modules.pop("app.services.dossier_summary", None)
    return module


class DummyDB:
    def add(self, obj) -> None:
        return None

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def refresh(self, obj) -> None:
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime(2026, 1, 1, 0, 0, 0)
        return None


def _cross_decision_payload(
    *,
    decision_type: str,
    left_graph_node_id: str,
    right_graph_node_id: str,
) -> dict[str, object]:
    pair_key = build_cross_conversation_resolution_pair_key(
        left_graph_node_id,
        right_graph_node_id,
    )
    return {
        "subject": "cross_conversation_identity",
        "decision_type": decision_type,
        "source_graph_node_id": pair_key,
        "target_participant_id": build_cross_conversation_resolution_pair_hash(pair_key),
    }


def test_cross_conversation_not_same_decision_filters_pair_and_reverse_pair() -> None:
    candidates = build_dossier_participant_candidates(
        graph_payload={
            "graph": {
                "nodes": [
                    {"id": "conv2-a", "label": "A公司", "type": "organization", "conversation_id": 12},
                    {"id": "conv1-a", "label": "A公司", "type": "organization", "conversation_id": 11},
                ]
            }
        },
        participants=[],
        confirmed_mentions=[],
        participant_aliases={},
        resolution_decisions=[
            _cross_decision_payload(
                decision_type="not_same",
                left_graph_node_id="conv1-a",
                right_graph_node_id="conv2-a",
            )
        ],
    )

    cross_candidates = [
        candidate
        for candidate in candidates
        if candidate.get("task_type") == "identity"
    ]
    assert cross_candidates == []


def test_cross_conversation_same_label_company_candidate_still_generates_without_decision() -> None:
    candidates = build_dossier_participant_candidates(
        graph_payload={
            "graph": {
                "nodes": [
                    {"id": "conv1-a", "label": "A公司", "type": "organization", "conversation_id": 11},
                    {"id": "conv2-a", "label": "A公司", "type": "organization", "conversation_id": 12},
                ]
            }
        },
        participants=[],
        confirmed_mentions=[],
        participant_aliases={},
    )

    cross_candidates = [
        candidate
        for candidate in candidates
        if candidate.get("task_type") == "identity"
    ]
    assert len(cross_candidates) == 1
    assert cross_candidates[0]["confidence"] == "high"


def test_self_anchor_resolution_decision_does_not_filter_cross_conversation_identity_candidates() -> None:
    candidates = build_dossier_participant_candidates(
        graph_payload={
            "graph": {
                "nodes": [
                    {"id": "conv1-a", "label": "A公司", "type": "organization", "conversation_id": 11},
                    {"id": "conv2-a", "label": "A公司", "type": "organization", "conversation_id": 12},
                ]
            }
        },
        participants=[],
        confirmed_mentions=[],
        participant_aliases={},
        resolution_decisions=[
            {
                "subject": "self_anchor",
                "decision_type": "not_same",
                "source_graph_node_id": "self-node-1",
                "target_participant_id": 7,
            }
        ],
    )

    cross_candidates = [
        candidate
        for candidate in candidates
        if candidate.get("task_type") == "identity"
    ]
    assert len(cross_candidates) == 1


def test_cross_conversation_decide_api_persists_symmetric_pair_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()
    dossier = Dossier(
        id=18,
        user_id=3,
        title="劳动争议案卷",
        case_type="劳动争议",
        summary="summary",
        status="active",
    )
    db = DummyDB()
    create_kwargs: dict[str, object] = {}

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        return dossier

    async def fake_list_dossier_entity_resolution_decisions(db, user_id: int, dossier_id: int):
        return []

    async def fake_create_dossier_entity_resolution_decision(db, **kwargs):
        create_kwargs.update(kwargs)
        return DossierEntityResolutionDecision(
            id=91,
            dossier_id=dossier.id,
            user_id=3,
            decision_type=kwargs["decision_type"],
            subject=kwargs["subject"],
            source_conversation_id=kwargs["source_conversation_id"],
            source_graph_node_id=kwargs["source_graph_node_id"],
            source_raw_label=kwargs["source_raw_label"],
            target_participant_id=kwargs["target_participant_id"],
            target_display_label=kwargs.get("target_display_label"),
        )

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dossiers_api, "get_dossier_for_user", fake_get_dossier_for_user)
    monkeypatch.setattr(
        dossiers_api,
        "list_dossier_entity_resolution_decisions",
        fake_list_dossier_entity_resolution_decisions,
    )
    monkeypatch.setattr(
        dossiers_api,
        "create_dossier_entity_resolution_decision",
        fake_create_dossier_entity_resolution_decision,
    )

    result = asyncio.run(
        dossiers_api.decide_cross_conversation_participant_resolution(
            dossier_id=18,
            payload=dossiers_api.CrossConversationResolutionDecisionRequest(
                subject="cross_conversation_identity",
                decision="not_same",
                source_node=dossiers_api.CrossConversationDecisionNodeRequest(
                    conversation_id=12,
                    graph_node_id="conv2-hr",
                    raw_label="王经理",
                    graph_node_label="王经理",
                    graph_node_type="person",
                ),
                target_node=dossiers_api.CrossConversationDecisionNodeRequest(
                    conversation_id=11,
                    graph_node_id="conv1-person",
                    raw_label="张三",
                    graph_node_label="张三",
                    graph_node_type="person",
                ),
                reason="user_marked_not_same",
                candidate_key="cross-conversation:12:conv2-hr:11:conv1-person",
                task_key="cross-conversation:12:conv2-hr:11:conv1-person",
            ),
            current_user=AuthenticatedUser(user_id=3, username="owner"),
            db=db,
        )
    )

    expected_pair_key = build_cross_conversation_resolution_pair_key("conv1-person", "conv2-hr")
    assert create_kwargs["subject"] == "cross_conversation_identity"
    assert create_kwargs["decision_type"] == "not_same"
    assert create_kwargs["source_graph_node_id"] == expected_pair_key
    assert create_kwargs["target_participant_id"] == build_cross_conversation_resolution_pair_hash(
        expected_pair_key
    )
    assert result["status"] == "success"
    assert result["decision"]["pair_key"] == expected_pair_key


def test_cross_conversation_decide_api_updates_existing_pair_instead_of_creating_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()
    dossier = Dossier(
        id=19,
        user_id=3,
        title="劳动争议案卷",
        case_type="劳动争议",
        summary="summary",
        status="active",
    )
    db = DummyDB()
    pair_key = build_cross_conversation_resolution_pair_key("conv1-a", "conv2-a")
    pair_hash = build_cross_conversation_resolution_pair_hash(pair_key)
    existing_decision = DossierEntityResolutionDecision(
        id=92,
        dossier_id=dossier.id,
        user_id=3,
        decision_type="skip",
        subject="cross_conversation_identity",
        source_conversation_id=11,
        source_graph_node_id=pair_key,
        source_raw_label="A公司",
        target_participant_id=pair_hash,
        target_display_label="A公司",
    )

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        return dossier

    async def fake_list_dossier_entity_resolution_decisions(db, user_id: int, dossier_id: int):
        return [existing_decision]

    async def fake_create_dossier_entity_resolution_decision(db, **kwargs):
        raise AssertionError("existing pair should be updated instead of creating a duplicate decision")

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dossiers_api, "get_dossier_for_user", fake_get_dossier_for_user)
    monkeypatch.setattr(
        dossiers_api,
        "list_dossier_entity_resolution_decisions",
        fake_list_dossier_entity_resolution_decisions,
    )
    monkeypatch.setattr(
        dossiers_api,
        "create_dossier_entity_resolution_decision",
        fake_create_dossier_entity_resolution_decision,
    )

    result = asyncio.run(
        dossiers_api.decide_cross_conversation_participant_resolution(
            dossier_id=19,
            payload=dossiers_api.CrossConversationResolutionDecisionRequest(
                subject="cross_conversation_identity",
                decision="not_same",
                source_node=dossiers_api.CrossConversationDecisionNodeRequest(
                    conversation_id=12,
                    graph_node_id="conv2-a",
                    raw_label="A公司",
                    graph_node_label="A公司",
                    graph_node_type="organization",
                ),
                target_node=dossiers_api.CrossConversationDecisionNodeRequest(
                    conversation_id=11,
                    graph_node_id="conv1-a",
                    raw_label="A公司",
                    graph_node_label="A公司",
                    graph_node_type="organization",
                ),
            ),
            current_user=AuthenticatedUser(user_id=3, username="owner"),
            db=db,
        )
    )

    assert existing_decision.decision_type == "not_same"
    assert result["decision"]["pair_key"] == pair_key
