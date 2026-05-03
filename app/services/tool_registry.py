from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


ToolCategory = Literal[
    "retrieval",
    "extraction",
    "graph_read",
    "graph_write",
    "memory",
    "intelligence",
    "participant_write",
    "composition",
    "persistence_write",
]

ToolMigrationStatus = Literal[
    "ready_readonly",
    "specialist_only",
    "guarded_write",
    "planned",
]


@dataclass(frozen=True)
class ToolContract:
    name: str
    category: ToolCategory
    current_implementation: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    side_effects: tuple[str, ...] = ()
    hard_guards: tuple[str, ...] = ()
    planner_callable: bool = False
    migration_status: ToolMigrationStatus = "planned"
    notes: str = ""

    @property
    def has_side_effects(self) -> bool:
        return bool(self.side_effects)


def _schema(
    *,
    required: tuple[str, ...] = (),
    properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "required": list(required),
        "properties": dict(properties or {}),
    }


def _tool(
    *,
    name: str,
    category: ToolCategory,
    current_implementation: str,
    description: str,
    input_schema: dict[str, Any],
    output_schema: dict[str, Any],
    side_effects: tuple[str, ...] = (),
    hard_guards: tuple[str, ...] = (),
    planner_callable: bool = False,
    migration_status: ToolMigrationStatus,
    notes: str = "",
) -> ToolContract:
    return ToolContract(
        name=name,
        category=category,
        current_implementation=current_implementation,
        description=description,
        input_schema=input_schema,
        output_schema=output_schema,
        side_effects=side_effects,
        hard_guards=hard_guards,
        planner_callable=planner_callable,
        migration_status=migration_status,
        notes=notes,
    )


