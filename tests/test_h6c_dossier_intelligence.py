from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from app.models import Dossier, User
from app.services import dossier_intelligence

SELF_ANCHOR_LABEL = "\u5f53\u4e8b\u4eba"
SELF_SUGGESTED_LABEL = "\u52b3\u52a8\u8005\u672c\u4eba"


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
        title="\u52b3\u52a8\u4e89\u8bae\u6848\u5377",
        case_type="\u52b3\u52a8\u4e89\u8bae",
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


def _patch_display_graph_context(
    monkeypatch: pytest.MonkeyPatch,
    *,
    raw_nodes: list[dict] | None = None,
    display_nodes: list[dict] | None = None,
    confirmed_participants: list[dict] | None = None,
    participant_mappings: list[dict] | None = None,
    entity_resolution_decisions: list[dict] | None = None,
    participant_aliases: dict | None = None,
    raw_stats: dict | None = None,
    display_stats: dict | None = None,
) -> None:
    async def fake_load_display_graph_context(db, *, user_id: int, dossier: Dossier):
        assert user_id == 7
        assert dossier.id == 51
        return (
            {
                "participant_aliases": participant_aliases or {},
                "confirmed_participants": confirmed_participants or [],
                "participant_mappings": participant_mappings or [],
                "entity_resolution_decisions": entity_resolution_decisions or [],
                "graph": {
                    "stats": raw_stats or {"conversation_count": 1, "node_count": 0, "edge_count": 0},
                    "nodes": raw_nodes or [],
                    "edges": [],
                },
            },
            {
                "nodes": display_nodes or [],
                "edges": [],
                "stats": display_stats
                or {
                    "display_node_count": len(display_nodes or []),
                    "merged_participant_count": 0,
                    "pending_confirmation_count": len(display_nodes or []),
                },
            },
        )

    monkeypatch.setattr(
        dossier_intelligence,
        "_load_display_graph_context",
        fake_load_display_graph_context,
    )


def _step_names(result: dict) -> list[str]:
    return [
        step.get("step")
        for step in result["trace"]["steps"]
        if isinstance(step, dict)
    ]


def _party_naming_tasks(result: dict) -> list[dict]:
    return [
        task
        for task in result["confirmation_tasks"]
        if task.get("task_type") == "party_naming"
    ]


def _self_identity_tasks(result: dict) -> list[dict]:
    return [
        task
        for task in result["confirmation_tasks"]
        if task.get("task_type") == "entity_identity"
        and task.get("subject") == "self_anchor"
    ]


def test_build_dossier_intelligence_prefers_auto_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dossier_owner(
        monkeypatch,
        dossier=_make_dossier(auto_summary="\u7cfb\u7edf\u81ea\u52a8\u6458\u8981", summary="\u624b\u5199\u6458\u8981"),
    )

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
    assert result["summary"]["text"] == "\u7cfb\u7edf\u81ea\u52a8\u6458\u8981"
    assert result["summary"]["source"] == "auto_summary"
    assert result["provider"] == "rules"
    assert result["model"] == "dossier-intelligence-rules-v0"
    assert result["fallback_reason"] == "llm_not_enabled_in_skeleton"
    assert "load_dossier" in _step_names(result)
    assert "build_summary" in _step_names(result)
    assert "build_party_naming_tasks" in _step_names(result)
    assert "build_confirmation_tasks" in _step_names(result)


def test_build_dossier_intelligence_uses_manual_summary_when_auto_summary_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dossier_owner(monkeypatch, dossier=_make_dossier(summary="\u7528\u6237\u624b\u5199\u6458\u8981"))

    result = asyncio.run(
        dossier_intelligence.build_dossier_intelligence(
            DummyDB(),
            current_user=_make_user(),
            dossier_id=51,
            include_display_graph=False,
        )
    )

    assert result["summary"]["text"] == "\u7528\u6237\u624b\u5199\u6458\u8981"
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

    assert result["summary"]["text"] == "\u5f53\u524d\u6848\u5377\u6682\u65e0\u53ef\u7528\u6458\u8981\u3002"
    assert result["summary"]["source"] == "rule_fallback"
    assert "No dossier summary available" in result["trace"]["warnings"]


