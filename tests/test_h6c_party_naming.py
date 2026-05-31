from app.services.conversation_entity_state import build_conversation_entity_state
from app.services.dossier_intelligence import _build_party_naming_task


def test_party_naming_task_only_targets_self_anchor_labels() -> None:
    raw_graph_payload = {
        "participant_aliases": {},
        "confirmed_participants": [],
        "graph": {
            "nodes": [
                {"id": "self-1", "label": "我", "type": "person", "conversation_id": 101},
                {"id": "company-1", "label": "B公司", "type": "organization", "conversation_id": 101},
                {"id": "manager-1", "label": "王经理", "type": "person", "conversation_id": 101},
            ]
        },
    }

    task = _build_party_naming_task(raw_graph_payload, {"nodes": []})

    assert task is not None
    assert task["type"] == "party_naming"
    assert task["task_type"] == "party_naming"
    assert task["status"] == "pending"
    assert task["subject"] == "self_anchor"
    assert task["current_label"] == "我"
    assert task["raw_labels"] == ["我"]
    assert task["source_conversation_ids"] == [101]
    assert task["evidence"]["raw_labels"] == ["我"]


def test_party_naming_accepts_explicit_current_party_aliases() -> None:
    for label in ["劳动者", "申请人", "我们"]:
        raw_graph_payload = {
            "participant_aliases": {},
            "confirmed_participants": [],
            "graph": {
                "nodes": [
                    {"id": f"self-{label}", "label": label, "type": "person", "conversation_id": 102},
                ]
            },
        }

        task = _build_party_naming_task(raw_graph_payload, {"nodes": []})

        assert task is not None
        assert task["type"] == "party_naming"
        assert task["task_type"] == "party_naming"
        assert task["status"] == "pending"
        assert task["current_label"] == label


def test_party_naming_suppressed_when_current_party_already_resolved() -> None:
    raw_graph_payload = {
        "participant_aliases": {},
        "confirmed_participants": [],
        "graph": {
            "nodes": [
                {
                    "id": "person-808",
                    "label": "张三",
                    "type": "person",
                    "conversation_id": 808,
                    "properties": {"role": "current_party"},
                },
                {"id": "worker-808", "label": "劳动者", "type": "person", "conversation_id": 808},
                {"id": "company-808", "label": "B公司", "type": "organization", "conversation_id": 808},
            ]
        },
    }
    conversation_entity_state = build_conversation_entity_state(
        message_text="我叫张三，是这起劳动争议里的劳动者。我和B公司签了劳动合同，但B公司一直没有给我缴纳社保。",
        raw_graph_payload=raw_graph_payload,
        conversation_id=808,
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )

    task = _build_party_naming_task(
        raw_graph_payload,
        {"nodes": []},
        conversation_id=808,
        conversation_entity_state=conversation_entity_state,
    )

    assert task is None
