from __future__ import annotations

import asyncio
import importlib
import sys
from types import ModuleType

import pytest
from fastapi import HTTPException

from app.core.security import AuthenticatedUser
from app.models import Conversation, Dossier, DossierParticipant, DossierParticipantMention


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
    pass


def _make_dossier() -> Dossier:
    return Dossier(
        id=51,
        user_id=3,
        title="跨会话主体整理案卷",
        case_type="劳动争议",
        summary="summary",
        status="active",
    )


def _make_scope_objects():
    participant = DossierParticipant(
        id=12,
        dossier_id=51,
        user_id=3,
        label="A公司",
        role="用人单位",
        party_side="opponent",
        entity_type="organization",
        confirmed_by_user=True,
    )
    mentions = [
        DossierParticipantMention(
            id=101,
            dossier_id=51,
            conversation_id=674,
            participant_id=12,
            raw_label="A公司",
            graph_node_id="raw-674-a",
            graph_node_label="A公司",
            graph_node_type="organization",
            source="graph_node",
            confirmed_by_user=True,
        ),
        DossierParticipantMention(
            id=102,
            dossier_id=51,
            conversation_id=675,
            participant_id=12,
            raw_label="A公司",
            graph_node_id="raw-675-a",
            graph_node_label="A公司",
            graph_node_type="organization",
            source="graph_node",
            confirmed_by_user=True,
        ),
    ]
    conversations = [
        Conversation(id=674, user_id=3, dossier_id=51, title="会话 674"),
        Conversation(id=675, user_id=3, dossier_id=51, title="会话 675"),
    ]
    return conversations, [participant], mentions


def _make_raw_graph_payload(*, merged: bool) -> dict:
    if merged:
        confirmed_participants = [
            {
                "id": 12,
                "label": "A公司",
                "entity_type": "organization",
                "party_side": "opponent",
            }
        ]
        participant_mappings = [
            {
                "participant_id": 12,
                "participant_label": "A公司",
                "participant_entity_type": "organization",
                "conversation_id": 674,
                "raw_label": "A公司",
                "graph_node_id": "raw-674-a",
                "graph_node_label": "A公司",
                "graph_node_type": "organization",
                "source": "graph_node",
            },
            {
                "participant_id": 12,
                "participant_label": "A公司",
                "participant_entity_type": "organization",
                "conversation_id": 675,
                "raw_label": "A公司",
                "graph_node_id": "raw-675-a",
                "graph_node_label": "A公司",
                "graph_node_type": "organization",
                "source": "graph_node",
            },
        ]
    else:
        confirmed_participants = []
        participant_mappings = []

    return {
        "status": "success",
        "dossier_id": 51,
        "participant_aliases": {},
        "confirmed_participants": confirmed_participants,
        "participant_mappings": participant_mappings,
        "graph": {
            "nodes": [
                {"id": "raw-674-a", "label": "A公司", "type": "organization", "conversation_id": 674},
                {"id": "raw-675-a", "label": "A公司", "type": "organization", "conversation_id": 675},
            ],
            "edges": [],
            "stats": {
                "conversation_count": 2,
                "node_count": 2,
                "edge_count": 0,
            },
        },
        "generated_at": "2026-04-28T00:00:00+00:00",
    }


def _patch_display_graph_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    dossiers_api,
    *,
    dossier: Dossier | None,
    raw_payload: dict,
) -> None:
    conversations, participants, mentions = _make_scope_objects()

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        assert dossier_id == 51
        return dossier

    async def fake_list_dossier_conversations(db, user_id: int, dossier_id: int):
        return conversations

    async def fake_list_dossier_participants(db, user_id: int, dossier_id: int):
        return participants

    async def fake_list_dossier_participant_mentions(db, user_id: int, dossier_id: int):
        return mentions

    async def fake_get_dossier_graph_payload(**kwargs):
        return raw_payload

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dossiers_api, "get_dossier_for_user", fake_get_dossier_for_user)
    monkeypatch.setattr(
        dossiers_api,
        "list_dossier_conversations",
        fake_list_dossier_conversations,
    )
    monkeypatch.setattr(
        dossiers_api,
        "list_dossier_participants",
        fake_list_dossier_participants,
    )
    monkeypatch.setattr(
        dossiers_api,
        "list_dossier_participant_mentions",
        fake_list_dossier_participant_mentions,
    )
    monkeypatch.setattr(
        dossiers_api,
        "get_dossier_graph_payload",
        fake_get_dossier_graph_payload,
    )


