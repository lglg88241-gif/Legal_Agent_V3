from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

if TYPE_CHECKING:
    from app.services.legal_agent import LegalGraphAgent

MAX_GRAPH_LOOPS = 2


class AgentState(TypedDict, total=False):
    user_id: int
    session_id: str
    question: str
    current_query: str
    routed_query: str
    route_intent: str
    tool_calls: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    response: str
    sources: list[str]
    loop_count: int
    confidence_score: float
    missing_info: list[str]
    is_final: bool
    reflection_status: str
    reflection_analysis: str
    clarification_questions: list[str]
    graph_focus: str


def route_after_tool_router(state: AgentState) -> str:
    route_intent = str(state.get("route_intent", "TOOLS")).upper()
    if route_intent == "CHITCHAT":
        return "greeting"
    return "vector_researcher"


def decide_next_step(state: AgentState) -> str:
    status = str(state.get("reflection_status", "SUFFICIENT")).upper()
    loop_count = int(state.get("loop_count", 0))

    if status == "NEED_USER_INFO":
        return "clarifier"

    if status == "NEED_DEEP_GRAPH":
        if loop_count <= MAX_GRAPH_LOOPS:
            return "neo4j_specialist"
        if state.get("clarification_questions"):
            return "clarifier"
        return "legal_advisor"

    return "legal_advisor"


def build_legal_agent_graph(agent: LegalGraphAgent):
    graph = StateGraph(AgentState)
    graph.add_node("tool_router", agent.tool_router_node)
    graph.add_node("greeting", agent.greeting_node)
    graph.add_node("vector_researcher", agent.vector_researcher_node)
    graph.add_node("reflector", agent.reflector_node)
    graph.add_node("neo4j_specialist", agent.neo4j_specialist_node)
    graph.add_node("clarifier", agent.clarifier_node)
    graph.add_node("legal_advisor", agent.legal_advisor_node)

    graph.set_entry_point("tool_router")
    graph.add_conditional_edges(
        "tool_router",
        route_after_tool_router,
        {
            "greeting": "greeting",
            "vector_researcher": "vector_researcher",
        },
    )
    graph.add_edge("greeting", END)
    graph.add_edge("vector_researcher", "reflector")
    graph.add_conditional_edges(
        "reflector",
        decide_next_step,
        {
            "clarifier": "clarifier",
            "neo4j_specialist": "neo4j_specialist",
            "legal_advisor": "legal_advisor",
        },
    )
    graph.add_edge("neo4j_specialist", "reflector")
    graph.add_edge("clarifier", END)
    graph.add_edge("legal_advisor", END)

    return graph.compile()
