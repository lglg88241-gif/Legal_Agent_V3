from __future__ import annotations

import asyncio
import importlib
import sys
from datetime import datetime
from types import ModuleType

import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, inspect

from app.core.security import AuthenticatedUser
from app.models import (
    Dossier,
    DossierEntityResolutionDecision,
    DossierParticipant,
    DossierParticipantMention,
)
from app.services.conversation_store import ensure_storage_schema

DOSSIER_ID = 51
USER_ID = 3
TARGET_PARTICIPANT_ID = 123
NEW_PARTICIPANT_ID = 200
SELF_LABEL = "\u5f53\u4e8b\u4eba"
TARGET_LABEL = "\u5f20\u4e09"
NEW_LABEL = "\u674e\u56db"
SOURCE_GRAPH_NODE_ID = "self-697"


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
        if hasattr(obj, "updated_at") and getattr(obj, "updated_at", None) is None:
            obj.updated_at = datetime(2026, 4, 28, 12, 0, 0)
        if hasattr(obj, "created_at") and getattr(obj, "created_at", None) is None:
            obj.created_at = datetime(2026, 4, 28, 12, 0, 0)
        return None


def _make_dossier() -> Dossier:
    return Dossier(
        id=DOSSIER_ID,
        user_id=USER_ID,
        title="\u52b3\u52a8\u4e89\u8bae\u6848\u5377",
        case_type="\u52b3\u52a8\u4e89\u8bae",
        summary="summary",
        status="active",
    )


def _make_target_participant() -> DossierParticipant:
    return DossierParticipant(
        id=TARGET_PARTICIPANT_ID,
        dossier_id=DOSSIER_ID,
        user_id=USER_ID,
        label=TARGET_LABEL,
        role="\u5f53\u4e8b\u4eba",
        party_side="claimant",
        entity_type="person",
        confirmed_by_user=True,
    )


def _make_source_node(api_module, *, raw_label: str = SELF_LABEL):
    return api_module.SelfIdentitySourceNodeRequest(
        conversation_id=697,
        raw_label=raw_label,
        graph_node_id=SOURCE_GRAPH_NODE_ID,
        graph_node_label=raw_label,
        graph_node_type="person",
    )


def _make_mention(*, participant_id: int, source: str = "self_identity_confirmation") -> DossierParticipantMention:
    return DossierParticipantMention(
        id=1,
        dossier_id=DOSSIER_ID,
        conversation_id=697,
        participant_id=participant_id,
        raw_label=SELF_LABEL,
        graph_node_id=SOURCE_GRAPH_NODE_ID,
        graph_node_label=SELF_LABEL,
        graph_node_type="person",
        source=source,
        confidence=1.0,
        confirmed_by_user=True,
    )


def _make_decision(
    *,
    decision_type: str,
    target_participant_id: int = TARGET_PARTICIPANT_ID,
    new_participant_id: int | None = None,
    new_display_label: str | None = None,
) -> DossierEntityResolutionDecision:
    return DossierEntityResolutionDecision(
        id=9,
        dossier_id=DOSSIER_ID,
        user_id=USER_ID,
        decision_type=decision_type,
        subject="self_anchor",
        source_conversation_id=697,
        source_graph_node_id=SOURCE_GRAPH_NODE_ID,
        source_raw_label=SELF_LABEL,
        target_participant_id=target_participant_id,
        target_display_label=TARGET_LABEL,
        new_participant_id=new_participant_id,
        new_display_label=new_display_label,
    )


def test_runtime_schema_creates_self_identity_resolution_decision_table() -> None:
    async def _run() -> None:
        sync_engine = create_engine("sqlite:///:memory:")

        class FakeAsyncConnection:
            async def run_sync(self, fn):
                with sync_engine.begin() as sync_conn:
                    return fn(sync_conn)

        class FakeAsyncBegin:
            async def __aenter__(self):
                return FakeAsyncConnection()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class FakeAsyncEngine:
            def begin(self):
                return FakeAsyncBegin()

        try:
            await ensure_storage_schema(bind_engine=FakeAsyncEngine())
            with sync_engine.begin() as sync_conn:
                inspector = inspect(sync_conn)
                tables = set(inspector.get_table_names())
                assert "dossier_entity_resolution_decisions" in tables
                decision_columns = {
                    column["name"]
                    for column in inspector.get_columns("dossier_entity_resolution_decisions")
                }
                assert "decision_type" in decision_columns
                assert "source_graph_node_id" in decision_columns
                assert "target_participant_id" in decision_columns
                assert "new_participant_id" in decision_columns
        finally:
            sync_engine.dispose()

    asyncio.run(_run())


