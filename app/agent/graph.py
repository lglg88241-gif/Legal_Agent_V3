from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

if TYPE_CHECKING:
    from app.services.legal_agent import LegalGraphAgent

MAX_REFLECTION_LOOPS = 2


class AgentState(TypedDict, total=False):
    session_id: str
    question: str
    current_query: str
    routed_query: str
    tool_calls: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    response: str
    sources: list[str]
    loop_count: int
    needs_retry: bool
    retry_query: str


def reflection_router(state: AgentState) -> str:
    loop_count = int(state.get("loop_count", 0))
    state["loop_count"] = loop_count

    if state.get("needs_retry") and loop_count <= MAX_REFLECTION_LOOPS:
        return "retrieve"
    return "end"


def build_legal_agent_graph(agent: LegalGraphAgent):
    graph = StateGraph(AgentState)
    graph.add_node("tool_router", agent.tool_router_node)
    graph.add_node("vector_researcher", agent.vector_researcher_node)
    graph.add_node("advisor", agent.advisor_node)

    graph.set_entry_point("tool_router")
    graph.add_edge("tool_router", "vector_researcher")
    graph.add_edge("vector_researcher", "advisor")
    graph.add_conditional_edges(
        "advisor",
        reflection_router,
        {
            "retrieve": "vector_researcher",
            "end": END,
        },
    )

    return graph.compile()
