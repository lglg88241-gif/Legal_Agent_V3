from __future__ import annotations

import asyncio

import pytest

from app.core.security import AuthenticatedUser
from app.models import Conversation, Dossier, DossierParticipant, DossierParticipantMention
from app.services import dossier_graph_tool


class DummyDB:
    pass


def _conversation(*, conversation_id: int = 501, user_id: int = 7, dossier_id: int = 41) -> Conversation:
    return Conversation(
        id=conversation_id,
        user_id=user_id,
        dossier_id=dossier_id,
        session_id=f"session-{conversation_id}",
        title="Tracked conversation",
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )


def _dossier(*, dossier_id: int = 41, user_id: int = 7) -> Dossier:
    return Dossier(
        id=dossier_id,
        user_id=user_id,
        title="劳动争议测试案卷",
        case_type="劳动争议",
        summary="summary",
        status="active",
        participant_aliases_json=(
            '{"primary_party":{"label":"我","role":"劳动者","party_side":"claimant","confirmed_by_user":true},'
            '"opposing_party":{"label":"A公司","role":"用人单位","party_side":"opponent","confirmed_by_user":true}}'
        ),
    )


def _participant(
    *,
    participant_id: int = 9,
    dossier_id: int = 41,
    user_id: int = 7,
    label: str = "A公司",
    role: str = "用人单位",
    party_side: str = "opponent",
    entity_type: str = "organization",
) -> DossierParticipant:
    return DossierParticipant(
        id=participant_id,
        dossier_id=dossier_id,
        user_id=user_id,
        label=label,
        role=role,
        party_side=party_side,
        entity_type=entity_type,
        confirmed_by_user=True,
    )


def _mention(
    *,
    mention_id: int = 1,
    dossier_id: int = 41,
    conversation_id: int = 501,
    participant_id: int = 9,
    raw_label: str = "公司",
    source: str = "graph",
) -> DossierParticipantMention:
    return DossierParticipantMention(
        id=mention_id,
        dossier_id=dossier_id,
        conversation_id=conversation_id,
        participant_id=participant_id,
        raw_label=raw_label,
        source=source,
        confirmed_by_user=True,
    )


def _graph_payload(
    *,
    dossier_id: int = 41,
    warning: str | None = None,
    node_count: int = 2,
    edge_count: int = 1,
) -> dict[str, object]:
    nodes = [
        {
            "id": "node-company",
            "label": "公司",
            "raw_label": "公司",
            "display_label": "A公司",
            "type": "organization",
            "conversation_id": 501,
            "participant_id": 9,
            "properties": {"conversation_id": 501},
        },
        {
            "id": "node-event",
            "label": "违法解除",
            "type": "event",
            "conversation_id": 501,
            "properties": {"conversation_id": 501},
        },
    ]
    edges = [
        {
            "id": "edge-1",
            "source": "node-company",
            "target": "node-event",
            "type": "CASE_RELATION",
            "label": "通知解除",
            "properties": {"conversation_id": 501},
        }
    ]
    return {
        "status": "success",
        "dossier_id": dossier_id,
        "participant_aliases": {
            "primary_party": {
                "label": "我",
                "role": "劳动者",
                "party_side": "claimant",
                "confirmed_by_user": True,
            },
            "opposing_party": {
                "label": "A公司",
                "role": "用人单位",
                "party_side": "opponent",
                "confirmed_by_user": True,
            },
        },
        "confirmed_participants": [],
        "participant_mappings": [],
        "graph": {
            "nodes": nodes[:node_count],
            "edges": edges[:edge_count],
            "stats": {
                "conversation_count": 1,
                "node_count": node_count,
                "edge_count": edge_count,
            },
        },
        "warning": warning,
    }