def test_build_dossier_intelligence_builds_display_graph_notes_from_stats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dossier_owner(monkeypatch, dossier=_make_dossier(auto_summary="\u7cfb\u7edf\u81ea\u52a8\u6458\u8981"))
    _patch_display_graph_context(
        monkeypatch,
        raw_stats={"conversation_count": 2, "node_count": 4, "edge_count": 3},
        display_nodes=[
            {"id": "participant:12", "source_conversation_ids": [674, 675]},
        ],
        display_stats={
            "display_node_count": 3,
            "merged_participant_count": 1,
            "pending_confirmation_count": 2,
        },
    )

    result = asyncio.run(
        dossier_intelligence.build_dossier_intelligence(
            DummyDB(),
            current_user=_make_user(),
            dossier_id=51,
        )
    )

    assert result["display_graph_notes"] == [
        "\u5df2\u786e\u8ba4\u4e3b\u4f53\u6570\u91cf\uff1a1\u3002",
        "\u5f85\u786e\u8ba4\u539f\u59cb\u5b9e\u4f53\u6570\u91cf\uff1a2\u3002",
        "\u6d89\u53ca\u4f1a\u8bdd\u6570\u91cf\uff1a2\u3002",
    ]
    assert "load_display_graph" in _step_names(result)
    assert _party_naming_tasks(result) == []


def test_build_dossier_intelligence_generates_party_naming_task_for_self_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dossier_owner(monkeypatch, dossier=_make_dossier(auto_summary="\u7cfb\u7edf\u81ea\u52a8\u6458\u8981"))
    _patch_display_graph_context(
        monkeypatch,
        raw_nodes=[
            {
                "id": "self-674",
                "label": SELF_ANCHOR_LABEL,
                "type": "person",
                "conversation_id": 674,
            }
        ],
        display_nodes=[
            {
                "id": "raw:self-674",
                "kind": "raw_entity",
                "display_label": SELF_ANCHOR_LABEL,
                "raw_labels": [SELF_ANCHOR_LABEL],
                "raw_node_ids": ["self-674"],
                "source_conversation_ids": [674],
            }
        ],
    )

    result = asyncio.run(
        dossier_intelligence.build_dossier_intelligence(
            DummyDB(),
            current_user=_make_user(),
            dossier_id=51,
        )
    )

    tasks = _party_naming_tasks(result)
    assert len(tasks) == 1
    task = tasks[0]
    assert task["subject"] == "self_anchor"
    assert task["current_label"] == SELF_ANCHOR_LABEL
    assert SELF_SUGGESTED_LABEL in task["suggested_labels"]
    assert task["requires_user_confirmation"] is True
    assert task["source"] == "dossier_intelligence_rules"
    assert task["evidence"]["raw_labels"] == [SELF_ANCHOR_LABEL]
    assert task["evidence"]["source_conversation_ids"] == [674]
    assert task["evidence"]["raw_node_ids"] == ["self-674"]
    assert result["provider"] == "rules"
    assert result["model"] == "dossier-intelligence-rules-v0"
    assert result["fallback_reason"] == "llm_not_enabled_in_skeleton"


def test_build_dossier_intelligence_does_not_generate_party_naming_without_self_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dossier_owner(monkeypatch, dossier=_make_dossier(auto_summary="\u7cfb\u7edf\u81ea\u52a8\u6458\u8981"))
    _patch_display_graph_context(
        monkeypatch,
        raw_nodes=[
            {
                "id": "company-674",
                "label": "A\u516c\u53f8",
                "type": "organization",
                "conversation_id": 674,
            }
        ],
        display_nodes=[
            {
                "id": "raw:company-674",
                "kind": "raw_entity",
                "display_label": "A\u516c\u53f8",
                "raw_labels": ["A\u516c\u53f8"],
                "raw_node_ids": ["company-674"],
                "source_conversation_ids": [674],
            }
        ],
    )

    result = asyncio.run(
        dossier_intelligence.build_dossier_intelligence(
            DummyDB(),
            current_user=_make_user(),
            dossier_id=51,
        )
    )

    assert _party_naming_tasks(result) == []


