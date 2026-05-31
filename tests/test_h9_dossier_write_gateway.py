from __future__ import annotations

from app.services.dossier_hard_guards import identity_merge_blockers, validate_task_scope
from app.services.dossier_write_gateway import build_scoped_confirmation_decision


def _task() -> dict:
    return {
        "id": "task-1",
        "task_key": "task-1",
        "dossier_id": 51,
        "source_conversation_ids": [805, 806],
        "source_entity": {
            "id": "entity-a",
            "label": "张三",
            "entity_type": "person",
        },
        "target_entity": {
            "id": "entity-b",
            "label": "张三",
            "entity_type": "person",
        },
    }


def test_current_conversation_cannot_consume_other_conversation_task() -> None:
    result = validate_task_scope(_task(), dossier_id=51, conversation_id=807)

    assert result["allowed"] is False
    assert result["blocked_by"] == ["task_scope_must_match_conversation"]


def test_confirm_same_entity_requires_scoped_cross_conversation_task() -> None:
    result = build_scoped_confirmation_decision(
        task=_task(),
        action="confirm",
        dossier_id=51,
        conversation_id=805,
    )

    assert result["allowed"] is True
    assert result["decision"]["decision_type"] == "same"
    assert result["decision"]["subject"] == "cross_conversation_identity"
    assert result["decision"]["task_key"] == "task-1"


def test_person_to_company_merge_is_blocked() -> None:
    blockers = identity_merge_blockers(
        {"entity_type": "person", "role": "manager_or_hr"},
        {"entity_type": "organization", "role": "employer"},
        requires_user_confirmation=True,
        user_confirmed=True,
        source_dossier_id=51,
        target_dossier_id=51,
    )

    assert "person_to_company_never_identity_merge" in blockers


def test_scoped_confirmation_guard_uses_role_and_type_fields() -> None:
    task = {
        "id": "task-role-company",
        "task_key": "task-role-company",
        "dossier_id": 51,
        "source_conversation_ids": [805, 806],
        "source_entity": {
            "id": "entity-manager",
            "label": "王经理",
            "entity_type": "person",
            "role": "manager_or_hr",
            "aliases": ["经理"],
            "source_entity_ids": ["conversation:805:entity:manager"],
            "dossier_id": 51,
        },
        "target_entity": {
            "id": "entity-company",
            "label": "B公司",
            "entity_type": "organization",
            "role": "employer",
            "aliases": [],
            "source_entity_ids": ["conversation:806:entity:company"],
            "dossier_id": 51,
        },
    }

    result = build_scoped_confirmation_decision(
        task=task,
        action="confirm",
        dossier_id=51,
        conversation_id=805,
    )

    assert result["allowed"] is False
    assert "person_to_company_never_identity_merge" in result["blocked_by"]
    assert "role_person_not_auto_identity_merge" in result["blocked_by"]


def test_wrong_dossier_scope_is_blocked() -> None:
    result = build_scoped_confirmation_decision(
        task=_task(),
        action="confirm",
        dossier_id=52,
        conversation_id=805,
    )

    assert result["allowed"] is False
    assert "task_scope_must_match_dossier" in result["blocked_by"]