AGENTIC_TOOL_REGISTRY: tuple[ToolContract, ...] = (
    _tool(
        name="exact_law_lookup",
        category="retrieval",
        current_implementation="app.agent.tools::search_law_articles",
        description="Read-only exact statute/article lookup via the existing exact-match fast path.",
        input_schema=_schema(
            required=("query",),
            properties={
                "query": {"type": "string"},
                "chat_history": {"type": "string"},
            },
        ),
        output_schema=_schema(
            properties={
                "documents": {"type": "array"},
                "exact_match": {"type": "boolean"},
            },
        ),
        hard_guards=("exact_statute_lookup_fast_path", "fabricated_citation_guard"),
        planner_callable=True,
        migration_status="ready_readonly",
        notes="Current implementation shares the underlying function with legal_rag_search.",
    ),
    _tool(
        name="legal_rag_search",
        category="retrieval",
        current_implementation="app.agent.tools::search_law_articles",
        description="Read-only legal retrieval for doctrine, elements, compensation rules, and general law grounding.",
        input_schema=_schema(
            required=("query",),
            properties={
                "query": {"type": "string"},
                "chat_history": {"type": "string"},
            },
        ),
        output_schema=_schema(properties={"documents": {"type": "array"}}),
        hard_guards=("fabricated_citation_guard",),
        planner_callable=True,
        migration_status="ready_readonly",
    ),
    _tool(
        name="case_search",
        category="retrieval",
        current_implementation="app.agent.tools::search_case_text",
        description="Read-only similar-case retrieval.",
        input_schema=_schema(
            required=("query",),
            properties={
                "query": {"type": "string"},
                "chat_history": {"type": "string"},
            },
        ),
        output_schema=_schema(properties={"documents": {"type": "array"}}),
        planner_callable=True,
        migration_status="ready_readonly",
    ),
    _tool(
        name="case_fact_extract",
        category="extraction",
        current_implementation="app.services.graph_extractor::extract_case_triples",
        description="Extract structured factual triples from free-form case text.",
        input_schema=_schema(
            required=("text",),
            properties={"text": {"type": "string"}},
        ),
        output_schema=_schema(properties={"triples": {"type": "array"}}),
        planner_callable=False,
        migration_status="specialist_only",
        notes="Pure extraction today, but still coupled to the graph-update specialist path.",
    ),
    _tool(
        name="case_summary_update",
        category="persistence_write",
        current_implementation="app.services.conversation_store::update_conversation_case_summary",
        description="Persist the normalized case summary back to the conversation record.",
        input_schema=_schema(
            required=("conversation_id", "case_summary"),
            properties={
                "conversation_id": {"type": "integer"},
                "case_summary": {"type": "string"},
            },
        ),
        output_schema=_schema(properties={"updated": {"type": "boolean"}}),
        side_effects=("db_write:conversation_case_summary",),
        hard_guards=(
            "owner_check",
            "temporary_blocks_persistence",
            "legal_qa_blocks_case_persistence",
        ),
        planner_callable=False,
        migration_status="guarded_write",
        notes="Write path remains owned by the current orchestrator, not by planner direct calls.",
    ),
    _tool(
        name="conversation_graph_extract",
        category="extraction",
        current_implementation="app.services.graph_extractor::extract_incremental_graph_actions",
        description="Extract incremental graph actions from factual user input.",
        input_schema=_schema(
            required=("user_input",),
            properties={"user_input": {"type": "string"}},
        ),
        output_schema=_schema(properties={"actions": {"type": "array"}}),
        planner_callable=False,
        migration_status="specialist_only",
    ),
    _tool(
        name="schedule_graph_update",
        category="graph_write",
        current_implementation="app.celery_app::update_graph_task.delay",
        description="Schedule asynchronous case-graph update work.",
        input_schema=_schema(
            required=("conversation_id", "user_id"),
            properties={
                "conversation_id": {"type": "integer"},
                "user_id": {"type": "integer"},
                "message_id": {"type": "integer"},
            },
        ),
        output_schema=_schema(
            properties={
                "scheduled": {"type": "boolean"},
                "task_id": {"type": "string"},
            },
        ),
        side_effects=("async_write:neo4j_case_graph",),
        hard_guards=(
            "owner_check",
            "temporary_blocks_persistence",
            "legal_qa_blocks_graph_update",
            "message_bound_graph_update_only",
        ),
        planner_callable=False,
        migration_status="guarded_write",
    ),
    _tool(
        name="current_conversation_graph_query",
        category="graph_read",
        current_implementation="app.services.graph_extractor::get_case_graph",
        description="Read-only structured query of the current conversation graph.",
        input_schema=_schema(
            required=("conversation_id",),
            properties={"conversation_id": {"type": "integer"}},
        ),
        output_schema=_schema(
            properties={
                "query": {"type": "string"},
                "nodes": {"type": "array"},
                "edges": {"type": "array"},
            },
        ),
        planner_callable=True,
        migration_status="ready_readonly",
        notes="Text rendering currently also exists via app.services.graph_extractor::format_graph_memory.",
    ),
    _tool(
        name="dossier_graph_context_query",
        category="graph_read",
        current_implementation="app.services.dossier_graph_tool::query_dossier_graph_context",
        description="Read-only dossier-scoped graph and participant context lookup.",
        input_schema=_schema(
            required=("conversation_id", "dossier_id", "query"),
            properties={
                "conversation_id": {"type": "integer"},
                "dossier_id": {"type": "integer"},
                "query": {"type": "string"},
                "focus_participant_id": {"type": "integer"},
                "limit": {"type": "integer"},
            },
        ),
        output_schema=_schema(
            properties={
                "participants": {"type": "array"},
                "participant_mentions": {"type": "array"},
                "matched_nodes": {"type": "array"},
                "matched_edges": {"type": "array"},
                "graph_summary": {"type": ["string", "null"]},
            },
        ),
        hard_guards=("owner_check", "dossier_scope_isolation"),
        planner_callable=True,
        migration_status="ready_readonly",
    ),
    _tool(
        name="conversation_entity_state_build",
        category="intelligence",
        current_implementation="app.services.conversation_entity_state::build_conversation_entity_state",
        description="Build resolved single-conversation entity state for dossier orchestration.",
        input_schema=_schema(
            required=("conversation_id",),
            properties={
                "conversation_id": {"type": "integer"},
                "message_text": {"type": "string"},
                "message_understanding": {"type": "object"},
                "raw_graph_payload": {"type": "object"},
            },
        ),
        output_schema=_schema(
            properties={
                "entities": {"type": "array"},
                "relations": {"type": "array"},
                "clarification_tasks": {"type": "array"},
            },
        ),
        hard_guards=(
            "temporary_blocks_persistence",
            "legal_qa_blocks_case_graph_pollution",
            "raw_graph_not_mutated_by_dossier_overlay",
        ),
        planner_callable=True,
        migration_status="ready_readonly",
    ),
    _tool(
        name="dossier_entity_state_build",
        category="intelligence",
        current_implementation="app.services.dossier_entity_state::build_dossier_entity_state",
        description="Build dossier canonical entity state from conversation_entity_state inputs.",
        input_schema=_schema(
            required=("dossier_id", "conversation_entity_states"),
            properties={
                "dossier_id": {"type": "integer"},
                "conversation_entity_states": {"type": "array"},
                "resolution_decisions": {"type": "array"},
            },
        ),
        output_schema=_schema(
            properties={
                "entities": {"type": "array"},
                "relations": {"type": "array"},
                "confirmation_tasks": {"type": "array"},
            },
        ),
        hard_guards=(
            "conversation_must_belong_to_dossier",
            "entity_merge_requires_user_confirmation",
            "person_to_company_never_identity_merge",
            "role_person_not_auto_identity_merge",
        ),
        planner_callable=True,
        migration_status="ready_readonly",
    ),
    _tool(
        name="display_graph_build",
        category="graph_read",
        current_implementation="app.services.dossier_display_graph::build_dossier_display_graph_from_payload",
        description="Build the read-only dossier display graph from raw graph payload plus participant overlays.",
        input_schema=_schema(
            required=("payload",),
            properties={"payload": {"type": "object"}},
        ),
        output_schema=_schema(
            properties={
                "nodes": {"type": "array"},
                "edges": {"type": "array"},
                "stats": {"type": "object"},
            },
        ),
        hard_guards=("raw_graph_not_mutated_by_overlay",),
        planner_callable=True,
        migration_status="ready_readonly",
    ),
    _tool(
        name="current_conversation_messages_query",
        category="memory",
        current_implementation="app.services.conversation_store::list_recent_conversation_messages",
        description="Read-only recent current-conversation message lookup used by subject reference and clarification flows.",
        input_schema=_schema(
            required=("conversation_id",),
            properties={
                "conversation_id": {"type": "integer"},
                "limit": {"type": "integer"},
            },
        ),
        output_schema=_schema(
            properties={
                "recent_messages": {"type": "array"},
            },
        ),
        planner_callable=True,
        migration_status="ready_readonly",
        notes="Executor reads the normalized recent messages already present in request context.",
    ),
    _tool(
        name="dossier_intelligence_build",
        category="intelligence",
        current_implementation="app.services.dossier_intelligence::build_dossier_intelligence",
        description="Assemble dossier summary, display notes, and confirmation-task intelligence.",
        input_schema=_schema(
            required=("dossier_id",),
            properties={
                "dossier_id": {"type": "integer"},
                "conversation_id": {"type": "integer"},
                "include_display_graph": {"type": "boolean"},
                "use_llm": {"type": "boolean"},
                "provider_hint": {"type": "string"},
            },
        ),
        output_schema=_schema(
            properties={
                "summary": {"type": "object"},
                "display_graph_notes": {"type": "array"},
                "confirmation_tasks": {"type": "array"},
                "trace": {"type": "array"},
            },
        ),
        hard_guards=("owner_check", "raw_graph_not_mutated_by_overlay"),
        planner_callable=False,
        migration_status="specialist_only",
        notes="Read-only today, but the output contract is still stabilizing across H7.",
    ),
    _tool(
        name="subject_reference_resolve",
        category="memory",
        current_implementation="app.services.memory_resolver::build_subject_reference_response_text",
        description="Resolve subject references across current-conversation and dossier memory scopes.",
        input_schema=_schema(
            required=("question", "message_understanding", "current_conversation_id"),
            properties={
                "question": {"type": "string"},
                "message_understanding": {"type": "object"},
                "request_context": {"type": "object"},
                "current_conversation_graph": {"type": "object"},
                "dossier_context": {"type": "object"},
                "dossier_graph_context": {"type": "object"},
                "pending_clarification": {"type": "object"},
                "recent_messages": {"type": "array"},
                "current_conversation_id": {"type": "integer"},
            },
        ),
        output_schema=_schema(
            properties={
                "response_text": {"type": "string"},
                "confidence_score": {"type": "number"},
                "memory_resolution": {"type": "object"},
            },
        ),
        planner_callable=True,
        migration_status="ready_readonly",
    ),
    _tool(
        name="participant_resolution_apply",
        category="participant_write",
        current_implementation="app.services.conversation_store::resolve_dossier_participant_mentions",
        description="Persist participant mention mappings into dossier storage.",
        input_schema=_schema(
            required=("dossier_id", "participant_id", "mentions"),
            properties={
                "dossier_id": {"type": "integer"},
                "participant_id": {"type": "integer"},
                "mentions": {"type": "array"},
            },
        ),
        output_schema=_schema(properties={"mentions": {"type": "array"}}),
        side_effects=("db_write:dossier_participant_mentions",),
        hard_guards=(
            "owner_check",
            "archived_dossier_write_block",
            "explicit_user_confirmation_required",
        ),
        planner_callable=False,
        migration_status="guarded_write",
    ),
    _tool(
        name="party_naming_confirm",
        category="participant_write",
        current_implementation="app.api.dossiers::confirm_dossier_party_naming",
        description="Apply an explicit user-confirmed party-naming decision to dossier participants.",
        input_schema=_schema(
            required=("dossier_id", "display_label", "source_task"),
            properties={
                "dossier_id": {"type": "integer"},
                "display_label": {"type": "string"},
                "source_task": {"type": "object"},
            },
        ),
        output_schema=_schema(
            properties={
                "participant": {"type": "object"},
                "mentions": {"type": "array"},
            },
        ),
        side_effects=(
            "db_write:dossier_participant",
            "db_write:dossier_participant_mentions",
        ),
        hard_guards=(
            "owner_check",
            "archived_dossier_write_block",
            "explicit_user_confirmation_required",
            "self_anchor_only_party_naming",
        ),
        planner_callable=False,
        migration_status="planned",
        notes="Kept planned because current party-naming still spans intelligence task generation and API confirmation.",
    ),
    _tool(
        name="out_of_scope_answer_compose",
        category="composition",
        current_implementation="app.agent.nodes._llm::OUT_OF_SCOPE_RESPONSE",
        description="Compose the fixed out-of-scope response without running retrieval.",
        input_schema=_schema(
            required=("question",),
            properties={"question": {"type": "string"}},
        ),
        output_schema=_schema(
            properties={
                "response": {"type": "string"},
                "sources": {"type": "array"},
                "route_intent": {"type": "string"},
            },
        ),
        hard_guards=("foreign_law_blocks_domestic_rag",),
        planner_callable=True,
        migration_status="ready_readonly",
    ),
    _tool(
        name="legal_answer_compose",
        category="composition",
        current_implementation="app.services.legal_agent::legal_agent.ask",
        description="Compose the final legal answer through the existing legacy legal graph.",
        input_schema=_schema(
            required=("user_id", "session_id", "question"),
            properties={
                "user_id": {"type": "integer"},
                "session_id": {"type": "string"},
                "question": {"type": "string"},
                "chat_history": {"type": "string"},
                "case_summary": {"type": "string"},
                "graph_memory": {"type": "string"},
                "conversation_id": {"type": "integer"},
                "dossier_context": {"type": "object"},
                "dossier_graph_context": {"type": "object"},
            },
        ),
        output_schema=_schema(
            properties={
                "response": {"type": "string"},
                "sources": {"type": "array"},
                "route_intent": {"type": "string"},
                "case_summary": {"type": "string"},
            },
        ),
        planner_callable=True,
        migration_status="specialist_only",
        notes="Still owns its own internal LangGraph and router rules.",
    ),
    _tool(
        name="memory_answer_compose",
        category="composition",
        current_implementation="app.services.memory_resolver::answer_subject_reference_message_async",
        description="Compose the final user-facing answer for subject-reference and clarification flows.",
        input_schema=_schema(
            required=("question", "message_understanding", "current_conversation_id"),
            properties={
                "question": {"type": "string"},
                "message_understanding": {"type": "object"},
                "request_context": {"type": "object"},
                "current_conversation_graph": {"type": "object"},
                "dossier_context": {"type": "object"},
                "dossier_graph_context": {"type": "object"},
                "pending_clarification": {"type": "object"},
                "case_summary": {"type": "string"},
                "recent_messages": {"type": "array"},
                "current_conversation_id": {"type": "integer"},
            },
        ),
        output_schema=_schema(
            properties={
                "response": {"type": "string"},
                "confidence_score": {"type": "number"},
                "resolution_trace": {"type": "object"},
            },
        ),
        planner_callable=True,
        migration_status="specialist_only",
        notes="Planner and executor both treat this as a specialist composition tool in H8-2b.",
    ),
    _tool(
        name="acknowledgement_compose",
        category="composition",
        current_implementation="app.services.case_update_acknowledgement::build_case_update_acknowledgement",
        description="Compose the lightweight acknowledgement for fact-update and confirmation flows.",
        input_schema=_schema(
            required=("plan", "intent", "user_message_text", "conversation_id", "route_intent"),
            properties={
                "plan": {"type": "object"},
                "intent": {"type": "string"},
                "user_message_text": {"type": "string"},
                "persisted_fact_text": {"type": ["string", "null"]},
                "graph_update_scheduled": {"type": "boolean"},
                "graph_update_actions": {"type": "array"},
                "dossier_id": {"type": ["integer", "null"]},
                "conversation_id": {"type": "integer"},
                "persistence_consent": {"type": ["string", "null"]},
                "route_intent": {"type": "string"},
            },
        ),
        output_schema=_schema(
            properties={
                "text": {"type": "string"},
                "trace": {"type": "object"},
                "fallback_used": {"type": "boolean"},
            },
        ),
        planner_callable=True,
        migration_status="specialist_only",
    ),
)