def test_build_dossier_intelligence_dedupes_party_naming_when_confirmed_self_participant_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dossier_owner(monkeypatch, dossier=_make_dossier(auto_summary="\u7cfb\u7edf\u81ea\u52a8\u6458\u8981"))
    _patch_display_graph_context(
        monkeypatch,
        raw_nodes=[
            {
                "id": "self-674",
                "label": SELF_ANCHOR_LABEL,
                "type": "person",
                "conversation_id": 674,
            }
        ],
        display_nodes=[
            {
                "id": "raw:self-674",
                "kind": "raw_entity",
                "display_label": SELF_ANCHOR_LABEL,
                "raw_labels": [SELF_ANCHOR_LABEL],
                "raw_node_ids": ["self-674"],
                "source_conversation_ids": [674],
            }
        ],
        confirmed_participants=[
            {
                "id": 21,
                "label": SELF_SUGGESTED_LABEL,
                "role": "",
                "party_side": "claimant",
                "entity_type": "person",
            }
        ],
    )

    result = asyncio.run(
        dossier_intelligence.build_dossier_intelligence(
            DummyDB(),
            current_user=_make_user(),
            dossier_id=51,
        )
    )

    assert _party_naming_tasks(result) == []


def test_build_dossier_intelligence_generates_self_identity_task_for_new_self_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dossier_owner(monkeypatch, dossier=_make_dossier(auto_summary="\u7cfb\u7edf\u81ea\u52a8\u6458\u8981"))
    _patch_display_graph_context(
        monkeypatch,
        raw_nodes=[
            {
                "id": "self-696",
                "label": SELF_ANCHOR_LABEL,
                "type": "person",
                "conversation_id": 696,
            },
            {
                "id": "self-697",
                "label": SELF_ANCHOR_LABEL,
                "type": "person",
                "conversation_id": 697,
            },
        ],
        display_nodes=[
            {
                "id": "participant:123",
                "kind": "participant",
                "display_label": "\u5f20\u4e09",
                "raw_labels": [SELF_ANCHOR_LABEL],
                "raw_node_ids": ["self-696"],
                "source_conversation_ids": [696],
            },
            {
                "id": "raw:self-697",
                "kind": "raw_entity",
                "display_label": SELF_ANCHOR_LABEL,
                "raw_labels": [SELF_ANCHOR_LABEL],
                "raw_node_ids": ["self-697"],
                "source_conversation_ids": [697],
            },
        ],
        confirmed_participants=[
            {
                "id": 123,
                "label": "\u5f20\u4e09",
                "role": "",
                "party_side": "claimant",
                "entity_type": "person",
            }
        ],
        participant_mappings=[
            {
                "participant_id": 123,
                "conversation_id": 696,
                "raw_label": SELF_ANCHOR_LABEL,
                "graph_node_id": "self-696",
                "graph_node_label": SELF_ANCHOR_LABEL,
                "graph_node_type": "person",
            }
        ],
        raw_stats={"conversation_count": 2, "node_count": 2, "edge_count": 0},
    )

    result = asyncio.run(
        dossier_intelligence.build_dossier_intelligence(
            DummyDB(),
            current_user=_make_user(),
            dossier_id=51,
        )
    )

    tasks = _self_identity_tasks(result)
    assert len(tasks) == 1
    task = tasks[0]
    assert task["title"] == "\u786e\u8ba4\u5f53\u4e8b\u4eba\u662f\u5426\u4e3a\u540c\u4e00\u4eba"
    assert task["source_node"] == {
        "conversation_id": 697,
        "raw_label": SELF_ANCHOR_LABEL,
        "graph_node_id": "self-697",
        "graph_node_label": SELF_ANCHOR_LABEL,
        "graph_node_type": "person",
    }
    assert task["target_participant"] == {
        "participant_id": 123,
        "display_label": "\u5f20\u4e09",
        "source_conversation_ids": [696],
    }
    assert task["confidence"] == "high"
    assert task["requires_user_confirmation"] is True
    assert task["suggested_action"] == "confirm_same_self"
    assert task["source"] == "dossier_intelligence_rules"
    assert "\u5f20\u4e09" in task["reason"]
    assert "build_self_identity_tasks" in _step_names(result)
    assert _party_naming_tasks(result) == []


