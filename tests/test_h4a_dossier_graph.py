from __future__ import annotations

import asyncio
import importlib
import sys
from types import ModuleType

import pytest
from fastapi import HTTPException

from app.core.security import AuthenticatedUser
from app.models import Conversation, Dossier
from app.services import dossier_graph


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
    async def commit(self) -> None:
        return None

    async def refresh(self, obj) -> None:
        return None


def test_get_dossier_graph_other_user_returns_not_found(
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
            dossiers_api.get_dossier_graph(
                dossier_id=17,
                current_user=AuthenticatedUser(user_id=3, username="owner"),
                db=DummyDB(),
            )
        )

    assert exc_info.value.status_code == 404


def test_get_dossier_graph_without_conversations_returns_empty_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()
    dossier = Dossier(
        id=21,
        user_id=5,
        title="劳动争议案卷",
        case_type="劳动争议",
        summary="summary",
        status="active",
        participant_aliases_json='{"primary_party":{"label":"我","role":"劳动者","party_side":"claimant","confirmed_by_user":true}}',
    )

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        return dossier

    async def fake_list_dossier_conversations(db, user_id: int, dossier_id: int):
        assert user_id == 5
        assert dossier_id == 21
        return []

    async def fake_list_dossier_participants(db, user_id: int, dossier_id: int):
        return []

    async def fake_list_dossier_participant_mentions(db, user_id: int, dossier_id: int):
        return []

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dossiers_api, "get_dossier_for_user", fake_get_dossier_for_user)
    monkeypatch.setattr(dossiers_api, "list_dossier_conversations", fake_list_dossier_conversations)
    monkeypatch.setattr(dossiers_api, "list_dossier_participants", fake_list_dossier_participants)
    monkeypatch.setattr(
        dossiers_api,
        "list_dossier_participant_mentions",
        fake_list_dossier_participant_mentions,
    )

    result = asyncio.run(
        dossiers_api.get_dossier_graph(
            dossier_id=21,
            current_user=AuthenticatedUser(user_id=5, username="owner"),
            db=DummyDB(),
        )
    )

    assert result["status"] == "success"
    assert result["dossier_id"] == 21
    assert result["participant_aliases"]["primary_party"]["label"] == "我"
    assert result["graph"]["nodes"] == []
    assert result["graph"]["edges"] == []
    assert result["graph"]["stats"] == {
        "conversation_count": 0,
        "node_count": 0,
        "edge_count": 0,
    }


def test_get_dossier_graph_passes_only_bound_conversation_ids_and_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()
    dossier = Dossier(
        id=31,
        user_id=8,
        title="案卷",
        case_type="劳动争议",
        summary="summary",
        status="active",
        participant_aliases_json='{"opposing_party":{"label":"公司","role":"用人单位","party_side":"opponent","confirmed_by_user":true}}',
    )
    conversations = [
        Conversation(
            id=201,
            user_id=8,
            dossier_id=31,
            session_id="session-201",
            title="第一次咨询",
            conversation_mode="TRACKED_CASE",
            persistence_consent="tracked",
        ),
        Conversation(
            id=202,
            user_id=8,
            dossier_id=31,
            session_id="session-202",
            title="补充证据",
            conversation_mode="CASE_ANALYSIS",
            persistence_consent="temporary",
        ),
    ]
    captured: dict[str, object] = {}

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        return dossier

    async def fake_list_dossier_conversations(db, user_id: int, dossier_id: int):
        assert user_id == 8
        assert dossier_id == 31
        return conversations

    async def fake_list_dossier_participants(db, user_id: int, dossier_id: int):
        return []

    async def fake_list_dossier_participant_mentions(db, user_id: int, dossier_id: int):
        return []

    async def fake_get_dossier_graph_payload(
        *,
        user_id: int,
        dossier_id: int,
        conversation_ids: list[int],
        participant_aliases: dict[str, object] | None = None,
        confirmed_participants: list[dict[str, object]] | None = None,
        participant_mappings: list[dict[str, object]] | None = None,
    ):
        captured["user_id"] = user_id
        captured["dossier_id"] = dossier_id
        captured["conversation_ids"] = conversation_ids
        captured["participant_aliases"] = participant_aliases
        captured["confirmed_participants"] = confirmed_participants
        captured["participant_mappings"] = participant_mappings
        return {
            "status": "success",
            "dossier_id": dossier_id,
            "participant_aliases": participant_aliases or {},
            "confirmed_participants": confirmed_participants or [],
            "participant_mappings": participant_mappings or [],
            "graph": {
                "nodes": [],
                "edges": [],
                "stats": {
                    "conversation_count": len(conversation_ids),
                    "node_count": 0,
                    "edge_count": 0,
                },
            },
        }

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dossiers_api, "get_dossier_for_user", fake_get_dossier_for_user)
    monkeypatch.setattr(dossiers_api, "list_dossier_conversations", fake_list_dossier_conversations)
    monkeypatch.setattr(dossiers_api, "list_dossier_participants", fake_list_dossier_participants)
    monkeypatch.setattr(
        dossiers_api,
        "list_dossier_participant_mentions",
        fake_list_dossier_participant_mentions,
    )
    monkeypatch.setattr(dossiers_api, "get_dossier_graph_payload", fake_get_dossier_graph_payload)

    result = asyncio.run(
        dossiers_api.get_dossier_graph(
            dossier_id=31,
            current_user=AuthenticatedUser(user_id=8, username="owner"),
            db=DummyDB(),
        )
    )

    assert captured == {
        "user_id": 8,
        "dossier_id": 31,
        "conversation_ids": [201, 202],
        "participant_aliases": {
            "opposing_party": {
                "label": "公司",
                "role": "用人单位",
                "party_side": "opponent",
                "confirmed_by_user": True,
            }
        },
        "confirmed_participants": [],
        "participant_mappings": [],
    }
    assert result["graph"]["stats"]["conversation_count"] == 2


