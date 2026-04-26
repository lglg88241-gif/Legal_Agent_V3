from __future__ import annotations

import asyncio
import importlib
import sys
from types import ModuleType

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, inspect

from app.agent.nodes.advisor import _format_dossier_context_block
from app.core.security import AuthenticatedUser
from app.models import Conversation, Dossier, DossierParticipant
from app.services import dossier_graph
from app.services.conversation_store import (
    build_dossier_participant_candidates,
    ensure_storage_schema,
    resolve_dossier_participant_mentions,
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
        return None


def test_runtime_schema_creates_dossier_participant_tables() -> None:
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

        engine = FakeAsyncEngine()
        try:
            await ensure_storage_schema(bind_engine=engine)

            with sync_engine.begin() as sync_conn:
                inspector = inspect(sync_conn)
                tables = set(inspector.get_table_names())
                assert "dossier_participants" in tables
                assert "dossier_participant_mentions" in tables
        finally:
            sync_engine.dispose()

    asyncio.run(_run())


def test_create_dossier_participant_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()
    dossier = Dossier(
        id=8,
        user_id=3,
        title="劳动争议案卷",
        case_type="劳动争议",
        summary="summary",
        status="active",
    )

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        assert user_id == 3
        assert dossier_id == 8
        return dossier

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
        assert confirmed_by_user is True
        return DossierParticipant(
            id=21,
            dossier_id=dossier.id,
            user_id=user_id,
            label=label,
            role=role,
            party_side=party_side,
            entity_type=entity_type,
            description=description,
            confirmed_by_user=True,
        )

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dossiers_api, "get_dossier_for_user", fake_get_dossier_for_user)
    monkeypatch.setattr(
        dossiers_api,
        "create_dossier_participant_record",
        fake_create_dossier_participant_record,
    )

    result = asyncio.run(
        dossiers_api.create_dossier_participant(
            dossier_id=8,
            payload=dossiers_api.DossierParticipantCreateRequest(
                label="A公司",
                role="用人单位",
                party_side="opponent",
                entity_type="organization",
                description="用户确认过的案卷内对方主体",
            ),
            current_user=AuthenticatedUser(user_id=3, username="owner"),
            db=DummyDB(),
        )
    )

    assert result["status"] == "success"
    assert result["participant"]["label"] == "A公司"
    assert result["participant"]["party_side"] == "opponent"
    assert result["participant"]["entity_type"] == "organization"


def test_create_dossier_participant_archived_dossier_returns_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()
    dossier = Dossier(
        id=9,
        user_id=3,
        title="归档案卷",
        case_type="劳动争议",
        summary="summary",
        status="archived",
    )

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        return dossier

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dossiers_api, "get_dossier_for_user", fake_get_dossier_for_user)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            dossiers_api.create_dossier_participant(
                dossier_id=9,
                payload=dossiers_api.DossierParticipantCreateRequest(label="我"),
                current_user=AuthenticatedUser(user_id=3, username="owner"),
                db=DummyDB(),
            )
        )

    assert exc_info.value.status_code == 409


def test_update_dossier_participant_other_user_returns_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()
    dossier = Dossier(
        id=11,
        user_id=5,
        title="案卷",
        case_type="劳动争议",
        summary="summary",
        status="active",
    )

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        return dossier

    async def fake_get_dossier_participant_for_user(db, user_id: int, dossier_id: int, participant_id: int):
        return None

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dossiers_api, "get_dossier_for_user", fake_get_dossier_for_user)
    monkeypatch.setattr(
        dossiers_api,
        "get_dossier_participant_for_user",
        fake_get_dossier_participant_for_user,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            dossiers_api.update_dossier_participant(
                dossier_id=11,
                participant_id=42,
                payload=dossiers_api.DossierParticipantUpdateRequest(label="A公司"),
                current_user=AuthenticatedUser(user_id=5, username="owner"),
                db=DummyDB(),
            )
        )

    assert exc_info.value.status_code == 404


def test_build_dossier_participant_candidates_does_not_auto_confirm() -> None:
    candidates = build_dossier_participant_candidates(
        graph_payload={
            "graph": {
                "nodes": [
                    {"id": "1", "label": "公司", "conversation_id": 11, "properties": {}},
                    {"id": "2", "label": "用人单位", "conversation_id": 12, "properties": {}},
                    {"id": "3", "label": "老板", "conversation_id": 12, "properties": {}},
                ]
            }
        },
        participants=[],
        confirmed_mentions=[],
        participant_aliases={},
    )

    assert len(candidates) >= 2
    organization_candidate = next(
        candidate
        for candidate in candidates
        if candidate["candidate_key"] == "opponent_organization_cluster"
    )
    assert organization_candidate["existing_participant_id"] is None
    assert organization_candidate["raw_labels"] == ["公司", "用人单位"]
    assert "confirmed_by_user" not in organization_candidate


