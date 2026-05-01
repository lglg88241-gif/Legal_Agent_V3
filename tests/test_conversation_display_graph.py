from __future__ import annotations

import asyncio
import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest

from app.core.security import AuthenticatedUser
from app.models.entities import Conversation
from app.services.conversation_display_graph import build_conversation_display_graph
from app.services.conversation_entity_state import build_conversation_entity_state


def _build_raw_graph() -> dict:
    return {
        "query": "我叫张三，是这起劳动争议里的劳动者。",
        "nodes": [
            {
                "id": "node_1",
                "label": "当事人",
                "type": "person",
                "conversation_id": 123,
                "properties": {},
            },
            {
                "id": "node_2",
                "label": "张三",
                "type": "person",
                "conversation_id": 123,
                "properties": {"role": "current_party"},
            },
            {
                "id": "node_3",
                "label": "B公司",
                "type": "organization",
                "conversation_id": 123,
                "properties": {},
            },
        ],
        "edges": [
            {
                "id": "edge_1",
                "source": "node_1",
                "target": "node_2",
                "label": "称为",
                "type": "identity",
                "conversation_id": 123,
            },
            {
                "id": "edge_2",
                "source": "node_2",
                "target": "node_3",
                "label": "签订劳动合同",
                "type": "contract",
                "conversation_id": 123,
            },
        ],
        "generated_at": "2026-04-30T00:00:00Z",
    }


def _build_entity_state(raw_graph: dict) -> dict:
    return build_conversation_entity_state(
        conversation_id=123,
        message_text="我叫张三，是这起劳动争议里的劳动者。我和B公司签了劳动合同。",
        raw_graph_payload={"graph": raw_graph},
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )


def _load_conversations_api_module():
    fake_graph_extractor = ModuleType("app.services.graph_extractor")
    fake_graph_extractor.delete_case_graph = lambda conversation_id: {"success": True}
    fake_graph_extractor.get_case_graph = None
    sys.modules["app.services.graph_extractor"] = fake_graph_extractor

    sys.modules.pop("app.api.conversations", None)
    return importlib.import_module("app.api.conversations")


def test_build_conversation_display_graph_merges_self_anchor_into_real_name() -> None:
    raw_graph = _build_raw_graph()
    entity_state = _build_entity_state(raw_graph)

    result = build_conversation_display_graph(raw_graph, entity_state)

    labels = [node.get("label") for node in result["nodes"]]
    assert labels.count("张三") == 1
    assert "当事人" not in labels

    zhang_san = next(node for node in result["nodes"] if node.get("label") == "张三")
    assert zhang_san["id"] == "node_2"
    assert sorted(zhang_san.get("raw_node_ids", [])) == ["node_1", "node_2"]
    assert "当事人" in zhang_san.get("raw_labels", [])
    assert "张三" in zhang_san.get("raw_labels", [])

    assert len(result["edges"]) == 1
    edge = result["edges"][0]
    assert edge["source"] == "node_2"
    assert edge["target"] == "node_3"
    assert edge["label"] == "签订劳动合同"

    assert len(raw_graph["nodes"]) == 3
    assert len(raw_graph["edges"]) == 2


def test_build_conversation_display_graph_keeps_raw_graph_when_name_not_resolved() -> None:
    raw_graph = {
        "nodes": [
            {"id": "node_1", "label": "当事人", "type": "person", "conversation_id": 123},
            {"id": "node_2", "label": "B公司", "type": "organization", "conversation_id": 123},
        ],
        "edges": [
            {"id": "edge_1", "source": "node_1", "target": "node_2", "label": "劳动争议"},
        ],
    }
    entity_state = build_conversation_entity_state(
        conversation_id=123,
        message_text="我是劳动者，和B公司有劳动争议。",
        raw_graph_payload={"graph": raw_graph},
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )

    result = build_conversation_display_graph(raw_graph, entity_state)

    labels = [node.get("label") for node in result["nodes"]]
    assert labels == ["当事人", "B公司"]
    assert result["edges"][0]["source"] == "node_1"
    assert result["edges"][0]["target"] == "node_2"


def test_get_conversation_graph_returns_merged_display_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    conversations_api = _load_conversations_api_module()
    raw_graph = _build_raw_graph()

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_conversation_for_user(db, user_id: int, conversation_id: int):
        return Conversation(
            id=conversation_id,
            user_id=user_id,
            session_id="session-123",
            title="case",
            conversation_mode="TRACKED_CASE",
            persistence_consent="tracked",
        )

    async def fake_get_case_graph(conversation_id: int):
        assert conversation_id == 123
        return raw_graph

    monkeypatch.setattr(conversations_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(conversations_api, "get_conversation_for_user", fake_get_conversation_for_user)
    monkeypatch.setattr(conversations_api, "get_case_graph", fake_get_case_graph)

    result = asyncio.run(
        conversations_api.get_conversation_graph(
            conversation_id=123,
            current_user=AuthenticatedUser(user_id=9, username="alice"),
            db=SimpleNamespace(),
        )
    )

    labels = [node.get("label") for node in result["nodes"]]
    assert result["status"] == "success"
    assert result["conversation_id"] == 123
    assert labels.count("张三") == 1
    assert "当事人" not in labels
    assert len(result["edges"]) == 1
    assert result["edges"][0]["source"] == "node_2"
    assert result["edges"][0]["target"] == "node_3"