def test_self_identity_confirm_same_owner_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()
    dossier = _make_dossier()
    target_participant = _make_target_participant()
    created_mentions: list[dict] = []
    created_decisions: list[dict] = []
    existing_mention = _make_mention(participant_id=TARGET_PARTICIPANT_ID, source="party_naming")
    existing_decision = _make_decision(decision_type="same")
    state = {"call_count": 0}

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        assert user_id == USER_ID
        assert dossier_id == DOSSIER_ID
        return dossier

    async def fake_get_dossier_participant_for_user(db, user_id: int, dossier_id: int, participant_id: int):
        assert participant_id == TARGET_PARTICIPANT_ID
        return target_participant

    async def fake_list_dossier_participant_mentions(db, user_id: int, dossier_id: int):
        return [] if state["call_count"] == 0 else [existing_mention]

    async def fake_list_dossier_entity_resolution_decisions(db, user_id: int, dossier_id: int):
        return [] if state["call_count"] == 0 else [existing_decision]

    async def fake_resolve_dossier_participant_mentions(
        db,
        *,
        dossier,
        user_id: int,
        participant,
        mentions,
    ):
        created_mentions.extend(mentions)
        return [_make_mention(participant_id=participant.id)]

    async def fake_create_dossier_entity_resolution_decision(db, **kwargs):
        created_decisions.append(kwargs)
        return existing_decision

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dossiers_api, "get_dossier_for_user", fake_get_dossier_for_user)
    monkeypatch.setattr(
        dossiers_api,
        "get_dossier_participant_for_user",
        fake_get_dossier_participant_for_user,
    )
    monkeypatch.setattr(
        dossiers_api,
        "list_dossier_participant_mentions",
        fake_list_dossier_participant_mentions,
    )
    monkeypatch.setattr(
        dossiers_api,
        "list_dossier_entity_resolution_decisions",
        fake_list_dossier_entity_resolution_decisions,
    )
    monkeypatch.setattr(
        dossiers_api,
        "resolve_dossier_participant_mentions",
        fake_resolve_dossier_participant_mentions,
    )
    monkeypatch.setattr(
        dossiers_api,
        "create_dossier_entity_resolution_decision",
        fake_create_dossier_entity_resolution_decision,
    )

    payload = dossiers_api.SelfIdentityConfirmRequest(
        action="confirm_same",
        source_node=_make_source_node(dossiers_api),
        target_participant_id=TARGET_PARTICIPANT_ID,
    )

    first_result = asyncio.run(
        dossiers_api.confirm_dossier_self_identity(
            dossier_id=DOSSIER_ID,
            payload=payload,
            current_user=AuthenticatedUser(user_id=USER_ID, username="owner"),
            db=DummyDB(),
        )
    )
    state["call_count"] = 1
    second_result = asyncio.run(
        dossiers_api.confirm_dossier_self_identity(
            dossier_id=DOSSIER_ID,
            payload=payload,
            current_user=AuthenticatedUser(user_id=USER_ID, username="owner"),
            db=DummyDB(),
        )
    )

    assert first_result["status"] == "success"
    assert first_result["participant"]["label"] == TARGET_LABEL
    assert first_result["mentions"][0]["graph_node_id"] == SOURCE_GRAPH_NODE_ID
    assert first_result["decision"]["decision_type"] == "same"
    assert len(created_mentions) == 1
    assert created_mentions[0] == {
        "conversation_id": 697,
        "raw_label": SELF_LABEL,
        "source": "self_identity_confirmation",
        "confidence": 1.0,
        "graph_node_id": SOURCE_GRAPH_NODE_ID,
        "graph_node_label": SELF_LABEL,
        "graph_node_type": "person",
    }
    assert len(created_decisions) == 1
    assert second_result["status"] == "success"
    assert second_result["participant"]["label"] == TARGET_LABEL
    assert second_result["mentions"][0]["participant_id"] == TARGET_PARTICIPANT_ID


