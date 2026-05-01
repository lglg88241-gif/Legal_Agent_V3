from __future__ import annotations

import asyncio
import importlib
import sys
from datetime import datetime
from types import ModuleType

import pytest
from fastapi import FastAPI, HTTPException

from app.core.security import AuthenticatedUser
from app.models import Dossier, DossierParticipant, DossierParticipantMention
from app.services.conversation_store import normalize_participant_mention_source


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
    def __init__(self) -> None:
        self.refreshed_objects: list[object] = []

    def add(self, obj) -> None:
        return None

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def refresh(self, obj) -> None:
        self.refreshed_objects.append(obj)
        if isinstance(obj, DossierParticipant) and getattr(obj, "updated_at", None) is None:
            obj.updated_at = datetime(2026, 4, 28, 12, 0, 0)
        if hasattr(obj, "created_at") and getattr(obj, "created_at", None) is None:
            obj.created_at = datetime(2026, 4, 28, 12, 0, 0)
        return None


def _make_dossier() -> Dossier:
    return Dossier(
        id=51,
        user_id=3,
        title="劳动争议案卷",
        case_type="劳动争议",
        summary="summary",
        status="active",
    )


def test_normalize_participant_mention_source_accepts_party_naming() -> None:
    assert normalize_participant_mention_source("party_naming") == "party_naming"


def test_party_naming_confirm_owner_creates_self_participant_and_mentions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()
    dossier = _make_dossier()
    captured: dict[str, object] = {}

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        assert user_id == 3
        assert dossier_id == 51
        return dossier

    async def fake_list_dossier_participants(db, user_id: int, dossier_id: int):
        return []

    async def fake_create_dossier_participant_record(
        db,
        *,
        dossier,
        user_id: int,
        label: str,
        role: str | None = None,
        party_side: str = "unknown",
        entity_type: str = "unknown",
        description: str | None = None,
        confirmed_by_user: bool = True,
    ):
        captured["create"] = {
            "label": label,
            "role": role,
            "party_side": party_side,
            "entity_type": entity_type,
            "description": description,
            "confirmed_by_user": confirmed_by_user,
        }
        return DossierParticipant(
            id=100,
            dossier_id=dossier.id,
            user_id=user_id,
            label=label,
            role=role,
            party_side=party_side,
            entity_type=entity_type,
            description=description,
            confirmed_by_user=confirmed_by_user,
        )

    async def fake_update_dossier_participant_record(db, participant, **kwargs):
        raise AssertionError("existing self participant should not be updated in create case")

    async def fake_resolve_dossier_participant_mentions(
        db,
        *,
        dossier,
        user_id: int,
        participant,
        mentions,
    ):
        captured["mentions_payload"] = mentions
        resolved_mentions = []
        for index, mention in enumerate(mentions, start=1):
            resolved_mentions.append(
                DossierParticipantMention(
                    id=index,
                    dossier_id=dossier.id,
                    conversation_id=mention["conversation_id"],
                    participant_id=participant.id,
                    raw_label=mention["raw_label"],
                    graph_node_id=mention.get("graph_node_id"),
                    graph_node_label=mention.get("graph_node_label"),
                    graph_node_type=mention.get("graph_node_type"),
                    source=mention["source"],
                    confidence=mention.get("confidence"),
                    confirmed_by_user=True,
                )
            )
        return resolved_mentions

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dossiers_api, "get_dossier_for_user", fake_get_dossier_for_user)
    monkeypatch.setattr(
        dossiers_api,
        "list_dossier_participants",
        fake_list_dossier_participants,
    )
    monkeypatch.setattr(
        dossiers_api,
        "create_dossier_participant_record",
        fake_create_dossier_participant_record,
    )
    monkeypatch.setattr(
        dossiers_api,
        "update_dossier_participant_record",
        fake_update_dossier_participant_record,
    )
    monkeypatch.setattr(
        dossiers_api,
        "resolve_dossier_participant_mentions",
        fake_resolve_dossier_participant_mentions,
    )

    result = asyncio.run(
        dossiers_api.confirm_dossier_party_naming(
            dossier_id=51,
            payload=dossiers_api.PartyNamingConfirmRequest(
                display_label="劳动者本人",
                source_task=dossiers_api.PartyNamingSourceTaskRequest(
                    subject="self_anchor",
                    evidence=dossiers_api.PartyNamingEvidenceRequest(
                        raw_labels=["当事人", "我"],
                        source_conversation_ids=[101, 102],
                        raw_node_ids=["raw:self-1", "raw:self-2"],
                    ),
                ),
            ),
            current_user=AuthenticatedUser(user_id=3, username="owner"),
            db=DummyDB(),
        )
    )

    assert result["status"] == "success"
    assert result["participant_id"] == 100
    assert result["display_name"] == "劳动者本人"
    assert result["aliases"] == ["当事人", "我"]
    assert result["updated"] is True
    assert result["participant"]["label"] == "劳动者本人"
    assert result["participant"]["party_side"] == "claimant"
    assert result["participant"]["entity_type"] == "person"
    assert result["participant"]["confirmed_by_user"] is True
    assert captured["create"] == {
        "label": "劳动者本人",
        "role": "当事人",
        "party_side": "claimant",
        "entity_type": "person",
        "description": None,
        "confirmed_by_user": True,
    }

    mentions_payload = captured["mentions_payload"]
    assert mentions_payload == [
        {
            "conversation_id": 101,
            "raw_label": "当事人",
            "source": "party_naming",
            "confidence": 1.0,
            "graph_node_id": "raw:self-1",
            "graph_node_label": "当事人",
            "graph_node_type": "person",
        },
        {
            "conversation_id": 102,
            "raw_label": "我",
            "source": "party_naming",
            "confidence": 1.0,
            "graph_node_id": "raw:self-2",
            "graph_node_label": "我",
            "graph_node_type": "person",
        },
    ]
    assert result["mentions"][0]["conversation_id"] == 101
    assert result["mentions"][0]["graph_node_id"] == "raw:self-1"
    assert result["mentions"][0]["source"] == "party_naming"
    assert result["mentions"][1]["conversation_id"] == 102
    assert result["mentions"][1]["graph_node_id"] == "raw:self-2"


