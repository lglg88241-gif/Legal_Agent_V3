import logging
from typing import TypedDict, Dict, Any
from langgraph.graph import StateGraph, END
from json_repair import repair_json
import json

from app.database.neo4j_mgr import neo4j_mgr
from app.database.chroma_mgr import chroma_mgr
from app.services.llm_service import llm_service
from app.memory.redis_mgr import memory_mgr
from langgraph.checkpoint.memory import MemorySaver
logger = logging.getLogger(__name__)

_reranker_instance = None
def get_reranker():
    global _reranker_instance
    if _reranker_instance is None:
        from sentence_transformers import CrossEncoder
        from app.core.config import settings
        _reranker_instance = CrossEncoder(settings.RERANKER_MODEL_PATH, device="cpu")
    return _reranker_instance

class AgentState(TypedDict):
    session_id: str
    question: str
    intent: str
    extracted_entities: Dict[str, Any]
    retrieved_docs: str
    final_answer: str

class LegalGraphAgent:
    def __init__(self):
        self.workflow = StateGraph(AgentState)
        self._build_graph()

    def _build_graph(self):
        self.workflow.add_node("semantic_router", self.semantic_router_node)
        self.workflow.add_node("graph_extractor", self.graph_extractor_node)
        self.workflow.add_node("vector_researcher", self.vector_researcher_node)
        self.workflow.add_node("advisor", self.advisor_node)
        
        self.workflow.set_entry_point("semantic_router")
        self.workflow.add_conditional_edges(
            "semantic_router", lambda state: state["intent"],
            {"graph_extractor": "graph_extractor", "vector_researcher": "vector_researcher"}
        )
        self.workflow.add_edge("graph_extractor", "vector_researcher")
        self.workflow.add_edge("vector_researcher", "advisor")
        self.workflow.add_edge("advisor", END)
        
        # 🌟 编译图并注入 Redis 记忆云脑
        # 🌟 核心修复：暂时屏蔽旧版 Redis 记忆，换上官方内存引擎确保连通性
        # self.app = self.workflow.compile(checkpointer=memory_mgr)
        self.memory_saver = MemorySaver()
        self.app = self.workflow.compile(checkpointer=self.memory_saver)
    async def semantic_router_node(self, state: AgentState) -> AgentState:
        # 简单路由逻辑
        state["intent"] = "vector_researcher"
        return state

    async def graph_extractor_node(self, state: AgentState) -> AgentState:
        try:
            raw = await llm_service.async_generate("提取实体: " + state['question'])
            state["extracted_entities"] = json.loads(repair_json(raw))
        except:
            state["extracted_entities"] = {}
        return state

    async def vector_researcher_node(self, state: AgentState) -> AgentState:
        query = state['question']
        chroma_results = await chroma_mgr.async_similarity_search(query, k=3)
        laws = chroma_results.get("laws", [])
        cases = chroma_results.get("cases", [])
        combined = [doc.page_content for doc in laws + cases]
        
        if combined:
            reranker = get_reranker()
            scores = reranker.predict([[query, text] for text in combined])
            scored_docs = sorted(zip(combined, scores), key=lambda x: x[1], reverse=True)[:2]
            state["retrieved_docs"] = "\n".join([doc[0] for doc in scored_docs])
        else:
            state["retrieved_docs"] = "未检索到相关法条。"
        return state

    async def advisor_node(self, state: AgentState) -> AgentState:
        prompt = f"上下文：\n{state['retrieved_docs']}\n\n问题：{state['question']}"
        state["final_answer"] = await llm_service.async_generate(prompt, "你是一个专业AI法律顾问。")
        return state

    async def ask(self, session_id: str, question: str) -> str:
        input_state = {
            "session_id": session_id, "question": question, "intent": "",
            "extracted_entities": {}, "retrieved_docs": "", "final_answer": ""
        }
        # 🌟 唤醒特定用户的 Redis 记忆
        config = {"configurable": {"thread_id": session_id}}
        final_state = await self.app.ainvoke(input_state, config=config)
        return final_state["final_answer"]

legal_agent = LegalGraphAgent()
