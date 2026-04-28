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
                        "id": "participant:11",
                        "label": "张三",
                        "status": "confirmed",
                        "entity_type": "person",
                        "source_conversation_ids": [696, 697],
                        "source_labels": ["张三", "当事人"],
                        "raw_node_ids": ["raw-696-self", "raw-697-self"],
                        "participant_id": 11,
                        "best_conversation_id": 697,
                        "best_conversation_reason": "该会话包含该主体最多的关系边",
                        "edge_count_by_conversation": {"696": 1, "697": 2},
                        "suggested_action": "view",
                    },
                    {
                        "id": "participant:12",
                        "label": "B公司",
                        "status": "confirmed",
                        "entity_type": "organization",
                        "source_conversation_ids": [696, 697],
                        "source_labels": ["B公司"],
                        "raw_node_ids": ["raw-696-company", "raw-697-company"],
                        "participant_id": 12,
                        "best_conversation_id": 696,
                        "best_conversation_reason": "该会话包含该主体最多的关系边",
                        "edge_count_by_conversation": {"696": 3, "697": 1},
                        "suggested_action": "view",
                    },
                ],
                "edges": [
                    {
                        "id": "employment:participant:11:participant:12",
                        "source": "participant:11",
                        "target": "participant:12",
                        "label": "劳动关系/社保争议",
                        "relation_type": "employment_dispute",
                        "source_conversation_ids": [696, 697],
                        "evidence": [
                            {
                                "conversation_id": 696,
                                "summary": "“张三”与“B公司”共同关联“社保”，形成“劳动关系/社保争议”线索。",
                                "raw_node_ids": ["raw-696-self", "raw-696-company", "raw-696-social-insurance"],
                                "raw_edge_ids": ["raw-edge-1", "raw-edge-2"],
                            }
                        ],
                        "confidence": "high",
                    }
                ],
                "tasks": [],
                "stats": {
                    "bound_conversations": 2,
                    "connected_entities": 2,
                    "pending_identity": 0,
                    "pending_naming": 0,
                    "isolated_anchors": 0,
                },
                "trace": {
                    "mode": "rules",
                    "provider": "rules",
                    "model": "dossier-coordination-rules-v1",
                    "fallback_reason": "llm_not_enabled_in_responsible_entity_graph",
                    "steps": [
                        "load_raw_dossier_graph",
                        "load_participant_mappings",
                        "filter_responsible_entities",
                        "derive_subject_relations",
                        "attach_evidence",
                        "build_confirmation_tasks",
                    ],
                    "warnings": [],
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
    assert {entity["label"] for entity in result["coordination_graph"]["entities"]} == {"张三", "B公司"}
    assert all(
        entity["label"] not in {"劳动合同", "社保", "月薪8000", "人事通知", "确认书"}
        for entity in result["coordination_graph"]["entities"]
    )
    assert result["coordination_graph"]["edges"]
    assert result["coordination_graph"]["trace"]["model"] == "dossier-coordination-rules-v1"
    assert (
        result["coordination_graph"]["trace"]["fallback_reason"]
        == "llm_not_enabled_in_responsible_entity_graph"
    )
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
