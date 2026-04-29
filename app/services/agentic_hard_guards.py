from __future__ import annotations

from typing import Any


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _normalize_key(value: Any) -> str:
    return _normalize_text(value).lower()


def evaluate_agentic_hard_guards(
    *,
    agentic_plan: dict[str, Any] | None,
    persistence_consent: str | None,
    conversation_mode: str | None,
    missing_previous_fact: bool = False,
) -> dict[str, Any]:
    plan = agentic_plan if isinstance(agentic_plan, dict) else {}
    requested_writes = plan.get("writes_requested") if isinstance(plan.get("writes_requested"), list) else []
    normalized_persistence = _normalize_key(persistence_consent)
    normalized_mode = _normalize_text(conversation_mode).upper()

    temporary_blocks_persistence = normalized_persistence == "temporary"
    legal_qa_blocks_graph = normalized_mode == "LEGAL_QA"

    guards_applied = [
        {"name": "temporary_blocks_persistence", "triggered": temporary_blocks_persistence},
        {"name": "legal_qa_blocks_graph", "triggered": legal_qa_blocks_graph},
        {"name": "missing_previous_fact", "triggered": bool(missing_previous_fact)},
    ]

    evaluated_writes: list[dict[str, Any]] = []
    for item in requested_writes:
        if not isinstance(item, dict):
            continue
        write_name = _normalize_text(item.get("name")) or "unknown_write"
        blocked_by: list[str] = []
        if write_name == "structured_case_persistence":
            if temporary_blocks_persistence:
                blocked_by.append("temporary_blocks_persistence")
            if legal_qa_blocks_graph:
                blocked_by.append("legal_qa_blocks_graph")
            if missing_previous_fact:
                blocked_by.append("missing_previous_fact")
        if write_name == "graph_update_schedule":
            if temporary_blocks_persistence:
                blocked_by.append("temporary_blocks_persistence")
            if legal_qa_blocks_graph:
                blocked_by.append("legal_qa_blocks_graph")
            if missing_previous_fact:
                blocked_by.append("missing_previous_fact")
        evaluated_writes.append(
            {
                **item,
                "allowed": len(blocked_by) == 0,
                "blocked_by": blocked_by,
            }
        )

    return {
        "guards_applied": guards_applied,
        "writes_requested": evaluated_writes,
    }
