from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from json_repair import repair_json
from langchain_community.chat_models import ChatZhipuAI
from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.graph import AgentState, build_legal_agent_graph
from app.agent.tools import AGENT_TOOLS
from app.core.config import settings

logger = logging.getLogger(__name__)

ROUTER_PROMPT = """
你是一个高精度法律工具路由引擎。你的唯一任务是根据用户问题选择需要调用的检索工具，并且只返回 JSON。

可用工具：
1. search_law_articles(query: str)
2. search_case_text(query: str)
3. query_neo4j_graph(entity_name: str)

规则：
1. 用户询问法条、法律依据、是否构成、赔偿标准、量刑标准等问题时，必须调用 search_law_articles。
2. 用户询问案例、类案、指导案例、裁判思路时，必须调用 search_case_text。
3. 用户提到具体当事人、公司、案由、罪名、判决结果线索时，必须调用 query_neo4j_graph。
4. 复合问题允许同时调用多个工具，严禁漏掉必要工具。

输出格式：
{
  "tool_calls": [
    {"name": "search_law_articles", "arguments": {"query": "..."}},
    {"name": "search_case_text", "arguments": {"query": "..."}},
    {"name": "query_neo4j_graph", "arguments": {"entity_name": "..."}}
  ]
}
""".strip()

REFLECTOR_SYSTEM_PROMPT = """
你是一个严谨的法学反思专家。请评估“当前检索结果”是否足以支撑对“用户问题”的确定性回答。
你必须只返回 JSON，格式如下：
{
  "status": "SUFFICIENT" | "NEED_DEEP_GRAPH" | "NEED_USER_INFO",
  "confidence_score": 0.0,
  "analysis": "...",
  "missing_info": ["..."],
  "questions": ["..."],
  "graph_focus": "..."
}

规则：
1. SUFFICIENT：现有法条、案例或图谱结果已足以回答。
2. NEED_DEEP_GRAPH：需要继续查询人物、案由、裁判关系等更深层图谱线索。
3. NEED_USER_INFO：缺少关键案情事实，必须先向用户追问。
4. confidence_score 取值 0 到 1。
5. questions 仅在 NEED_USER_INFO 时给出 1-2 个关键追问。
6. graph_focus 仅在 NEED_DEEP_GRAPH 时提供一个适合查询 Neo4j 的实体或案由短语。
""".strip()

LEGAL_ADVISOR_SYSTEM_PROMPT = """
你是一个严谨的中国执业律师。你必须严格依据提供的检索内容回答，绝不捏造法条、案例、金额或文件名。
引用案例时必须写出具体判决结果。若检索内容无关，直接回答未找到依据。
你必须只返回 JSON：
{
  "answer": "...",
  "sources": ["..."]
}
sources 只能填写检索结果中真实出现过的来源文件名，不能新增、改写或猜测。
""".strip()

TOOL_REGISTRY = {tool.name: tool for tool in AGENT_TOOLS}
LAW_TOOL = "search_law_articles"
CASE_TOOL = "search_case_text"
GRAPH_TOOL = "query_neo4j_graph"