TOOL_CONTRACTS_BY_NAME: dict[str, ToolContract] = {
    contract.name: contract for contract in AGENTIC_TOOL_REGISTRY
}


def list_tool_contracts() -> tuple[ToolContract, ...]:
    return AGENTIC_TOOL_REGISTRY


def get_tool_contract(name: str) -> ToolContract | None:
    return TOOL_CONTRACTS_BY_NAME.get(str(name or "").strip())


def list_planner_callable_tools() -> tuple[ToolContract, ...]:
    return tuple(
        contract
        for contract in AGENTIC_TOOL_REGISTRY
        if contract.planner_callable
    )


def validate_tool_registry() -> None:
    seen_names: set[str] = set()
    for contract in AGENTIC_TOOL_REGISTRY:
        if not contract.name or contract.name in seen_names:
            raise ValueError(f"Duplicate or empty tool name: {contract.name!r}")
        seen_names.add(contract.name)

        if contract.input_schema.get("type") != "object":
            raise ValueError(f"Tool {contract.name} must declare an object input schema")
        if contract.output_schema.get("type") != "object":
            raise ValueError(f"Tool {contract.name} must declare an object output schema")
        if not isinstance(contract.input_schema.get("properties"), dict):
            raise ValueError(f"Tool {contract.name} input schema missing properties")
        if not isinstance(contract.output_schema.get("properties"), dict):
            raise ValueError(f"Tool {contract.name} output schema missing properties")
        if contract.has_side_effects and not contract.hard_guards:
            raise ValueError(f"Write-capable tool {contract.name} must declare hard guards")
        if contract.planner_callable and contract.has_side_effects:
            raise ValueError(
                f"Planner-callable tool {contract.name} cannot expose unprepared write side effects"
            )


validate_tool_registry()


__all__ = [
    "AGENTIC_TOOL_REGISTRY",
    "TOOL_CONTRACTS_BY_NAME",
    "ToolContract",
    "ToolCategory",
    "ToolMigrationStatus",
    "get_tool_contract",
    "list_planner_callable_tools",
    "list_tool_contracts",
    "validate_tool_registry",
]