def test_build_dossier_intelligence_does_not_generate_self_identity_task_for_single_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dossier_owner(monkeypatch, dossier=_make_dossier(auto_summary="\u7cfb\u7edf\u81ea\u52a8\u6458\u8981"))
    _patch_display_graph_context(
        monkeypatch,
        raw_nodes=[
            {
                "id": "self-696",
                "label": SELF_ANCHOR_LABEL,
                "type": "person",
                "conversation_id": 696,
            }
        ],
        confirmed_participants=[
            {
                "id": 123,
                "label": "\u5f20\u4e09",
                "role": "",
                "party_side": "claimant",
                "entity_type": "person",
            }
        ],
        participant_mappings=[
            {
                "participant_id": 123,
                "conversation_id": 696,
                "raw_label": SELF_ANCHOR_LABEL,
                "graph_node_id": "self-696",
                "graph_node_label": SELF_ANCHOR_LABEL,
                "graph_node_type": "person",
            }
        ],
        raw_stats={"conversation_count": 1, "node_count": 1, "edge_count": 0},
    )

    result = asyncio.run(
        dossier_intelligence.build_dossier_intelligence(
            DummyDB(),
            current_user=_make_user(),
            dossier_id=51,
        )
    )

    assert _self_identity_tasks(result) == []


def test_build_dossier_intelligence_prefers_party_naming_when_no_named_self_participant_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dossier_owner(monkeypatch, dossier=_make_dossier(auto_summary="\u7cfb\u7edf\u81ea\u52a8\u6458\u8981"))
    _patch_display_graph_context(
        monkeypatch,
        raw_nodes=[
            {
                "id": "self-696",
                "label": SELF_ANCHOR_LABEL,
                "type": "person",
                "conversation_id": 696,
            },
            {
                "id": "self-697",
                "label": SELF_ANCHOR_LABEL,
                "type": "person",
                "conversation_id": 697,
            },
        ],
        display_nodes=[
            {
                "id": "raw:self-696",
                "kind": "raw_entity",
                "display_label": SELF_ANCHOR_LABEL,
                "raw_labels": [SELF_ANCHOR_LABEL],
                "raw_node_ids": ["self-696"],
                "source_conversation_ids": [696],
            },
            {
                "id": "raw:self-697",
                "kind": "raw_entity",
                "display_label": SELF_ANCHOR_LABEL,
                "raw_labels": [SELF_ANCHOR_LABEL],
                "raw_node_ids": ["self-697"],
                "source_conversation_ids": [697],
            },
        ],
        raw_stats={"conversation_count": 2, "node_count": 2, "edge_count": 0},
    )

    result = asyncio.run(
        dossier_intelligence.build_dossier_intelligence(
            DummyDB(),
            current_user=_make_user(),
            dossier_id=51,
        )
    )

    assert _self_identity_tasks(result) == []
    assert len(_party_naming_tasks(result)) == 1


def test_build_dossier_intelligence_skips_self_identity_task_when_node_is_already_mapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dossier_owner(monkeypatch, dossier=_make_dossier(auto_summary="\u7cfb\u7edf\u81ea\u52a8\u6458\u8981"))
    _patch_display_graph_context(
        monkeypatch,
        raw_nodes=[
            {
                "id": "self-696",
                "label": SELF_ANCHOR_LABEL,
                "type": "person",
                "conversation_id": 696,
            },
            {
                "id": "self-697",
                "label": SELF_ANCHOR_LABEL,
                "type": "person",
                "conversation_id": 697,
            },
        ],
        confirmed_participants=[
            {
                "id": 123,
                "label": "\u5f20\u4e09",
                "role": "",
                "party_side": "claimant",
                "entity_type": "person",
            }
        ],
        participant_mappings=[
            {
                "participant_id": 123,
                "conversation_id": 696,
                "raw_label": SELF_ANCHOR_LABEL,
                "graph_node_id": "self-696",
                "graph_node_label": SELF_ANCHOR_LABEL,
                "graph_node_type": "person",
            },
            {
                "participant_id": 123,
                "conversation_id": 697,
                "raw_label": SELF_ANCHOR_LABEL,
                "graph_node_id": "self-697",
                "graph_node_label": SELF_ANCHOR_LABEL,
                "graph_node_type": "person",
            },
        ],
        raw_stats={"conversation_count": 2, "node_count": 2, "edge_count": 0},
    )

    result = asyncio.run(
        dossier_intelligence.build_dossier_intelligence(
            DummyDB(),
            current_user=_make_user(),
            dossier_id=51,
        )
    )

    assert _self_identity_tasks(result) == []