class LegalGraphAgent:
    def __init__(self):
        self.router_llm = ChatZhipuAI(
            model="glm-4",
            api_key=settings.ZHIPUAI_API_KEY,
            temperature=0.1,
        )
        self.reflector_llm = ChatZhipuAI(
            model="glm-4",
            api_key=settings.ZHIPUAI_API_KEY,
            temperature=0.1,
        )
        self.expert_llm = ChatZhipuAI(
            model="glm-4",
            api_key=settings.ZHIPUAI_API_KEY,
            temperature=0.1,
        )
        self.graph = build_legal_agent_graph(self)

    async def _chat_model(
        self,
        llm: ChatZhipuAI,
        messages: list[dict[str, str]],
    ) -> str:
        langchain_messages = []
        for message in messages:
            role = message.get("role")
            content = message.get("content", "")
            if role == "system":
                langchain_messages.append(SystemMessage(content=content))
            else:
                langchain_messages.append(HumanMessage(content=content))

        response = await llm.ainvoke(langchain_messages)
        return response.content if isinstance(response.content, str) else str(response.content)

    def _safe_json_load(self, raw_content: str, fallback: dict[str, Any]) -> dict[str, Any]:
        try:
            parsed = json.loads(repair_json(raw_content))
            if isinstance(parsed, dict):
                return parsed
        except Exception as exc:
            logger.warning("Failed to parse JSON payload: %s", exc)
        return fallback

    def _extract_candidate_sources(self, tool_results: list[dict[str, Any]]) -> list[str]:
        sources: list[str] = []
        seen: set[str] = set()

        for item in tool_results:
            try:
                parsed = json.loads(item["result"])
            except Exception:
                continue

            if not isinstance(parsed, list):
                continue

            for entry in parsed:
                if not isinstance(entry, dict):
                    continue
                source = entry.get("source")
                if not source and isinstance(entry.get("metadata"), dict):
                    source = entry["metadata"].get("source")
                if not isinstance(source, str):
                    continue
                cleaned = source.strip()
                if not cleaned or cleaned in seen:
                    continue
                seen.add(cleaned)
                sources.append(cleaned)

        return sources

    def _normalize_sources(self, value: Any, candidate_sources: list[str]) -> list[str]:
        if isinstance(value, str):
            raw_sources = [value]
        elif isinstance(value, list):
            raw_sources = [item for item in value if isinstance(item, str)]
        else:
            raw_sources = []

        allowed = {source: source for source in candidate_sources}
        normalized: list[str] = []
        seen: set[str] = set()

        for source in raw_sources:
            cleaned = source.strip()
            if not cleaned:
                continue
            matched = allowed.get(cleaned)
            if not matched or matched in seen:
                continue
            seen.add(matched)
            normalized.append(matched)

        if normalized:
            return normalized

        return candidate_sources[:5]

    def _merge_tool_results(
        self,
        base_results: list[dict[str, Any]],
        new_results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()

        for item in [*base_results, *new_results]:
            key = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)

        return merged

    def _build_context_blocks(self, tool_results: list[dict[str, Any]]) -> str:
        blocks = []
        for item in tool_results:
            blocks.append(
                f"工具: {item['tool']}\n参数: {json.dumps(item['arguments'], ensure_ascii=False)}\n结果: {item['result']}"
            )
        return "\n\n".join(blocks)

    def _detect_intents(self, question: str) -> dict[str, bool]:
        lowered = question.lower()
        law_keywords = [
            "法条",
            "法律",
            "法规",
            "量刑",
            "依据",
            "民法典",
            "刑法",
            "劳动合同法",
            "赔偿",
            "算不算",
            "怎么判",
            "如何认定",
            "是否构成",
            "工伤",
        ]
        case_keywords = [
            "案例",
            "类案",
            "判决",
            "案情",
            "指导案例",
            "裁判",
            "有没有案例",
            "实际案例",
            "类似案例",
        ]
        graph_keywords = [
            "案由",
            "纠纷",
            "罪名",
            "原告",
            "被告",
            "公司",
            "劳动争议",
            "逃税",
            "合同纠纷",
            "entity",
            "case",
        ]

        has_law_intent = any(keyword in question for keyword in law_keywords)
        has_case_intent = any(keyword in question for keyword in case_keywords)
        has_graph_intent = any(keyword in question for keyword in graph_keywords) or "case" in lowered

        return {
            "has_law_intent": has_law_intent,
            "has_case_intent": has_case_intent,
            "has_graph_intent": has_graph_intent,
            "is_composite": has_law_intent and has_case_intent,
        }

    def _fallback_routes(self, question: str) -> list[dict[str, Any]]:
        intents = self._detect_intents(question)
        routes: list[dict[str, Any]] = []

        if intents["has_law_intent"]:
            routes.append({"name": LAW_TOOL, "arguments": {"query": question}})
        if intents["has_case_intent"]:
            routes.append({"name": CASE_TOOL, "arguments": {"query": question}})
        if intents["has_graph_intent"]:
            routes.append({"name": GRAPH_TOOL, "arguments": {"entity_name": question}})

        if intents["is_composite"]:
            if not any(route["name"] == LAW_TOOL for route in routes):
                routes.append({"name": LAW_TOOL, "arguments": {"query": question}})
            if not any(route["name"] == CASE_TOOL for route in routes):
                routes.append({"name": CASE_TOOL, "arguments": {"query": question}})

        if not routes:
            routes.append({"name": LAW_TOOL, "arguments": {"query": question}})
            routes.append({"name": CASE_TOOL, "arguments": {"query": question}})

        return self._dedupe_tool_calls(routes)

    def _dedupe_tool_calls(self, tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        for call in tool_calls:
            name = call["name"]
            arguments = call["arguments"]
            key = (name, json.dumps(arguments, ensure_ascii=False, sort_keys=True))
            if key in seen:
                continue
            seen.add(key)
            deduped.append({"name": name, "arguments": arguments})

        return deduped

    def _enforce_composite_routes(
        self,
        question: str,
        tool_calls: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        intents = self._detect_intents(question)
        enforced = list(tool_calls)
        names = {call["name"] for call in enforced}

        if intents["is_composite"]:
            if LAW_TOOL not in names:
                enforced.append({"name": LAW_TOOL, "arguments": {"query": question}})
            if CASE_TOOL not in names:
                enforced.append({"name": CASE_TOOL, "arguments": {"query": question}})
            if intents["has_graph_intent"] and GRAPH_TOOL not in names:
                enforced.append({"name": GRAPH_TOOL, "arguments": {"entity_name": question}})

        return self._dedupe_tool_calls(enforced)

    async def _route_tools(self, question: str) -> list[dict[str, Any]]:
        try:
            content = await self._chat_model(
                self.router_llm,
                [
                    {"role": "system", "content": ROUTER_PROMPT},
                    {"role": "user", "content": question},
                ],
            )
            parsed = self._safe_json_load(content, {"tool_calls": []})
            tool_calls = parsed.get("tool_calls", [])
            valid_calls = []
            for call in tool_calls:
                name = call.get("name")
                arguments = call.get("arguments", {})
                if name in TOOL_REGISTRY and isinstance(arguments, dict):
                    valid_calls.append({"name": name, "arguments": arguments})
            if valid_calls:
                return self._enforce_composite_routes(question, valid_calls)
        except Exception as exc:
            logger.warning("Tool routing failed, fallback to heuristic routes: %s", exc)

        return self._fallback_routes(question)

    async def _execute_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = TOOL_REGISTRY[tool_name]
        try:
            result = await asyncio.to_thread(tool.invoke, arguments)
            return {"tool": tool_name, "arguments": arguments, "result": result}
        except Exception as exc:
            logger.warning("Tool %s execution failed: %s", tool_name, exc)
            return {
                "tool": tool_name,
                "arguments": arguments,
                "result": json.dumps({"error": str(exc)}, ensure_ascii=False),
            }

    async def _execute_tool_calls(self, tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        tasks = [
            self._execute_tool_call(call["name"], call["arguments"])
            for call in tool_calls
        ]
        return await asyncio.gather(*tasks)

    async def _generate_reflection(self, state: AgentState) -> dict[str, Any]:
        tool_results = state.get("tool_results", [])
        context_blocks = self._build_context_blocks(tool_results)
        fallback = {
            "status": "NEED_USER_INFO",
            "confidence_score": 0.25,
            "analysis": "现有证据不足，无法稳定定性。",
            "missing_info": ["关键案情事实不足"],
            "questions": ["请补充案件发生时间、主体关系和争议焦点。"],
            "graph_focus": "",
        }

        content = await self._chat_model(
            self.reflector_llm,
            [
                {"role": "system", "content": REFLECTOR_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"用户问题：{state['question']}\n\n"
                        f"当前检索查询：{state.get('current_query', state['question'])}\n"
                        f"当前循环次数：{state.get('loop_count', 0)}\n\n"
                        f"当前检索结果：\n{context_blocks or '无有效检索结果'}"
                    ),
                },
            ],
        )
        parsed = self._safe_json_load(content, fallback)

        status = str(parsed.get("status", fallback["status"])).upper()
        if status not in {"SUFFICIENT", "NEED_DEEP_GRAPH", "NEED_USER_INFO"}:
            status = fallback["status"]

        confidence_score = parsed.get("confidence_score", fallback["confidence_score"])
        try:
            confidence_value = float(confidence_score)
        except Exception:
            confidence_value = float(fallback["confidence_score"])
        confidence_value = max(0.0, min(1.0, confidence_value))

        missing_info = parsed.get("missing_info", [])
        if not isinstance(missing_info, list):
            missing_info = []
        missing_info = [item.strip() for item in missing_info if isinstance(item, str) and item.strip()]

        questions = parsed.get("questions", [])
        if not isinstance(questions, list):
            questions = []
        questions = [item.strip() for item in questions if isinstance(item, str) and item.strip()][:2]

        analysis = parsed.get("analysis", fallback["analysis"])
        if not isinstance(analysis, str) or not analysis.strip():
            analysis = fallback["analysis"]

        graph_focus = parsed.get("graph_focus", "")
        if not isinstance(graph_focus, str):
            graph_focus = ""
        graph_focus = graph_focus.strip() or state.get("current_query", state["question"])

        return {
            "reflection_status": status,
            "confidence_score": confidence_value,
            "reflection_analysis": analysis.strip(),
            "missing_info": missing_info,
            "clarification_questions": questions,
            "graph_focus": graph_focus,
        }

    async def _generate_final_answer(self, state: AgentState) -> dict[str, Any]:
        tool_results = state.get("tool_results", [])
        candidate_sources = self._extract_candidate_sources(tool_results)
        context_blocks = self._build_context_blocks(tool_results)
        fallback = {
            "answer": "未找到依据。",
            "sources": [],
        }

        content = await self._chat_model(
            self.expert_llm,
            [
                {"role": "system", "content": LEGAL_ADVISOR_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"用户问题：{state['question']}\n\n"
                        f"反思结论：{state.get('reflection_status', 'SUFFICIENT')}\n"
                        f"证据置信度：{state.get('confidence_score', 0.0)}\n"
                        f"可用来源文件：{json.dumps(candidate_sources, ensure_ascii=False)}\n\n"
                        f"检索上下文：\n{context_blocks or '无有效检索结果'}"
                    ),
                },
            ],
        )
        parsed = self._safe_json_load(content, fallback)

        answer = parsed.get("answer") or parsed.get("response") or fallback["answer"]
        if not isinstance(answer, str) or not answer.strip():
            answer = fallback["answer"]

        sources = self._normalize_sources(parsed.get("sources", []), candidate_sources)

        return {
            "response": answer.strip(),
            "sources": sources,
            "is_final": True,
        }

    async def tool_router_node(self, state: AgentState) -> AgentState:
        current_query = state.get("current_query") or state["question"]
        tool_calls = await self._route_tools(current_query)
        return {
            "current_query": current_query,
            "routed_query": current_query,
            "tool_calls": tool_calls,
        }

    async def vector_researcher_node(self, state: AgentState) -> AgentState:
        current_query = state.get("current_query") or state["question"]
        tool_calls = state.get("tool_calls") or []

        if not tool_calls or state.get("routed_query") != current_query:
            tool_calls = await self._route_tools(current_query)

        tool_results = await self._execute_tool_calls(tool_calls)
        return {
            "current_query": current_query,
            "routed_query": current_query,
            "tool_calls": tool_calls,
            "tool_results": self._merge_tool_results([], tool_results),
        }

    async def reflector_node(self, state: AgentState) -> AgentState:
        reflection = await self._generate_reflection(state)
        return {
            **reflection,
            "loop_count": int(state.get("loop_count", 0)) + 1,
        }

    async def neo4j_specialist_node(self, state: AgentState) -> AgentState:
        graph_focus = state.get("graph_focus") or state.get("current_query") or state["question"]
        graph_result = await self._execute_tool_call(
            GRAPH_TOOL,
            {"entity_name": graph_focus},
        )
        merged_results = self._merge_tool_results(state.get("tool_results", []), [graph_result])
        return {
            "tool_results": merged_results,
            "current_query": graph_focus,
        }

    async def clarifier_node(self, state: AgentState) -> AgentState:
        progress = state.get("reflection_analysis", "现有材料还不足以给出稳定结论。").strip()
        missing_info = state.get("missing_info", [])
        questions = state.get("clarification_questions", [])

        lines = [f"目前进展：{progress}"]
        if missing_info:
            lines.append("我还缺少这些关键案情要素：" + "；".join(missing_info))
        if questions:
            lines.append("请优先补充以下信息：")
            for index, question in enumerate(questions, start=1):
                lines.append(f"{index}. {question}")
        else:
            lines.append("请补充案件时间、主体关系、争议焦点等关键信息。")

        return {
            "response": "\n".join(lines),
            "sources": self._extract_candidate_sources(state.get("tool_results", [])),
            "is_final": False,
        }

    async def legal_advisor_node(self, state: AgentState) -> AgentState:
        return await self._generate_final_answer(state)

    async def ask(self, session_id: str, question: str) -> dict[str, Any]:
        initial_state: AgentState = {
            "session_id": session_id,
            "question": question,
            "current_query": question,
            "loop_count": 0,
            "confidence_score": 0.0,
            "missing_info": [],
            "clarification_questions": [],
            "tool_calls": [],
            "tool_results": [],
            "sources": [],
            "response": "",
            "reflection_status": "",
            "reflection_analysis": "",
            "graph_focus": "",
            "is_final": False,
        }

        final_state = await self.graph.ainvoke(initial_state)
        return {
            "response": final_state.get("response") or "未找到依据。",
            "sources": final_state.get("sources") or [],
            "is_final": bool(final_state.get("is_final", False)),
            "confidence_score": float(final_state.get("confidence_score", 0.0)),
            "missing_info": final_state.get("missing_info") or [],
        }


legal_agent = LegalGraphAgent()
