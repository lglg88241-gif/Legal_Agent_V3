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
你是一个高精度的意图识别路由引擎。你的唯一任务是判断用户问题需要调用哪些检索工具，并且只返回 JSON。
可用工具：
1. search_law_articles(query: str)
2. search_case_text(query: str)
3. query_neo4j_graph(entity_name: str)

判定规则：
1. 只要用户在问法律定性、法律依据、是否构成、算不算、怎么判、量刑标准、赔偿标准、适用什么法条，必须调用 search_law_articles。
2. 只要用户在问有没有案例、类似案例、实际案例、指导案例、同类判决、裁判思路，必须调用 search_case_text。
3. 只要用户提到具体当事人、公司名称、案由、罪名、判决结果线索，必须调用 query_neo4j_graph。
4. 当用户问题同时包含“法律定性/咨询”和“找案例”两类意图时，你必须同时返回 search_law_articles 和 search_case_text；如果问题里还出现当事人、罪名、案由等实体线索，还必须一并返回 query_neo4j_graph。

核心规则：
当用户的提问同时包含“法律定性/咨询”和“找案例”时，你必须同时在 JSON 数组中输出法条检索工具 search_law_articles 和案例检索工具 search_case_text 或 query_neo4j_graph。

绝对禁止：
1. 遇到复合问题时，绝不能只调用单一工具。
2. 绝不能输出 JSON 以外的解释、前后缀或 Markdown。