def test_get_dossier_graph_payload_applies_confirmed_participant_mappings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_to_thread(fn, *args, **kwargs):
        return {
            "nodes": [
                {
                    "id": "n1",
                    "label": "公司",
                    "type": "主体",
                    "conversation_id": 101,
                    "properties": {"conversation_id": 101},
                }
            ],
            "edges": [],
        }

    monkeypatch.setattr(dossier_graph.asyncio, "to_thread", fake_to_thread)

    result = asyncio.run(
        dossier_graph.get_dossier_graph_payload(
            user_id=9,
            dossier_id=5,
            conversation_ids=[101],
            participant_aliases={},
            confirmed_participants=[
                {
                    "id": 7,
                    "label": "A公司",
                    "role": "用人单位",
                    "party_side": "opponent",
                    "entity_type": "organization",
                }
            ],
            participant_mappings=[
                {
                    "participant_id": 7,
                    "participant_label": "A公司",
                    "participant_role": "用人单位",
                    "participant_party_side": "opponent",
                    "participant_entity_type": "organization",
                    "conversation_id": 101,
                    "raw_label": "公司",
                    "source": "graph",
                }
            ],
        )
    )

    assert result["participant_mappings"][0]["participant_label"] == "A公司"
    assert result["graph"]["nodes"][0]["display_label"] == "A公司"
    assert result["graph"]["nodes"][0]["raw_label"] == "公司"


def test_build_dossier_graph_summary_uses_confirmed_participant_labels() -> None:
    summary = dossier_graph.build_dossier_graph_summary(
        {
            "confirmed_participants": [
                {
                    "label": "A公司",
                    "role": "用人单位",
                    "party_side": "opponent",
                    "entity_type": "organization",
                }
            ],
            "graph": {
                "nodes": [
                    {
                        "id": "n1",
                        "label": "公司",
                        "display_label": "A公司",
                        "raw_label": "公司",
                        "type": "主体",
                        "conversation_id": 101,
                        "properties": {"conversation_id": 101},
                    }
                ],
                "edges": [],
                "stats": {
                    "conversation_count": 1,
                    "node_count": 1,
                    "edge_count": 0,
                },
            },
        }
    )

    assert summary is not None
    assert "A公司" in summary
    assert "raw=公司" in summary
    assert "未经用户确认的主体只能视为未确认 mention" in summary


def test_format_dossier_context_block_includes_confirmed_participants() -> None:
    block = _format_dossier_context_block(
        {
            "title": "劳动争议案卷",
            "case_type": "劳动争议",
            "summary": "围绕违法解除持续跟踪",
            "status": "active",
            "conversation_count": 1,
            "confirmed_participants": [
                {
                    "label": "A公司",
                    "role": "用人单位",
                    "party_side": "opponent",
                    "entity_type": "organization",
                }
            ],
            "participant_aliases": {
                "primary_party": {
                    "label": "我",
                    "role": "劳动者",
                    "party_side": "claimant",
                    "confirmed_by_user": True,
                }
            },
            "conversations": [
                {
                    "id": 101,
                    "title": "当前会话",
                    "conversation_mode": "TRACKED_CASE",
                    "persistence_consent": "tracked",
                    "case_summary": "用户咨询违法解除后的证据准备",
                }
            ],
        }
    )

    assert "confirmed_participants:" in block
    assert "label=A公司" in block
    assert "未确认 mention" in block
    assert "不同案卷中的“我”“公司”“对方”默认不是同一实体" in block


def test_resolve_dossier_participant_mentions_rejects_conversation_outside_dossier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossier = Dossier(
        id=15,
        user_id=7,
        title="案卷",
        case_type="劳动争议",
        summary="summary",
        status="active",
    )
    participant = DossierParticipant(
        id=99,
        dossier_id=15,
        user_id=7,
        label="A公司",
        role="用人单位",
        party_side="opponent",
        entity_type="organization",
        confirmed_by_user=True,
    )

    async def fake_list_user_conversations_by_ids(db, user_id: int, conversation_ids: list[int]):
        return [
            Conversation(
                id=conversation_ids[0],
                user_id=user_id,
                dossier_id=999,
                session_id="session-x",
                title="其他案卷会话",
                conversation_mode="TRACKED_CASE",
                persistence_consent="tracked",
            )
        ]

    async def fake_list_dossier_participant_mentions(db, user_id: int, dossier_id: int):
        return []

    monkeypatch.setattr(
        "app.services.conversation_store.list_user_conversations_by_ids",
        fake_list_user_conversations_by_ids,
    )
    monkeypatch.setattr(
        "app.services.conversation_store.list_dossier_participant_mentions",
        fake_list_dossier_participant_mentions,
    )

    with pytest.raises(ValueError) as exc_info:
        asyncio.run(
            resolve_dossier_participant_mentions(
                DummyDB(),
                dossier=dossier,
                user_id=7,
                participant=participant,
                mentions=[
                    {
                        "conversation_id": 44,
                        "raw_label": "公司",
                        "source": "graph",
                        "confidence": 0.92,
                    }
                ],
            )
        )

    assert "current user's dossier" in str(exc_info.value)
