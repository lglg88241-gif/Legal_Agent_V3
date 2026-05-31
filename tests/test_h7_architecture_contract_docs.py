from __future__ import annotations

from pathlib import Path


def test_agent_orchestration_architecture_doc_exists_and_covers_required_sections() -> None:
    doc_path = Path("docs/agent_orchestration_architecture.md")
    assert doc_path.exists(), "architecture audit doc is missing"

    content = doc_path.read_text(encoding="utf-8")

    required_headings = [
        "# Agent Orchestration Architecture Audit",
        "## 1. Current Runtime Flow",
        "## 4. Hard Rules That Must Remain Deterministic",
        "## 6. Target Agent Architecture",
        "## 7. Execution Contract v2 Proposal",
        "## 8. Memory Scope Contract",
        "## 10. Migration Plan",
    ]
    for heading in required_headings:
        assert heading in content

    required_terms = [
        "execution_trace",
        "agent_actions",
        "update_graph_task",
        "current_conversation_graph",
        "dossier_confirmed_participants",
        "temporary blocks persistence and graph writes",
        "Execution Contract v2",
        "Memory Scope Contract",
    ]
    for term in required_terms:
        assert term in content

