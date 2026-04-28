from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from app.models import Dossier, User
from app.services import dossier_intelligence


class DummyDB:
    pass


def _make_user(user_id: int = 7) -> User:
    return User(id=user_id, username=f"user-{user_id}", hashed_password="hashed")


def _make_dossier(
    *,
    auto_summary: str | None = None,
    summary: str | None = None,
) -> Dossier:
    return Dossier(
        id=51,
        user_id=7,
        title="劳动争议案卷",
        case_type="劳动争议",
        summary=summary,
        auto_summary=auto_summary,
        auto_summary_updated_at=datetime(2026, 4, 28, 9, 30, 0) if auto_summary else None,
        status="active",
        updated_at=datetime(2026, 4, 28, 10, 0, 0),
    )


def _patch_dossier_owner(
    monkeypatch: pytest.MonkeyPatch,
    *,
    dossier: Dossier | None,
) -> None:
    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        assert dossier_id == 51
        return dossier

    monkeypatch.setattr(dossier_intelligence, "get_dossier_for_user", fake_get_dossier_for_user)


def _step_names(result: dict) -> list[str]:
    return [
        step.get("step")
        for step in result["trace"]["steps"]
        if isinstance(step, dict)
    ]


def test_build_dossier_intelligence_prefers_auto_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dossier_owner(monkeypatch, dossier=_make_dossier(auto_summary="系统自动摘要", summary="手写摘要"))

    result = asyncio.run(
        dossier_intelligence.build_dossier_intelligence(
            DummyDB(),
            current_user=_make_user(),
            dossier_id=51,
            include_display_graph=False,
        )
    )

    assert result["status"] == "success"
    assert result["source"] == "dossier_intelligence"
    assert result["summary"]["text"] == "系统自动摘要"
    assert result["summary"]["source"] == "auto_summary"
    assert result["provider"] == "rules"
    assert result["model"] == "dossier-intelligence-rules-v0"
    assert result["fallback_reason"] == "llm_not_enabled_in_skeleton"
    assert "load_dossier" in _step_names(result)
    assert "build_summary" in _step_names(result)


def test_build_dossier_intelligence_uses_manual_summary_when_auto_summary_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dossier_owner(monkeypatch, dossier=_make_dossier(summary="用户手写摘要"))

    result = asyncio.run(
        dossier_intelligence.build_dossier_intelligence(
            DummyDB(),
            current_user=_make_user(),
            dossier_id=51,
            include_display_graph=False,
        )
    )

    assert result["summary"]["text"] == "用户手写摘要"
    assert result["summary"]["source"] == "manual_summary"
    assert result["trace"]["warnings"] == []


def test_build_dossier_intelligence_returns_readable_fallback_when_no_summary_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dossier_owner(monkeypatch, dossier=_make_dossier())

    result = asyncio.run(
        dossier_intelligence.build_dossier_intelligence(
            DummyDB(),
            current_user=_make_user(),
            dossier_id=51,
            include_display_graph=False,
        )
    )

    assert result["summary"]["text"] == "当前案卷暂无可用摘要。"
    assert result["summary"]["source"] == "rule_fallback"
    assert "No dossier summary available" in result["trace"]["warnings"]


def test_build_dossier_intelligence_builds_display_graph_notes_from_stats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dossier_owner(monkeypatch, dossier=_make_dossier(auto_summary="系统自动摘要"))

    async def fake_load_display_graph_context(db, *, user_id: int, dossier: Dossier):
        assert user_id == 7
        assert dossier.id == 51
        return (
            {
                "graph": {
                    "stats": {
                        "conversation_count": 2,
                        "node_count": 4,
                        "edge_count": 3,
                    }
                }
            },
            {
                "nodes": [
                    {"id": "participant:12", "source_conversation_ids": [674, 675]},
                ],
                "edges": [],
                "stats": {
                    "display_node_count": 3,
                    "merged_participant_count": 1,
                    "pending_confirmation_count": 2,
                },
            },
        )

    monkeypatch.setattr(
        dossier_intelligence,
        "_load_display_graph_context",
        fake_load_display_graph_context,
    )

    result = asyncio.run(
        dossier_intelligence.build_dossier_intelligence(
            DummyDB(),
            current_user=_make_user(),
            dossier_id=51,
        )
    )

    assert result["display_graph_notes"] == [
        "已确认主体数量：1。",
        "待确认原始实体数量：2。",
        "涉及会话数量：2。",
    ]
    assert "load_display_graph" in _step_names(result)
    assert result["confirmation_tasks"] == []


def test_build_dossier_intelligence_rejects_non_owner_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dossier_owner(monkeypatch, dossier=None)

    with pytest.raises(ValueError, match="Dossier not found"):
        asyncio.run(
            dossier_intelligence.build_dossier_intelligence(
                DummyDB(),
                current_user=_make_user(user_id=9),
                dossier_id=51,
                include_display_graph=False,
            )
        )