def test_fetch_dossier_graph_sync_uses_user_and_conversation_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, object] | None]] = []

    class FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def data(self):
            return self._rows

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def run(self, query: str, params: dict[str, object]):
            calls.append((query, params))
            assert params["user_id"] == 9
            assert params["conversation_ids"] == [101, 102]
            if "RETURN id(n) AS node_id" in query:
                return FakeResult(
                    [
                        {
                            "node_id": 1,
                            "labels": ["DynamicCaseEntity", "主体"],
                            "properties": {
                                "name": "公司",
                                "category": "主体",
                                "conversation_id": 101,
                                "user_id": 9,
                            },
                        },
                        {
                            "node_id": 2,
                            "labels": ["DynamicCaseEntity"],
                            "properties": {
                                "title": "违法辞退",
                                "node_type": "事件",
                                "conversation_id": 102,
                                "user_id": 9,
                            },
                        },
                    ]
                )
            return FakeResult(
                [
                    {
                        "edge_id": 5,
                        "source_id": 1,
                        "target_id": 2,
                        "edge_type": "CASE_RELATION",
                        "properties": {
                            "relation": "涉及",
                            "conversation_id": 101,
                            "user_id": 9,
                        },
                    }
                ]
            )

    class FakeDriver:
        def session(self):
            return FakeSession()

        def close(self):
            calls.append(("close", None))

    monkeypatch.setattr(dossier_graph, "_build_driver", lambda: FakeDriver())

    result = dossier_graph._fetch_dossier_graph_sync(9, [101, 102, 101, 0, -1])  # noqa: SLF001

    assert len(result["nodes"]) == 2
    assert result["nodes"][0]["id"] == "1"
    assert result["nodes"][0]["label"] == "公司"
    assert result["nodes"][0]["type"] == "主体"
    assert result["nodes"][1]["label"] == "违法辞退"
    assert result["nodes"][1]["type"] == "事件"
    assert result["edges"] == [
        {
            "id": "5",
            "source": "1",
            "target": "2",
            "type": "CASE_RELATION",
            "label": "涉及",
            "properties": {
                "relation": "涉及",
                "conversation_id": 101,
                "user_id": 9,
            },
        }
    ]
    assert sum(1 for query, _ in calls if isinstance(query, str) and "RETURN id(" in query) == 2
    assert calls[-1] == ("close", None)


def test_get_dossier_graph_payload_returns_warning_when_neo4j_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_to_thread(fn, *args, **kwargs):
        raise RuntimeError("neo4j down")

    monkeypatch.setattr(dossier_graph.asyncio, "to_thread", fake_to_thread)

    result = asyncio.run(
        dossier_graph.get_dossier_graph_payload(
            user_id=6,
            dossier_id=44,
            conversation_ids=[301, 302, 301],
            participant_aliases={
                "primary_party": {
                    "label": "我",
                    "role": "劳动者",
                    "party_side": "claimant",
                    "confirmed_by_user": True,
                }
            },
        )
    )

    assert result["status"] == "success"
    assert result["warning"] == "Neo4j dossier graph is unavailable"
    assert result["participant_aliases"]["primary_party"]["label"] == "我"
    assert result["graph"]["stats"] == {
        "conversation_count": 2,
        "node_count": 0,
        "edge_count": 0,
    }
    assert result["graph"]["nodes"] == []
    assert result["graph"]["edges"] == []
