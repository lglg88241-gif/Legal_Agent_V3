from __future__ import annotations

from app.services.conversation_entity_state import build_conversation_entity_state
from app.services.dossier_agent_context import DossierEntityRequestContext
from app.services.dossier_entity_state import (
    build_dossier_entity_state,
    coordination_graph_from_dossier_entity_state,
)
from app.services.dossier_tool_registry import get_dossier_tool_contract


def test_dossier_entity_request_context_keeps_ui_event_separate_from_conversation_state() -> None:
    conversation_state = build_conversation_entity_state(
        message_text="我叫张三，是这起劳动争议里的劳动者。",
        conversation_id=805,
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )
    context = DossierEntityRequestContext(
        user_id=7,
        dossier_id=51,
        conversation_id=805,
        event_type="open_dossier_drawer",
        conversation_entity_states=[conversation_state],
    )

    payload = context.as_dict()
    assert payload["event_type"] == "open_dossier_drawer"
    assert payload["conversation_entity_states"][0]["entities"][0]["canonical_label"] == "张三"
    assert "party_naming" not in payload["payload"]


def test_dossier_coordination_graph_uses_dossier_entity_state_contract() -> None:
    state_805 = build_conversation_entity_state(
        message_text="我叫张三，是这起劳动争议里的劳动者。",
        conversation_id=805,
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )
    dossier_state = build_dossier_entity_state(
        conversation_entity_states=[state_805],
        dossier_id=51,
        user_id=7,
        resolution_decisions=[],
    )
    graph = coordination_graph_from_dossier_entity_state(dossier_state)

    assert graph["trace"]["mode"] == "agentic_dossier_entity_state"
    assert graph["stats"]["canonical_entities"] == 1
    assert graph["entities"][0]["label"] == "张三"
    assert graph["tasks"] == []


def test_dossier_tool_registry_contains_h9_entity_line_tools() -> None:
    assert get_dossier_tool_contract("conversation_entity_state_build") is not None
    assert get_dossier_tool_contract("dossier_entity_state_build") is not None
    assert get_dossier_tool_contract("dossier_coordination_graph_build") is not None