def test_display_graph_owner_can_access_and_confirmed_mapping_merges_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()
    _patch_display_graph_dependencies(
        monkeypatch,
        dossiers_api,
        dossier=_make_dossier(),
        raw_payload=_make_raw_graph_payload(merged=True),
    )

    result = asyncio.run(
        dossiers_api.get_dossier_display_graph(
            dossier_id=51,
            current_user=AuthenticatedUser(user_id=3, username="owner"),
            db=DummyDB(),
        )
    )

    assert result["status"] == "success"
    assert result["dossier_id"] == 51
    assert result["view"] == "display"
    assert result["generated_at"] == "2026-04-28T00:00:00+00:00"
    display_graph = result["display_graph"]
    assert display_graph["stats"]["raw_node_count"] == 2
    assert display_graph["stats"]["display_node_count"] == 1
    assert display_graph["stats"]["merged_participant_count"] == 1
    merged_node = display_graph["nodes"][0]
    assert merged_node["id"] == "participant:12"
    assert merged_node["confirmed"] is True
    assert merged_node["pending_confirmation"] is False
    assert merged_node["source_conversation_ids"] == [674, 675]
    assert merged_node["raw_node_ids"] == ["raw-674-a", "raw-675-a"]


def test_display_graph_non_owner_cannot_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        return None

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dossiers_api, "get_dossier_for_user", fake_get_dossier_for_user)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            dossiers_api.get_dossier_display_graph(
                dossier_id=51,
                current_user=AuthenticatedUser(user_id=9, username="other"),
                db=DummyDB(),
            )
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Dossier not found"


def test_display_graph_unconfirmed_same_label_raw_nodes_remain_separate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()
    _patch_display_graph_dependencies(
        monkeypatch,
        dossiers_api,
        dossier=_make_dossier(),
        raw_payload=_make_raw_graph_payload(merged=False),
    )

    result = asyncio.run(
        dossiers_api.get_dossier_display_graph(
            dossier_id=51,
            current_user=AuthenticatedUser(user_id=3, username="owner"),
            db=DummyDB(),
        )
    )

    display_graph = result["display_graph"]
    assert display_graph["stats"]["display_node_count"] == 2
    assert display_graph["stats"]["merged_participant_count"] == 0
    assert display_graph["stats"]["pending_confirmation_count"] == 2
    assert {node["id"] for node in display_graph["nodes"]} == {"raw:raw-674-a", "raw:raw-675-a"}
    assert all(node["confirmed"] is False for node in display_graph["nodes"])
    assert all(node["pending_confirmation"] is True for node in display_graph["nodes"])


def test_raw_graph_endpoint_remains_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()
    raw_payload = _make_raw_graph_payload(merged=True)
    _patch_display_graph_dependencies(
        monkeypatch,
        dossiers_api,
        dossier=_make_dossier(),
        raw_payload=raw_payload,
    )

    result = asyncio.run(
        dossiers_api.get_dossier_graph(
            dossier_id=51,
            current_user=AuthenticatedUser(user_id=3, username="owner"),
            db=DummyDB(),
        )
    )

    assert result == raw_payload
    assert "graph" in result
    assert "display_graph" not in result
    assert result["graph"]["nodes"][0]["id"] == "raw-674-a"
    assert result["graph"]["nodes"][1]["id"] == "raw-675-a"