def test_self_identity_confirm_different_creates_new_participant_and_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()
    dossier = _make_dossier()
    target_participant = _make_target_participant()
    new_participant = DossierParticipant(
        id=NEW_PARTICIPANT_ID,
        dossier_id=DOSSIER_ID,
        user_id=USER_ID,
        label=NEW_LABEL,
        role="\u5f53\u4e8b\u4eba",
        party_side="claimant",
        entity_type="person",
        confirmed_by_user=True,
    )
    captured: dict[str, object] = {}

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        return dossier

    async def fake_get_dossier_participant_for_user(db, user_id: int, dossier_id: int, participant_id: int):
        return target_participant

    async def fake_list_dossier_participant_mentions(db, user_id: int, dossier_id: int):
        return []

    async def fake_list_dossier_entity_resolution_decisions(db, user_id: int, dossier_id: int):
        return []

    async def fake_list_dossier_entity_resolution_decisions(db, user_id: int, dossier_id: int):
        return []

    async def fake_list_dossier_entity_resolution_decisions(db, user_id: int, dossier_id: int):
        return []

    async def fake_list_dossier_entity_resolution_decisions(db, user_id: int, dossier_id: int):
        return []

    async def fake_create_dossier_participant_record(db, **kwargs):
        captured["create_participant"] = kwargs
        return new_participant

    async def fake_resolve_dossier_participant_mentions(
        db,
        *,
        dossier,
        user_id: int,
        participant,
        mentions,
    ):
        captured["mentions_payload"] = mentions
        return [_make_mention(participant_id=participant.id)]

    async def fake_create_dossier_entity_resolution_decision(db, **kwargs):
        captured["decision_payload"] = kwargs
        return _make_decision(
            decision_type="not_same",
            new_participant_id=NEW_PARTICIPANT_ID,
            new_display_label=NEW_LABEL,
        )

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dossiers_api, "get_dossier_for_user", fake_get_dossier_for_user)
    monkeypatch.setattr(
        dossiers_api,
        "get_dossier_participant_for_user",
        fake_get_dossier_participant_for_user,
    )
    monkeypatch.setattr(
        dossiers_api,
        "list_dossier_participant_mentions",
        fake_list_dossier_participant_mentions,
    )
    monkeypatch.setattr(
        dossiers_api,
        "list_dossier_entity_resolution_decisions",
        fake_list_dossier_entity_resolution_decisions,
    )
    monkeypatch.setattr(
        dossiers_api,
        "list_dossier_entity_resolution_decisions",
        fake_list_dossier_entity_resolution_decisions,
    )
    monkeypatch.setattr(
        dossiers_api,
        "list_dossier_entity_resolution_decisions",
        fake_list_dossier_entity_resolution_decisions,
    )
    monkeypatch.setattr(
        dossiers_api,
        "list_dossier_entity_resolution_decisions",
        fake_list_dossier_entity_resolution_decisions,
    )
    monkeypatch.setattr(
        dossiers_api,
        "create_dossier_participant_record",
        fake_create_dossier_participant_record,
    )
    monkeypatch.setattr(
        dossiers_api,
        "resolve_dossier_participant_mentions",
        fake_resolve_dossier_participant_mentions,
    )
    monkeypatch.setattr(
        dossiers_api,
        "create_dossier_entity_resolution_decision",
        fake_create_dossier_entity_resolution_decision,
    )

    result = asyncio.run(
        dossiers_api.confirm_dossier_self_identity(
            dossier_id=DOSSIER_ID,
            payload=dossiers_api.SelfIdentityConfirmRequest(
                action="confirm_different",
                source_node=_make_source_node(dossiers_api),
                target_participant_id=TARGET_PARTICIPANT_ID,
                new_display_label=NEW_LABEL,
            ),
            current_user=AuthenticatedUser(user_id=USER_ID, username="owner"),
            db=DummyDB(),
        )
    )

    assert result["status"] == "success"
    assert result["action"] == "confirm_different"
    assert result["participant"]["label"] == NEW_LABEL
    assert result["participant"]["confirmed_by_user"] is True
    assert result["mentions"][0]["participant_id"] == NEW_PARTICIPANT_ID
    assert result["mentions"][0]["graph_node_id"] == SOURCE_GRAPH_NODE_ID
    assert result["decision"]["decision_type"] == "not_same"
    assert result["decision"]["target_participant_id"] == TARGET_PARTICIPANT_ID
    assert result["decision"]["new_participant_id"] == NEW_PARTICIPANT_ID
    assert result["decision"]["new_display_label"] == NEW_LABEL
    assert captured["create_participant"] == {
        "dossier": dossier,
        "user_id": USER_ID,
        "label": NEW_LABEL,
        "role": dossiers_api._PARTY_NAMING_ROLE,
        "party_side": "claimant",
        "entity_type": "person",
        "description": None,
        "confirmed_by_user": True,
    }
    assert captured["mentions_payload"] == [
        {
            "conversation_id": 697,
            "raw_label": SELF_LABEL,
            "source": "self_identity_confirmation",
            "confidence": 1.0,
            "graph_node_id": SOURCE_GRAPH_NODE_ID,
            "graph_node_label": SELF_LABEL,
            "graph_node_type": "person",
        }
    ]
    assert captured["decision_payload"]["decision_type"] == "not_same"
    assert captured["decision_payload"]["target_participant_id"] == TARGET_PARTICIPANT_ID
    assert captured["decision_payload"]["new_participant_id"] == NEW_PARTICIPANT_ID