def test_party_naming_confirm_updates_existing_self_participant_without_creating_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()
    dossier = _make_dossier()
    existing_participant = DossierParticipant(
        id=88,
        dossier_id=dossier.id,
        user_id=3,
        label="我",
        role="当事人",
        party_side="claimant",
        entity_type="person",
        confirmed_by_user=False,
    )
    update_calls: list[dict[str, object]] = []

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        return dossier

    async def fake_list_dossier_participants(db, user_id: int, dossier_id: int):
        return [existing_participant]

    async def fake_create_dossier_participant_record(db, **kwargs):
        raise AssertionError("existing self participant should be updated, not recreated")

    async def fake_update_dossier_participant_record(db, participant, **kwargs):
        update_calls.append(kwargs)
        participant.label = kwargs["label"]
        participant.role = kwargs["role"]
        participant.party_side = kwargs["party_side"]
        participant.entity_type = kwargs["entity_type"]
        return participant

    async def fake_resolve_dossier_participant_mentions(
        db,
        *,
        dossier,
        user_id: int,
        participant,
        mentions,
    ):
        assert mentions == []
        return []

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dossiers_api, "get_dossier_for_user", fake_get_dossier_for_user)
    monkeypatch.setattr(
        dossiers_api,
        "list_dossier_participants",
        fake_list_dossier_participants,
    )
    monkeypatch.setattr(
        dossiers_api,
        "create_dossier_participant_record",
        fake_create_dossier_participant_record,
    )
    monkeypatch.setattr(
        dossiers_api,
        "update_dossier_participant_record",
        fake_update_dossier_participant_record,
    )
    monkeypatch.setattr(
        dossiers_api,
        "resolve_dossier_participant_mentions",
        fake_resolve_dossier_participant_mentions,
    )

    result = asyncio.run(
        dossiers_api.confirm_dossier_party_naming(
            dossier_id=51,
            payload=dossiers_api.PartyNamingConfirmRequest(
                display_label="张三",
                source_task=dossiers_api.PartyNamingSourceTaskRequest(
                    subject="self_anchor",
                ),
            ),
            current_user=AuthenticatedUser(user_id=3, username="owner"),
            db=DummyDB(),
        )
    )

    assert result["status"] == "success"
    assert result["participant_id"] == 88
    assert result["display_name"] == "张三"
    assert result["aliases"] == []
    assert result["updated"] is True
    assert result["participant"]["id"] == 88
    assert result["participant"]["label"] == "张三"
    assert result["mentions"] == []
    assert len(update_calls) == 1
    assert update_calls[0] == {
        "label": "张三",
        "role": "当事人",
        "party_side": "claimant",
        "entity_type": "person",
    }
    assert existing_participant.confirmed_by_user is True


def test_party_naming_confirm_non_owner_returns_project_style_not_found(
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
            dossiers_api.confirm_dossier_party_naming(
                dossier_id=51,
                payload=dossiers_api.PartyNamingConfirmRequest(
                    display_label="劳动者本人",
                    source_task=dossiers_api.PartyNamingSourceTaskRequest(subject="self_anchor"),
                ),
                current_user=AuthenticatedUser(user_id=9, username="other"),
                db=DummyDB(),
            )
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Dossier not found"


def test_party_naming_confirm_rejects_blank_display_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()
    dossier = _make_dossier()

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        return dossier

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dossiers_api, "get_dossier_for_user", fake_get_dossier_for_user)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            dossiers_api.confirm_dossier_party_naming(
                dossier_id=51,
                payload=dossiers_api.PartyNamingConfirmRequest(
                    display_label="   ",
                    source_task=dossiers_api.PartyNamingSourceTaskRequest(subject="self_anchor"),
                ),
                current_user=AuthenticatedUser(user_id=3, username="owner"),
                db=DummyDB(),
            )
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "display_label is required"


def test_party_naming_confirm_rejects_non_self_anchor_subject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()
    dossier = _make_dossier()

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        return dossier

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dossiers_api, "get_dossier_for_user", fake_get_dossier_for_user)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            dossiers_api.confirm_dossier_party_naming(
                dossier_id=51,
                payload=dossiers_api.PartyNamingConfirmRequest(
                    display_label="劳动者本人",
                    source_task=dossiers_api.PartyNamingSourceTaskRequest(subject="other_subject"),
                ),
                current_user=AuthenticatedUser(user_id=3, username="owner"),
                db=DummyDB(),
            )
        )

    assert exc_info.value.status_code == 400
    assert "self_anchor" in exc_info.value.detail


def test_party_naming_confirm_route_is_registered_in_openapi() -> None:
    dossiers_api = _load_dossiers_api_module()
    app = FastAPI()
    app.include_router(dossiers_api.router)

    schema = app.openapi()

    assert "/dossiers/{dossier_id}/intelligence/tasks/party-naming/confirm" in schema["paths"]
    assert "post" in schema["paths"]["/dossiers/{dossier_id}/intelligence/tasks/party-naming/confirm"]
