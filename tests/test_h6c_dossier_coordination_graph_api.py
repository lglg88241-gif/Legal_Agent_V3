from __future__ import annotations

import asyncio
import importlib
import sys
from types import ModuleType

import pytest
from fastapi import FastAPI, HTTPException

from app.core.security import AuthenticatedUser


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


def test_coordination_graph_owner_can_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_build_dossier_coordination_graph_for_user(
        db,
        *,
        current_user,
        dossier_id: int,
    ):
        assert current_user.user_id == 3
        assert dossier_id == 51
        return {
            "coordination_graph": {
                "entities": [
                    {
                        "id": "participant:12",
                        "label": "A公司",
                        "status": "confirmed",
                        "entity_type": "organization",
                        "source_conversation_ids": [674, 675],
                        "source_labels": ["A公司"],
                        "raw_node_ids": ["raw-674-a", "raw-675-a"],
                        "participant_id": 12,
                        "best_conversation_id": 674,
                        "best_conversation_reason": "该会话包含该主体最多的关系边",
                        "edge_count_by_conversation": {"674": 2, "675": 1},
                        "suggested_action": "view",
                    }
                ],
                "edges": [],
                "tasks": [],
                "stats": {
                    "bound_conversations": 2,
                    "connected_entities": 1,
                    "pending_identity": 0,
                    "pending_naming": 0,
                    "isolated_anchors": 0,
                },
                "trace": {
                    "mode": "rules",
                    "provider": "rules",
                    "model": "dossier-coordination-rules-v0",
                    "fallback_reason": "llm_not_enabled_in_skeleton",
                },
            },
            "generated_at": "2026-04-28T12:00:00+00:00",
        }

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(
        dossiers_api,
        "build_dossier_coordination_graph_for_user",
        fake_build_dossier_coordination_graph_for_user,
    )

    result = asyncio.run(
        dossiers_api.get_dossier_coordination_graph(
            dossier_id=51,
            current_user=AuthenticatedUser(user_id=3, username="owner"),
            db=DummyDB(),
        )
    )

    assert result["status"] == "success"
    assert result["dossier_id"] == 51
    assert result["view"] == "coordination"
    assert result["coordination_graph"]["stats"]["connected_entities"] == 1
    assert result["coordination_graph"]["trace"]["model"] == "dossier-coordination-rules-v0"
    assert result["generated_at"] == "2026-04-28T12:00:00+00:00"


def test_coordination_graph_non_owner_returns_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_build_dossier_coordination_graph_for_user(
        db,
        *,
        current_user,
        dossier_id: int,
    ):
        raise ValueError("Dossier not found")

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(
        dossiers_api,
        "build_dossier_coordination_graph_for_user",
        fake_build_dossier_coordination_graph_for_user,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            dossiers_api.get_dossier_coordination_graph(
                dossier_id=51,
                current_user=AuthenticatedUser(user_id=9, username="other"),
                db=DummyDB(),
            )
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Dossier not found"


def test_coordination_graph_route_is_registered_in_openapi() -> None:
    dossiers_api = _load_dossiers_api_module()
    app = FastAPI()
    app.include_router(dossiers_api.router)

    schema = app.openapi()

    assert "/dossiers/{dossier_id}/coordination-graph" in schema["paths"]
    assert "get" in schema["paths"]["/dossiers/{dossier_id}/coordination-graph"]