def _install_scope_patches(
    monkeypatch: pytest.MonkeyPatch,
    *,
    conversation: Conversation | None = None,
    dossier: Dossier | None = None,
    participants: list[DossierParticipant] | None = None,
    mentions: list[DossierParticipantMention] | None = None,
    focused_participant: DossierParticipant | None = None,
    payload: dict[str, object] | None = None,
) -> None:
    async def fake_get_conversation_for_user(db, user_id: int, conversation_id: int):
        return conversation

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        return dossier

    async def fake_list_dossier_conversations(db, user_id: int, dossier_id: int):
        return [conversation] if conversation is not None else []

    async def fake_list_dossier_participants(db, user_id: int, dossier_id: int):
        return participants or []

    async def fake_list_dossier_participant_mentions(db, user_id: int, dossier_id: int):
        return mentions or []

    async def fake_get_dossier_participant_for_user(
        db,
        user_id: int,
        dossier_id: int,
        participant_id: int,
    ):
        return focused_participant

    async def fake_get_dossier_graph_payload(**kwargs):
        return payload or _graph_payload()

    monkeypatch.setattr(
        dossier_graph_tool,
        "get_conversation_for_user",
        fake_get_conversation_for_user,
    )
    monkeypatch.setattr(
        dossier_graph_tool,
        "get_dossier_for_user",
        fake_get_dossier_for_user,
    )
    monkeypatch.setattr(
        dossier_graph_tool,
        "list_dossier_conversations",
        fake_list_dossier_conversations,
    )
    monkeypatch.setattr(
        dossier_graph_tool,
        "list_dossier_participants",
        fake_list_dossier_participants,
    )
    monkeypatch.setattr(
        dossier_graph_tool,
        "list_dossier_participant_mentions",
        fake_list_dossier_participant_mentions,
    )
    monkeypatch.setattr(
        dossier_graph_tool,
        "get_dossier_participant_for_user",
        fake_get_dossier_participant_for_user,
    )
    monkeypatch.setattr(
        dossier_graph_tool,
        "get_dossier_graph_payload",
        fake_get_dossier_graph_payload,
    )


def test_query_dossier_graph_context_rejects_non_owner_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_scope_patches(
        monkeypatch,
        conversation=None,
        dossier=_dossier(),
    )

    with pytest.raises(ValueError, match="Conversation not found"):
        asyncio.run(
            dossier_graph_tool.query_dossier_graph_context(
                DummyDB(),
                current_user=AuthenticatedUser(user_id=7, username="owner"),
                conversation_id=501,
                dossier_id=41,
                query="",
            )
        )


def test_query_dossier_graph_context_rejects_non_owner_dossier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_scope_patches(
        monkeypatch,
        conversation=_conversation(),
        dossier=None,
    )

    with pytest.raises(ValueError, match="Dossier not found"):
        asyncio.run(
            dossier_graph_tool.query_dossier_graph_context(
                DummyDB(),
                current_user=AuthenticatedUser(user_id=7, username="owner"),
                conversation_id=501,
                dossier_id=41,
                query="",
            )
        )


def test_query_dossier_graph_context_rejects_conversation_dossier_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_scope_patches(
        monkeypatch,
        conversation=_conversation(dossier_id=88),
        dossier=_dossier(dossier_id=41),
    )

    with pytest.raises(ValueError, match="Conversation is not bound to the requested dossier"):
        asyncio.run(
            dossier_graph_tool.query_dossier_graph_context(
                DummyDB(),
                current_user=AuthenticatedUser(user_id=7, username="owner"),
                conversation_id=501,
                dossier_id=41,
                query="",
            )
        )


def test_query_dossier_graph_context_empty_query_returns_summary_structure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    participant = _participant()
    mention = _mention()
    _install_scope_patches(
        monkeypatch,
        conversation=_conversation(),
        dossier=_dossier(),
        participants=[participant],
        mentions=[mention],
        payload=_graph_payload(),
    )

    result = asyncio.run(
        dossier_graph_tool.query_dossier_graph_context(
            DummyDB(),
            current_user=AuthenticatedUser(user_id=7, username="owner"),
            conversation_id=501,
            dossier_id=41,
            query="",
        )
    )

    assert result["status"] == "success"
    assert result["query_type"] == "summary"
    assert result["source"] == "graph_context"
    assert result["legal_basis"] is False
    assert result["scope"] == {"user_id": 7, "dossier_id": 41, "conversation_id": 501}
    assert result["stats"] == {"conversation_count": 1, "node_count": 2, "edge_count": 1}
    assert result["participants"][0]["label"] == "A公司"
    assert result["matched_nodes"][0]["display_label"] == "A公司"
    assert result["matched_edges"][0]["label"] == "通知解除"
    assert str(result["graph_summary"]).startswith("<dossier_graph_summary>")


def test_query_dossier_graph_context_focus_participant_returns_mentions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    participant = _participant()
    mention = _mention(participant_id=participant.id)
    _install_scope_patches(
        monkeypatch,
        conversation=_conversation(),
        dossier=_dossier(),
        participants=[participant],
        mentions=[mention],
        focused_participant=participant,
        payload=_graph_payload(),
    )

    result = asyncio.run(
        dossier_graph_tool.query_dossier_graph_context(
            DummyDB(),
            current_user=AuthenticatedUser(user_id=7, username="owner"),
            conversation_id=501,
            dossier_id=41,
            query="查看这个主体",
            focus_participant_id=participant.id,
        )
    )

    assert result["query_type"] == "participant"
    assert result["participants"] == [dossier_graph_tool.serialize_dossier_participant(participant)]
    assert result["participant_mentions"][0]["raw_label"] == "公司"
    assert result["matched_nodes"][0]["display_label"] == "A公司"
    assert result["matched_edges"][0]["label"] == "通知解除"