def test_build_dossier_intelligence_excludes_non_person_nodes_from_self_identity_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dossier_owner(monkeypatch, dossier=_make_dossier(auto_summary="\u7cfb\u7edf\u81ea\u52a8\u6458\u8981"))
    _patch_display_graph_context(
        monkeypatch,
        raw_nodes=[
            {
                "id": "self-696",
                "label": SELF_ANCHOR_LABEL,
                "type": "person",
                "conversation_id": 696,
            },
            {
                "id": "event-697",
                "label": SELF_ANCHOR_LABEL,
                "type": "event",
                "conversation_id": 697,
            },
            {
                "id": "document-698",
                "label": SELF_ANCHOR_LABEL,
                "type": "document",
                "conversation_id": 698,
            },
        ],
        confirmed_participants=[
            {
                "id": 123,
                "label": "\u5f20\u4e09",
                "role": "",
                "party_side": "claimant",
                "entity_type": "person",
            }
        ],
        participant_mappings=[
            {
                "participant_id": 123,
                "conversation_id": 696,
                "raw_label": SELF_ANCHOR_LABEL,
                "graph_node_id": "self-696",
                "graph_node_label": SELF_ANCHOR_LABEL,
                "graph_node_type": "person",
            }
        ],
        raw_stats={"conversation_count": 3, "node_count": 3, "edge_count": 0},
    )

    result = asyncio.run(
        dossier_intelligence.build_dossier_intelligence(
            DummyDB(),
            current_user=_make_user(),
            dossier_id=51,
        )
    )

    assert _self_identity_tasks(result) == []


def test_build_dossier_intelligence_skips_self_identity_task_after_not_same_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dossier_owner(monkeypatch, dossier=_make_dossier(auto_summary="\u7cfb\u7edf\u81ea\u52a8\u6458\u8981"))
    _patch_display_graph_context(
        monkeypatch,
        raw_nodes=[
            {
                "id": "self-696",
                "label": SELF_ANCHOR_LABEL,
                "type": "person",
                "conversation_id": 696,
            },
            {
                "id": "self-697",
                "label": SELF_ANCHOR_LABEL,
                "type": "person",
                "conversation_id": 697,
            },
        ],
        confirmed_participants=[
            {
                "id": 123,
                "label": "\u5f20\u4e09",
                "role": "",
                "party_side": "claimant",
                "entity_type": "person",
            }
        ],
        participant_mappings=[
            {
                "participant_id": 123,
                "conversation_id": 696,
                "raw_label": SELF_ANCHOR_LABEL,
                "graph_node_id": "self-696",
                "graph_node_label": SELF_ANCHOR_LABEL,
                "graph_node_type": "person",
            }
        ],
        entity_resolution_decisions=[
            {
                "decision_type": "not_same",
                "subject": "self_anchor",
                "source_conversation_id": 697,
                "source_graph_node_id": "self-697",
                "source_raw_label": SELF_ANCHOR_LABEL,
                "target_participant_id": 123,
                "target_display_label": "\u5f20\u4e09",
                "new_participant_id": 321,
                "new_display_label": "\u674e\u56db",
            }
        ],
        raw_stats={"conversation_count": 2, "node_count": 2, "edge_count": 0},
    )

    result = asyncio.run(
        dossier_intelligence.build_dossier_intelligence(
            DummyDB(),
            current_user=_make_user(),
            dossier_id=51,
        )
    )

    assert _self_identity_tasks(result) == []


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