返回格式：
{
  "tool_calls": [
    {"name": "search_law_articles", "arguments": {"query": "..."}},
    {"name": "search_case_text", "arguments": {"query": "..."}},
    {"name": "query_neo4j_graph", "arguments": {"entity_name": "..."}}
  ]
}
""".strip()

EXPERT_SYSTEM_PROMPT = """
你是一个严谨的中国执业律师。你必须严格依据提供的检索内容回答，绝不捏造法条、案例、金额或文件名。
引用案例时必须写出具体判决结果。若检索内容无关，直接回答未找到依据。
你必须只返回 JSON，对象格式固定为：
{
  "needs_retry": false,
  "retry_query": "",
  "answer": "...",
  "sources": ["..."]
}
如果证据不足，请将 needs_retry 设为 true，并在 retry_query 中提出新的检索方向。
如果证据充足，请将 needs_retry 设为 false，并基于证据生成专业建议。
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
        self.expert_llm = ChatZhipuAI(
            model="glm-4",
            api_key=settings.ZHIPUAI_API_KEY,
            temperature=0.1,
        )
        self.graph = build_legal_agent_graph(self)

    async def _chat_with_router_model(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 1024,
    ) -> str:
        del max_tokens
        langchain_messages = []
        for message in messages:
            role = message.get("role")
            content = message.get("content", "")
            if role == "system":
                langchain_messages.append(SystemMessage(content=content))
            else:
                langchain_messages.append(HumanMessage(content=content))

        response = await self.router_llm.ainvoke(langchain_messages)
        return response.content if isinstance(response.content, str) else str(response.content)

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

    def _merge_retry_query(self, current_query: str, retry_query: str) -> str:
        refined = retry_query.strip()
        if not refined:
            return current_query
        if refined in current_query:
            return current_query
        return f"{current_query} {refined}".strip()

    async def _generate_advisor_decision(
        self,
        question: str,
        current_query: str,
        tool_results: list[dict[str, Any]],
        loop_count: int,
    ) -> dict[str, Any]:
        candidate_sources = self._extract_candidate_sources(tool_results)
        context_blocks = []
        for item in tool_results:
            context_blocks.append(
                f"工具: {item['tool']}\n参数: {json.dumps(item['arguments'], ensure_ascii=False)}\n结果: {item['result']}"
            )

        user_prompt = (
            f"用户问题：{question}\n\n"
            f"当前检索查询：{current_query}\n"
            f"当前循环次数：{loop_count}\n"
            f"可用来源文件：{json.dumps(candidate_sources, ensure_ascii=False)}\n\n"
            f"以下是全部检索上下文：\n{chr(10).join(context_blocks)}\n\n"
            "请先评估检索到的上下文是否足以回答用户问题。"
            "如果证据不足，请设置 needs_retry=true，并在 retry_query 中给出新的检索方向。"
            "如果证据充足，请设置 needs_retry=false，并基于证据输出专业建议。"
            "必须严格返回 JSON。"
        )

        response = await self.expert_llm.ainvoke(
            [
                SystemMessage(content=EXPERT_SYSTEM_PROMPT),
                HumanMessage(content=user_prompt),
            ]
        )
        raw_content = response.content if isinstance(response.content, str) else str(response.content)

        try:
            parsed = json.loads(repair_json(raw_content))
            answer = parsed.get("answer") or parsed.get("response") or ""
            retry_query = parsed.get("retry_query") or parsed.get("refined_query") or ""
            needs_retry = bool(parsed.get("needs_retry"))
            sources = self._normalize_sources(parsed.get("sources", []), candidate_sources)

            if not isinstance(answer, str):
                answer = ""
            if not isinstance(retry_query, str):
                retry_query = ""

            if needs_retry and not retry_query.strip():
                needs_retry = False

            return {
                "response": answer.strip() or "未找到依据。",
                "sources": sources,
                "needs_retry": needs_retry,
                "retry_query": retry_query.strip(),
            }
        except Exception as exc:
            logger.warning("Failed to parse advisor response as JSON: %s", exc)
            fallback_response = raw_content.strip() if raw_content.strip() else "未找到依据。"
            return {
                "response": fallback_response,
                "sources": candidate_sources[:5],
                "needs_retry": False,
                "retry_query": "",
            }

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
            "消费者权益",
            "算不算",
            "怎么判",
            "如何认定",
            "是否构成",
            "赔偿标准",
            "工伤",
        ]
        case_keywords = [
            "案例",
            "类案",
            "判决",
            "案情",
            "作案手法",
            "历史案例",
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
            content = await self._chat_with_router_model(
                [
                    {"role": "system", "content": ROUTER_PROMPT},
                    {"role": "user", "content": question},
                ],
                max_tokens=300,
            )
            parsed = json.loads(repair_json(content))
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
            "tool_results": tool_results,
        }

    async def advisor_node(self, state: AgentState) -> AgentState:
        current_query = state.get("current_query") or state["question"]
        loop_count = int(state.get("loop_count", 0))
        decision = await self._generate_advisor_decision(
            question=state["question"],
            current_query=current_query,
            tool_results=state.get("tool_results", []),
            loop_count=loop_count,
        )

        updated_loop_count = loop_count + 1
        retry_query = decision.get("retry_query", "")
        needs_retry = bool(decision.get("needs_retry"))
        next_query = current_query

        if needs_retry:
            next_query = self._merge_retry_query(current_query, retry_query)
            if next_query == current_query:
                needs_retry = False

        return {
            "response": decision.get("response", "未找到依据。"),
            "sources": decision.get("sources", []),
            "needs_retry": needs_retry,
            "retry_query": retry_query,
            "current_query": next_query,
            "loop_count": updated_loop_count,
        }

    async def ask(self, session_id: str, question: str) -> dict[str, Any]:
        initial_state: AgentState = {
            "session_id": session_id,
            "question": question,
            "current_query": question,
            "loop_count": 0,
            "needs_retry": False,
            "tool_calls": [],
            "tool_results": [],
            "sources": [],
            "response": "",
            "retry_query": "",
        }

        final_state = await self.graph.ainvoke(initial_state)
        response = final_state.get("response") or "未找到依据。"
        sources = final_state.get("sources") or []

        return {
            "response": response,
            "sources": sources,
        }


legal_agent = LegalGraphAgent()
