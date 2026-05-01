from __future__ import annotations

from typing import Any


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _normalize_key(value: Any) -> str:
    return _normalize_text(value).lower()


def _positive_int(value: Any) -> int:
    try:
        normalized = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return normalized if normalized > 0 else 0


def _entity_type(entity: dict[str, Any] | None) -> str:
    if not isinstance(entity, dict):
        return "unknown"
    return _normalize_key(entity.get("entity_type")) or "unknown"


def _entity_role(entity: dict[str, Any] | None) -> str:
    if not isinstance(entity, dict):
        return ""
    return _normalize_key(entity.get("role"))


def _source_conversation_ids(entity: dict[str, Any] | None) -> set[int]:
    if not isinstance(entity, dict):
        return set()
    values = entity.get("source_conversation_ids")
    if not isinstance(values, list):
        values = [entity.get("source_conversation_id")]
    return {_positive_int(value) for value in values if _positive_int(value) > 0}


def identity_merge_blockers(
    source_entity: dict[str, Any] | None,
    target_entity: dict[str, Any] | None,
    *,
    requires_user_confirmation: bool = True,
    user_confirmed: bool = False,
    source_dossier_id: int | None = None,
    target_dossier_id: int | None = None,
) -> list[str]:
    blockers: list[str] = []
    source_type = _entity_type(source_entity)
    target_type = _entity_type(target_entity)
    source_role = _entity_role(source_entity)
    target_role = _entity_role(target_entity)

    if source_dossier_id is not None and target_dossier_id is not None:
        if _positive_int(source_dossier_id) != _positive_int(target_dossier_id):
            blockers.append("no_cross_dossier_entity_merge")

    if requires_user_confirmation and not user_confirmed:
        blockers.append("entity_merge_requires_user_confirmation")

    if {source_type, target_type} == {"person", "organization"}:
        blockers.append("person_to_company_never_identity_merge")

    if {source_type, target_type} == {"role", "organization"}:
        blockers.append("role_person_not_auto_identity_merge")

    role_like = {"manager_or_hr", "representative", "hr", "role", "manager"}
    if (source_type in {"person", "role"} and source_role in role_like) or (
        target_type in {"person", "role"} and target_role in role_like
    ):
        if source_type != target_type or source_role != target_role:
            blockers.append("role_person_not_auto_identity_merge")

    return list(dict.fromkeys(blockers))


def can_create_identity_candidate(
    source_entity: dict[str, Any],
    target_entity: dict[str, Any],
) -> bool:
    return not identity_merge_blockers(
        source_entity,
        target_entity,
        requires_user_confirmation=False,
        user_confirmed=False,
    )


def validate_task_scope(
    task: dict[str, Any],
    *,
    dossier_id: int | None = None,
    conversation_id: int | None = None,
) -> dict[str, Any]:
    blockers: list[str] = []
    task_dossier_id = _positive_int(task.get("dossier_id"))
    expected_dossier_id = _positive_int(dossier_id)
    if expected_dossier_id > 0 and task_dossier_id > 0 and task_dossier_id != expected_dossier_id:
        blockers.append("task_scope_must_match_dossier")

    expected_conversation_id = _positive_int(conversation_id)
    if expected_conversation_id > 0:
        source_ids = {
            _positive_int(value)
            for value in task.get("source_conversation_ids", [])
            if _positive_int(value) > 0
        }
        if source_ids and expected_conversation_id not in source_ids:
            blockers.append("task_scope_must_match_conversation")

    return {
        "allowed": not blockers,
        "blocked_by": blockers,
    }


def conversation_belongs_to_dossier(
    *,
    conversation_id: int | None,
    dossier_conversation_ids: list[int] | None,
) -> dict[str, Any]:
    normalized_conversation_id = _positive_int(conversation_id)
    allowed_ids = {_positive_int(value) for value in dossier_conversation_ids or []}
    allowed_ids.discard(0)
    allowed = normalized_conversation_id > 0 and normalized_conversation_id in allowed_ids
    return {
        "allowed": allowed,
        "blocked_by": [] if allowed else ["conversation_must_belong_to_dossier"],
    }


__all__ = [
    "can_create_identity_candidate",
    "conversation_belongs_to_dossier",
    "identity_merge_blockers",
    "validate_task_scope",
]
