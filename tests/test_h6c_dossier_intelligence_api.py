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


def _step(step: str, status: str = "success") -> dict[str, str]:
    return {"step": step, "status": status}


def _build_intelligence_response(*, summary_text: str, include_display_graph: bool) -> dict:
    steps = [_step("load_dossier")]
    steps.append(_step("load_display_graph") if include_display_graph else _step("skip_display_graph", "skipped"))
    steps.extend([_step("build_summary"), _step("build_confirmation_tasks")])
    return {
        "status": "success",
        "source": "dossier_intelligence",
        "scope": {
            "user_id": 3,
            "dossier_id": 51,
            "conversation_id": 674,
        },
        "summary": {
            "text": summary_text,
            "source": "auto_summary",
            "updated_at": "2026-04-28T09:30:00",
        },
        "confirmation_tasks": [],
        "display_graph_notes": [],
        "trace": {
            "steps": steps,
            "warnings": [],
        },
        "provider": "rules",
        "model": "dossier-intelligence-rules-v0",
        "fallback_reason": "llm_not_enabled_in_skeleton",
    }


def test_dossier_intelligence_owner_can_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()
    captured: dict[str, object] = {}

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_build_dossier_intelligence(
        db,
        *,
        current_user,
        dossier_id: int,
        conversation_id: int | None = None,
        include_display_graph: bool = True,
    ):
        captured["user_id"] = current_user.user_id
        captured["dossier_id"] = dossier_id
        captured["conversation_id"] = conversation_id
        captured["include_display_graph"] = include_display_graph
        return _build_intelligence_response(
            summary_text="系统自动摘要",
            include_display_graph=include_display_graph,
        )

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(
        dossiers_api,
        "build_dossier_intelligence",
        fake_build_dossier_intelligence,
    )

    result = asyncio.run(
        dossiers_api.get_dossier_intelligence(
            dossier_id=51,
            conversation_id=674,
            include_display_graph=True,
            current_user=AuthenticatedUser(user_id=3, username="owner"),
            db=DummyDB(),
        )
    )

    assert result["status"] == "success"
    assert result["source"] == "dossier_intelligence"
    assert result["provider"] == "rules"
    assert result["model"] == "dossier-intelligence-rules-v0"
    assert result["summary"]["text"] == "系统自动摘要"
    assert captured == {
        "user_id": 3,
        "dossier_id": 51,
        "conversation_id": 674,
        "include_display_graph": True,
    }


def test_dossier_intelligence_include_display_graph_false_still_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_build_dossier_intelligence(
        db,
        *,
        current_user,
        dossier_id: int,
        conversation_id: int | None = None,
        include_display_graph: bool = True,
    ):
        return _build_intelligence_response(
            summary_text="手写摘要",
            include_display_graph=include_display_graph,
        )

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(
        dossiers_api,
        "build_dossier_intelligence",
        fake_build_dossier_intelligence,
    )

    result = asyncio.run(
        dossiers_api.get_dossier_intelligence(
            dossier_id=51,
            conversation_id=None,
            include_display_graph=False,
            current_user=AuthenticatedUser(user_id=3, username="owner"),
            db=DummyDB(),
        )
    )

    assert result["summary"]["text"] == "手写摘要"
    assert any(step["step"] == "skip_display_graph" for step in result["trace"]["steps"])


def test_dossier_intelligence_non_owner_returns_project_style_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_build_dossier_intelligence(
        db,
        *,
        current_user,
        dossier_id: int,
        conversation_id: int | None = None,
        include_display_graph: bool = True,
    ):
        raise ValueError("Dossier not found")

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(
        dossiers_api,
        "build_dossier_intelligence",
        fake_build_dossier_intelligence,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            dossiers_api.get_dossier_intelligence(
                dossier_id=51,
                conversation_id=None,
                include_display_graph=True,
                current_user=AuthenticatedUser(user_id=9, username="other"),
                db=DummyDB(),
            )
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Dossier not found"


def test_dossier_intelligence_route_is_registered_in_openapi() -> None:
    dossiers_api = _load_dossiers_api_module()
    app = FastAPI()
    app.include_router(dossiers_api.router)

    schema = app.openapi()

    assert "/dossiers/{dossier_id}/intelligence" in schema["paths"]
    assert "get" in schema["paths"]["/dossiers/{dossier_id}/intelligence"]
