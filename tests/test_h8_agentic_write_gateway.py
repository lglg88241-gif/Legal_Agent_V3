from __future__ import annotations

import asyncio
from typing import Any

from app.services.agentic_write_gateway import execute_agentic_writes


FACT_MESSAGE = "B公司的人事通知我去签确认书，而且人事王经理口头通知我不用来了。"


def test_execute_agentic_writes_does_not_execute_blocked_requests() -> None:
    calls = {"persist": 0, "graph": 0}

    async def fake_persist(**kwargs: Any) -> dict[str, int]:
        calls["persist"] += 1
        return {"user_message_id": 101, "assistant_message_id": 102}

    class FakeGraphTask:
        @staticmethod
        def delay(conversation_id: int, user_id: int, message_id: int) -> object:
            calls["graph"] += 1
            return type("Result", (), {"id": f"graph-task-{conversation_id}-{message_id}"})()

    writes_requested = [
        {
            "name": "structured_case_persistence",
            "tool_name": "case_summary_update",
            "requested": True,
            "input_text": FACT_MESSAGE,
            "allowed": False,
            "blocked_by": ["temporary_blocks_structured_persistence"],
        },
        {
            "name": "graph_update_schedule",
            "tool_name": "schedule_graph_update",
            "requested": True,
            "input_text": FACT_MESSAGE,
            "allowed": False,
            "blocked_by": ["temporary_blocks_graph_update"],
        },
    ]

    result = asyncio.run(
        execute_agentic_writes(
            writes_requested=writes_requested,
            writes_allowed=[],
            writes_blocked=writes_requested,
            context={
                "user_id": 1,
                "conversation_id": 9,
                "user_content": FACT_MESSAGE,
                "assistant_content": "已记录。",
                "assistant_metadata": {},
                "user_metadata": {},
                "updated_case_summary": FACT_MESSAGE,
                "structured_persistence_handler": fake_persist,
                "graph_update_scheduler": FakeGraphTask(),
            },
        )
    )

    assert calls == {"persist": 0, "graph": 0}
    assert result["applied_writes"] == []
    assert [item["status"] for item in result["write_results"]] == ["blocked", "blocked"]
    assert [item["write_type"] for item in result["write_results"]] == [
        "structured_case_persistence",
        "graph_update_schedule",
    ]
    assert all(item["error"] is None for item in result["write_results"])


def test_execute_agentic_writes_applies_tracked_fact_update_and_schedules_graph() -> None:
    persist_calls: list[dict[str, Any]] = []
    graph_calls: list[tuple[int, int, int]] = []

    async def fake_persist(**kwargs: Any) -> dict[str, int]:
        persist_calls.append(dict(kwargs))
        return {"user_message_id": 101, "assistant_message_id": 102}

    class FakeGraphTask:
        @staticmethod
        def delay(conversation_id: int, user_id: int, message_id: int) -> object:
            graph_calls.append((conversation_id, user_id, message_id))
            return type("Result", (), {"id": f"graph-task-{conversation_id}-{message_id}"})()

    writes_requested = [
        {
            "name": "structured_case_persistence",
            "tool_name": "case_summary_update",
            "requested": True,
            "input_text": FACT_MESSAGE,
            "reason": "persist fact update",
            "allowed": True,
            "blocked_by": [],
        },
        {
            "name": "graph_update_schedule",
            "tool_name": "schedule_graph_update",
            "requested": True,
            "input_text": FACT_MESSAGE,
            "reason": "schedule graph update",
            "allowed": True,
            "blocked_by": [],
        },
    ]

    result = asyncio.run(
        execute_agentic_writes(
            writes_requested=writes_requested,
            writes_allowed=writes_requested,
            writes_blocked=[],
            context={
                "user_id": 1,
                "conversation_id": 9,
                "user_content": FACT_MESSAGE,
                "assistant_content": "已记录这条事实。",
                "assistant_metadata": {"route_intent": "CASE_UPDATE"},
                "user_metadata": {
                    "graph_update_input_text": FACT_MESSAGE,
                    "graph_update_source": "current_message",
                },
                "updated_case_summary": FACT_MESSAGE,
                "case_state_dict": {"facts": [FACT_MESSAGE]},
                "case_type": "labor_dispute",
                "graph_update_input_text": FACT_MESSAGE,
                "graph_update_source": "current_message",
                "structured_persistence_handler": fake_persist,
                "graph_update_scheduler": FakeGraphTask(),
            },
        )
    )

    assert len(persist_calls) == 1
    assert persist_calls[0]["case_summary"] == FACT_MESSAGE
    assert graph_calls == [(9, 1, 101)]

    assert result["persist_result"] == {"user_message_id": 101, "assistant_message_id": 102}
    assert [item["name"] for item in result["applied_writes"]] == [
        "structured_case_persistence",
        "graph_update_schedule",
    ]

    persist_result, graph_result = result["write_results"]
    for item in (persist_result, graph_result):
        assert {"write_type", "status", "input_summary", "output_summary", "error"} <= set(item)

    assert persist_result["status"] == "success"
    assert persist_result["error"] is None
    assert "王经理" in (persist_result["input_summary"] or "")

    assert graph_result["status"] == "success"
    assert graph_result["error"] is None
    assert graph_result["details"]["task_id"] == "graph-task-9-101"
    assert graph_result["details"]["graph_update_source"] == "current_message"