def test_query_dossier_graph_context_rejects_focus_participant_outside_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_scope_patches(
        monkeypatch,
        conversation=_conversation(),
        dossier=_dossier(),
        focused_participant=None,
        payload=_graph_payload(),
    )

    with pytest.raises(ValueError, match="Dossier participant not found"):
        asyncio.run(
            dossier_graph_tool.query_dossier_graph_context(
                DummyDB(),
                current_user=AuthenticatedUser(user_id=7, username="owner"),
                conversation_id=501,
                dossier_id=41,
                query="查看主体",
                focus_participant_id=999,
            )
        )


def test_query_dossier_graph_context_keyword_query_filters_without_cypher_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    participant = _participant()
    mention = _mention(participant_id=participant.id)
    _install_scope_patches(
        monkeypatch,
        conversation=_conversation(),
        dossier=_dossier(),
        participants=[participant],
        mentions=[mention],
        payload=_graph_payload(),
    )

    result = asyncio.run(
        dossier_graph_tool.query_dossier_graph_context(
            DummyDB(),
            current_user=AuthenticatedUser(user_id=7, username="owner"),
            conversation_id=501,
            dossier_id=41,
            query="MATCH (n) RETURN n",
        )
    )

    assert result["query_type"] == "keyword"
    assert result["matched_nodes"] == []
    assert result["matched_edges"] == []
    assert result["participants"] == []


def test_query_dossier_graph_context_keyword_query_matches_labels_and_related_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    participant = _participant()
    mention = _mention(participant_id=participant.id)
    _install_scope_patches(
        monkeypatch,
        conversation=_conversation(),
        dossier=_dossier(),
        participants=[participant],
        mentions=[mention],
        payload=_graph_payload(),
    )

    result = asyncio.run(
        dossier_graph_tool.query_dossier_graph_context(
            DummyDB(),
            current_user=AuthenticatedUser(user_id=7, username="owner"),
            conversation_id=501,
            dossier_id=41,
            query="公司相关事实",
        )
    )

    assert result["query_type"] == "keyword"
    assert result["participants"][0]["label"] == "A公司"
    assert result["participant_mentions"][0]["raw_label"] == "公司"
    assert any(node.get("display_label") == "A公司" for node in result["matched_nodes"])
    assert any(edge.get("label") == "通知解除" for edge in result["matched_edges"])


def test_query_dossier_graph_context_clamps_limit_and_marks_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    many_nodes = [
        {
            "id": f"node-{idx}",
            "label": f"节点{idx}",
            "type": "entity",
            "conversation_id": 501,
            "properties": {"conversation_id": 501},
        }
        for idx in range(80)
    ]
    many_edges = [
        {
            "id": f"edge-{idx}",
            "source": many_nodes[idx]["id"],
            "target": many_nodes[min(idx + 1, 79)]["id"],
            "type": "CASE_RELATION",
            "label": f"关系{idx}",
            "properties": {"conversation_id": 501},
        }
        for idx in range(80)
    ]
    payload = {
        **_graph_payload(node_count=0, edge_count=0),
        "graph": {
            "nodes": many_nodes,
            "edges": many_edges,
            "stats": {
                "conversation_count": 1,
                "node_count": len(many_nodes),
                "edge_count": len(many_edges),
            },
        },
    }
    _install_scope_patches(
        monkeypatch,
        conversation=_conversation(),
        dossier=_dossier(),
        participants=[],
        mentions=[],
        payload=payload,
    )

    result = asyncio.run(
        dossier_graph_tool.query_dossier_graph_context(
            DummyDB(),
            current_user=AuthenticatedUser(user_id=7, username="owner"),
            conversation_id=501,
            dossier_id=41,
            query="",
            limit=999,
        )
    )

    assert result["limit"] == 50
    assert len(result["matched_nodes"]) == 50
    assert len(result["matched_edges"]) == 50
    assert result["truncated"] is True


def test_query_dossier_graph_context_preserves_graph_warning_in_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_scope_patches(
        monkeypatch,
        conversation=_conversation(),
        dossier=_dossier(),
        payload=_graph_payload(warning="Neo4j dossier graph is unavailable"),
    )

    result = asyncio.run(
        dossier_graph_tool.query_dossier_graph_context(
            DummyDB(),
            current_user=AuthenticatedUser(user_id=7, username="owner"),
            conversation_id=501,
            dossier_id=41,
            query="统计",
        )
    )

    assert result["warnings"] == ["Neo4j dossier graph is unavailable"]
    assert result["notes"][0] == "graph_context 仅代表案卷结构化背景，不构成法律依据。"
