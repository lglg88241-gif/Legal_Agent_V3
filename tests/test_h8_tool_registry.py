from __future__ import annotations

from app.services.tool_registry import (
    AGENTIC_TOOL_REGISTRY,
    get_tool_contract,
    list_planner_callable_tools,
    validate_tool_registry,
)


def test_tool_registry_names_are_unique_and_non_empty() -> None:
    names = [contract.name for contract in AGENTIC_TOOL_REGISTRY]
    assert names
    assert all(names)
    assert len(names) == len(set(names))


def test_each_tool_contract_exposes_basic_schema() -> None:
    for contract in AGENTIC_TOOL_REGISTRY:
        assert contract.category
        assert contract.current_implementation
        assert contract.description
        assert contract.migration_status

        assert contract.input_schema["type"] == "object"
        assert isinstance(contract.input_schema["properties"], dict)
        assert contract.output_schema["type"] == "object"
        assert isinstance(contract.output_schema["properties"], dict)


def test_write_tools_declare_hard_guards_and_are_not_planner_callable() -> None:
    write_tools = [contract for contract in AGENTIC_TOOL_REGISTRY if contract.has_side_effects]
    assert write_tools

    for contract in write_tools:
        assert contract.hard_guards
        assert contract.planner_callable is False


def test_query_tools_are_planner_callable() -> None:
    planner_callable = {contract.name for contract in list_planner_callable_tools()}
    assert {
        "exact_law_lookup",
        "legal_rag_search",
        "case_search",
        "current_conversation_graph_query",
        "current_conversation_messages_query",
        "dossier_graph_context_query",
        "display_graph_build",
        "subject_reference_resolve",
        "memory_answer_compose",
        "legal_answer_compose",
        "acknowledgement_compose",
    }.issubset(planner_callable)


def test_party_naming_tool_is_planned_not_directly_callable() -> None:
    contract = get_tool_contract("party_naming_confirm")
    assert contract is not None
    assert contract.migration_status == "planned"
    assert contract.planner_callable is False


def test_current_conversation_messages_query_is_executor_ready_readonly() -> None:
    contract = get_tool_contract("current_conversation_messages_query")
    assert contract is not None
    assert contract.migration_status == "ready_readonly"
    assert contract.planner_callable is True


def test_registry_validation_passes() -> None:
    validate_tool_registry()
