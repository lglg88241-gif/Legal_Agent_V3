from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


DossierToolCategory = Literal["read", "intelligence", "write"]


@dataclass(frozen=True)
class DossierToolContract:
    name: str
    category: DossierToolCategory
    current_implementation: str
    description: str
    side_effects: tuple[str, ...] = ()
    hard_guards: tuple[str, ...] = ()
    planner_callable: bool = False


DOSSIER_TOOL_REGISTRY: tuple[DossierToolContract, ...] = (
    DossierToolContract(
        name="dossier_bound_conversations_list",
        category="read",
        current_implementation="app.services.conversation_store::list_dossier_conversations",
        description="Read conversations bound to a dossier.",
        hard_guards=("no_cross_user_dossier_write",),
        planner_callable=True,
    ),
    DossierToolContract(
        name="conversation_entity_state_build",
        category="intelligence",
        current_implementation="app.services.conversation_entity_state::build_conversation_entity_state",
        description="Build resolved single-conversation entity state from existing conversation agent outputs.",
        hard_guards=("temporary_conversation_blocks_dossier_write", "raw_graph_not_mutated_by_dossier_overlay"),
        planner_callable=True,
    ),
    DossierToolContract(
        name="dossier_entity_state_build",
        category="intelligence",
        current_implementation="app.services.dossier_entity_state::build_dossier_entity_state",
        description="Build dossier canonical entity state from conversation_entity_state inputs.",
        hard_guards=(
            "conversation_must_belong_to_dossier",
            "person_to_company_never_identity_merge",
            "role_person_not_auto_identity_merge",
        ),
        planner_callable=True,
    ),
    DossierToolContract(
        name="dossier_coordination_graph_build",
        category="read",
        current_implementation="app.services.dossier_entity_state::coordination_graph_from_dossier_entity_state",
        description="Render coordination graph from canonical dossier entity state.",
        hard_guards=("raw_graph_not_mutated_by_dossier_overlay",),
        planner_callable=True,
    ),
    DossierToolContract(
        name="dossier_confirmation_decision_save",
        category="write",
        current_implementation="app.services.conversation_store::create_dossier_entity_resolution_decision",
        description="Persist a scoped dossier entity confirmation decision.",
        side_effects=("db_write:dossier_entity_resolution_decision",),
        hard_guards=(
            "no_cross_user_dossier_write",
            "no_cross_dossier_entity_merge",
            "task_scope_must_match_dossier",
            "task_scope_must_match_conversation",
        ),
    ),
)


DOSSIER_TOOL_CONTRACTS_BY_NAME = {contract.name: contract for contract in DOSSIER_TOOL_REGISTRY}


def get_dossier_tool_contract(name: str) -> DossierToolContract | None:
    return DOSSIER_TOOL_CONTRACTS_BY_NAME.get(str(name or "").strip())


def list_dossier_tool_contracts() -> tuple[DossierToolContract, ...]:
    return DOSSIER_TOOL_REGISTRY


__all__ = [
    "DossierToolContract",
    "get_dossier_tool_contract",
    "list_dossier_tool_contracts",
]