def test_self_identity_confirm_different_requires_new_display_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()
    dossier = _make_dossier()
    target_participant = _make_target_participant()

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        return dossier

    async def fake_get_dossier_participant_for_user(db, user_id: int, dossier_id: int, participant_id: int):
        return target_participant

    async def fake_list_dossier_participant_mentions(db, user_id: int, dossier_id: int):
        return []

    async def fake_list_dossier_entity_resolution_decisions(db, user_id: int, dossier_id: int):
        return []

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dossiers_api, "get_dossier_for_user", fake_get_dossier_for_user)
    monkeypatch.setattr(
        dossiers_api,
        "get_dossier_participant_for_user",
        fake_get_dossier_participant_for_user,
    )
    monkeypatch.setattr(
        dossiers_api,
        "list_dossier_participant_mentions",
        fake_list_dossier_participant_mentions,
    )
    monkeypatch.setattr(
        dossiers_api,
        "list_dossier_entity_resolution_decisions",
        fake_list_dossier_entity_resolution_decisions,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            dossiers_api.confirm_dossier_self_identity(
                dossier_id=DOSSIER_ID,
                payload=dossiers_api.SelfIdentityConfirmRequest(
                    action="confirm_different",
                    source_node=_make_source_node(dossiers_api),
                    target_participant_id=TARGET_PARTICIPANT_ID,
                    new_display_label="",
                ),
                current_user=AuthenticatedUser(user_id=USER_ID, username="owner"),
                db=DummyDB(),
            )
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "new_display_label is required for confirm_different"


def test_self_identity_confirm_non_owner_returns_not_found(
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
            dossiers_api.confirm_dossier_self_identity(
                dossier_id=DOSSIER_ID,
                payload=dossiers_api.SelfIdentityConfirmRequest(
                    action="confirm_same",
                    source_node=_make_source_node(dossiers_api),
                    target_participant_id=TARGET_PARTICIPANT_ID,
                ),
                current_user=AuthenticatedUser(user_id=9, username="other"),
                db=DummyDB(),
            )
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Dossier not found"


def test_self_identity_confirm_rejects_non_self_anchor_label(
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
            dossiers_api.confirm_dossier_self_identity(
                dossier_id=DOSSIER_ID,
                payload=dossiers_api.SelfIdentityConfirmRequest(
                    action="confirm_same",
                    source_node=_make_source_node(dossiers_api, raw_label="B\u516c\u53f8"),
                    target_participant_id=TARGET_PARTICIPANT_ID,
                ),
                current_user=AuthenticatedUser(user_id=USER_ID, username="owner"),
                db=DummyDB(),
            )
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "self identity confirmation only supports self anchor labels"


def test_self_identity_confirm_conflicts_when_source_node_already_maps_to_other_participant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()
    dossier = _make_dossier()
    target_participant = _make_target_participant()
    other_mention = _make_mention(participant_id=999, source="graph_node")

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        return dossier

    async def fake_get_dossier_participant_for_user(db, user_id: int, dossier_id: int, participant_id: int):
        return target_participant

    async def fake_list_dossier_participant_mentions(db, user_id: int, dossier_id: int):
        return [other_mention]

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dossiers_api, "get_dossier_for_user", fake_get_dossier_for_user)
    monkeypatch.setattr(
        dossiers_api,
        "get_dossier_participant_for_user",
        fake_get_dossier_participant_for_user,
    )
    monkeypatch.setattr(
        dossiers_api,
        "list_dossier_participant_mentions",
        fake_list_dossier_participant_mentions,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            dossiers_api.confirm_dossier_self_identity(
                dossier_id=DOSSIER_ID,
                payload=dossiers_api.SelfIdentityConfirmRequest(
                    action="confirm_same",
                    source_node=_make_source_node(dossiers_api),
                    target_participant_id=TARGET_PARTICIPANT_ID,
                ),
                current_user=AuthenticatedUser(user_id=USER_ID, username="owner"),
                db=DummyDB(),
            )
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Self anchor source node is already linked to another participant"


def test_self_identity_confirm_route_is_registered_in_openapi() -> None:
    dossiers_api = _load_dossiers_api_module()
    app = FastAPI()
    app.include_router(dossiers_api.router)

    schema = app.openapi()

    assert "/dossiers/{dossier_id}/intelligence/tasks/self-identity/confirm" in schema["paths"]
    assert "post" in schema["paths"]["/dossiers/{dossier_id}/intelligence/tasks/self-identity/confirm"]
