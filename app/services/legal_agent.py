# app/services/legal_agent.py
import logging
from typing import TypedDict, Dict, Any
from langgraph.graph import StateGraph, END
from json_repair import repair_json
import json

# 引入 V3 的底座
from app.database.neo4j_mgr import neo4j_mgr
from app.database.chroma_mgr import chroma_mgr
from app.services.llm_service import llm_service

logger = logging.getLogger(__name__)

# --- 懒加载 Reranker 单例 ---
_reranker_instance = None
def get_reranker():
    global _reranker_instance
    if _reranker_instance is None:
        from sentence_transformers import CrossEncoder
        from app.core.config import settings
        logger.info("⏳ 正在懒加载 BGE Reranker 模型...")
        _reranker_instance = CrossEncoder(settings.RERANKER_MODEL_PATH, device="cpu")
    return _reranker_instance

# --- 状态机定义 ---
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
        """编排多智能体工作流"""
        # 1. 添加节点
        self.workflow.add_node("semantic_router", self.semantic_router_node)
        self.workflow.add_node("graph_extractor", self.graph_extractor_node)
        self.workflow.add_node("vector_researcher", self.vector_researcher_node)
        self.workflow.add_node("advisor", self.advisor_node)

        # 2. 设定入口点
        self.workflow.set_entry_point("semantic_router")

        # 3. 设定条件路由边
        self.workflow.add_conditional_edges(
            "semantic_router",
            lambda state: state["intent"],
            {
                "graph_extractor": "graph_extractor",
                "vector_researcher": "vector_researcher"
            }
        )

        # 4. 设定常规边
        self.workflow.add_edge("graph_extractor", "vector_researcher")
        self.workflow.add_edge("vector_researcher", "advisor")
        self.workflow.add_edge("advisor", END)

        # 5. 编译图 (记忆 Checkpointer 将在 Phase 3 注入)
        self.app = self.workflow.compile()

    async def semantic_router_node(self, state: AgentState) -> AgentState:
        """语义路由节点：判断是描述案情还是查询法律"""
        logger.info("🚦 进入节点: 语义路由 (Semantic Router)")
        prompt = f"请判断用户输入的意图。如果是描述新案情，请输出'graph_extractor'；如果是法律咨询或追问，请输出'vector_researcher'。\n用户输入：{state['question']}"
        intent = await llm_service.async_generate(prompt)
        state["intent"] = "graph_extractor" if "graph_extractor" in intent else "vector_researcher"
        return state

    async def graph_extractor_node(self, state: AgentState) -> AgentState:
        """图谱抽取节点：防弹解析与落库"""
        logger.info("🕸️ 进入节点: 图谱抽取 (Graph Extractor)")
        prompt = f"请提取以下案情中的法律实体和关系，并以严格的 JSON 格式输出：\n{state['question']}"
        raw_json = await llm_service.async_generate(prompt)
        
        # 防弹解析：使用 json-repair 兜底
        try:
            safe_json_str = repair_json(raw_json)
            entities = json.loads(safe_json_str)
            state["extracted_entities"] = entities
            # TODO: 此处可调用 neo4j_mgr.query() 将实体写入图谱
            logger.info("✅ 图谱实体抽取成功并执行防弹修复")
        except Exception as e:
            logger.error(f"❌ JSON 解析彻底失败: {e}")
            state["extracted_entities"] = {}
            
        return state

    async def vector_researcher_node(self, state: AgentState) -> AgentState:
        """双刀流检索中心：扩写 + A/B路召回 + 交叉验证 + 精排"""
        logger.info("⚔️ 进入节点: 双刀流检索 (Vector Researcher)")
        query = state['question']
        
        # 1. 并发召回 (ChromaDB)
        chroma_results = await chroma_mgr.async_similarity_search(query, k=5)
        laws = chroma_results.get("laws", [])
        cases = chroma_results.get("cases", [])
        
        law_ids = [doc.metadata.get("id") for doc in laws if "id" in doc.metadata]
        case_ids = [doc.metadata.get("id") for doc in cases if "id" in doc.metadata]
        
        # 2. 图谱交叉验证 (Neo4j)
        cross_refs = await neo4j_mgr.get_cross_references(law_ids, case_ids)
        
        # 3. 候选池合并与精排 (BGE Reranker)
        combined_texts = [doc.page_content for doc in laws + cases]
        if cross_refs:
            combined_texts.append(f"图谱关联推理：{str(cross_refs)}")
            
        reranker = get_reranker()
        # 将 query 与所有的候选文本组成 pair 进行打分
        pairs = [[query, text] for text in combined_texts]
        if pairs:
            scores = reranker.predict(pairs)
            # 简单截断：保留得分最高的前3个
            scored_docs = sorted(zip(combined_texts, scores), key=lambda x: x[1], reverse=True)[:3]
            state["retrieved_docs"] = "\n---\n".join([doc[0] for doc in scored_docs])
        else:
            state["retrieved_docs"] = "未检索到相关法律信息。"
            
        return state

    async def advisor_node(self, state: AgentState) -> AgentState:
        """法律顾问节点：生成结构化建议"""
        logger.info("⚖️ 进入节点: 法律顾问 (Advisor)")
        system_prompt = """你是一个专业的AI法律顾问。请严格遵循以下规则：
        1. 使用 <legal_analysis> 标签包裹法理分析过程。
        2. 使用 <final_advice> 标签包裹最终给用户的实操建议。
        3. 必须基于提供的检索上下文进行回答，禁止编造法条。"""
        
        prompt = f"检索到的上下文：\n{state['retrieved_docs']}\n\n用户问题：{state['question']}"
        answer = await llm_service.async_generate(prompt, system_prompt)
        state["final_answer"] = answer
        return state

    async def ask(self, session_id: str, question: str) -> str:
        """对外暴露的核心调用接口"""
        input_state = {
            "session_id": session_id,
            "question": question,
            "intent": "",
            "extracted_entities": {},
            "retrieved_docs": "",
            "final_answer": ""
        }
        
        # 启动工作流
        final_state = await self.app.ainvoke(input_state)
        return final_state["final_answer"]

# 全局单例
legal_agent = LegalGraphAgent()