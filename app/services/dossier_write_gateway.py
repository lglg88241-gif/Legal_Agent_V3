from __future__ import annotations

from typing import Any

from app.services.dossier_hard_guards import (
    identity_merge_blockers,
    validate_task_scope,
)


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def build_scoped_confirmation_decision(
    *,
    task: dict[str, Any],
    action: str,
    dossier_id: int,
    conversation_id: int | None = None,
) -> dict[str, Any]:
    normalized_action = _normalize_text(action).lower()
    if normalized_action not in {"confirm", "reject", "defer"}:
        return {
            "allowed": False,
            "blocked_by": ["unsupported_confirmation_action"],
            "decision": None,
        }

    scope_result = validate_task_scope(
        task,
        dossier_id=dossier_id,
        conversation_id=conversation_id,
    )
    blockers = list(scope_result.get("blocked_by") or [])

    source_entity = task.get("source_entity") if isinstance(task.get("source_entity"), dict) else {}
    target_entity = task.get("target_entity") if isinstance(task.get("target_entity"), dict) else {}
    if normalized_action == "confirm":
        blockers.extend(
            identity_merge_blockers(
                source_entity,
                target_entity,
                requires_user_confirmation=True,
                user_confirmed=True,
                source_dossier_id=source_entity.get("dossier_id") or dossier_id,
                target_dossier_id=target_entity.get("dossier_id") or dossier_id,
            )
        )

    if blockers:
        return {
            "allowed": False,
            "blocked_by": list(dict.fromkeys(blockers)),
            "decision": None,
        }

    decision_type = "same" if normalized_action == "confirm" else "not_same"
    if normalized_action == "defer":
        decision_type = "skip"

    return {
        "allowed": True,
        "blocked_by": [],
        "decision": {
            "decision_type": decision_type,
            "subject": "cross_conversation_identity",
            "task_key": _normalize_text(task.get("task_key") or task.get("id")),
            "dossier_id": dossier_id,
            "source_entity_id": _normalize_text(source_entity.get("id")),
            "target_entity_id": _normalize_text(target_entity.get("id")),
            "source_entity_ids": source_entity.get("source_entity_ids") or [],
            "target_entity_ids": target_entity.get("source_entity_ids") or [],
            "source_conversation_ids": task.get("source_conversation_ids") or [],
        },
    }


__all__ = ["build_scoped_confirmation_decision"]
