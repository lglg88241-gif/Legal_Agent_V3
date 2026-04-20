from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

if TYPE_CHECKING:
    from app.services.legal_agent import LegalGraphAgent

MAX_GRAPH_LOOPS = 2


class AgentState(TypedDict, total=False):
    user_id: int
    conversation_id: int
    session_id: str
    question: str
    is_precise_mode: bool
    proactive_graph_mode: bool
    chat_history: str
    case_summary: str
    graph_context: str
    graph_memory: str
    historical_reference: str
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
    saved_case_state: dict[str, Any] | None
    _detected_monthly_wage: Optional[float]


def route_after_tool_router(state: AgentState) -> str:
    route_intent = str(state.get("route_intent", "GENERAL_CONSULT")).upper()
    if route_intent == "GREETING":
        return "greeting"
    if route_intent == "OUT_OF_SCOPE":
        return "out_of_scope"
    if route_intent == "CASE_UPDATE":
        return "fact_updater"
    return "vector_researcher"


def route_after_vector_researcher(state: AgentState) -> str:
    route_intent = str(state.get("route_intent", "GENERAL_CONSULT")).upper()
    if route_intent == "GENERAL_CONSULT":
        return "legal_advisor"
    return "reflector"


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
    graph.add_node("out_of_scope", agent.out_of_scope_node)
    graph.add_node("fact_updater", agent.fact_updater_node)
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
            "out_of_scope": "out_of_scope",
            "fact_updater": "fact_updater",
            "vector_researcher": "vector_researcher",
        },
    )
    graph.add_edge("greeting", END)
    graph.add_edge("out_of_scope", END)
    graph.add_edge("fact_updater", "vector_researcher")
    graph.add_conditional_edges(
        "vector_researcher",
        route_after_vector_researcher,
        {
            "reflector": "reflector",
            "legal_advisor": "legal_advisor",
        },
    )
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

    try:
        from app.memory.redis_mgr import memory_mgr
    except Exception:
        return graph.compile()
    try:
        return graph.compile(checkpointer=memory_mgr)
    except TypeError:
        return graph.compile()
