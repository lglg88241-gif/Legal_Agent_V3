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
你是一个高精度的法律工具路由引擎。你的任务只有两类：
1. 输出 CHITCHAT：当用户只是打招呼、寒暄、确认你是否在线，例如“你好”“在吗”“hi”“hello”“早上好”，且没有任何实质法律纠纷或法律咨询内容时。
2. 输出 TOOLS：当用户在咨询法律条文、责任认定、赔偿、量刑、案例、案由、被告、原告、公司纠纷等问题时。

可用工具：
1. search_law_articles(query: str)
2. search_case_text(query: str)
3. query_neo4j_graph(entity_name: str)

核心规则：
1. 纯闲聊、纯问候、纯试探在线状态，必须果断输出 CHITCHAT，禁止调用任何检索工具。
2. 法条、法律依据、构成要件、赔偿标准、量刑标准等问题，必须调用 search_law_articles。
3. 历史案例、类案、指导案例、裁判思路等问题，必须调用 search_case_text。
4. 当问题涉及具体当事人、公司名称、案由、罪名、判决结果线索时，应调用 query_neo4j_graph。
5. 复合问题允许同时调用多个工具，严禁漏掉必要工具。

你必须只返回 JSON，不要解释。格式如下：
{
  "route_intent": "CHITCHAT" | "TOOLS",
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
2. NEED_DEEP_GRAPH：还需要进一步查询人物、案由、裁判关系等更深层图谱线索。
3. NEED_USER_INFO：缺少关键案情事实，必须先向用户追问。
4. confidence_score 取值 0 到 1。
5. questions 仅在 NEED_USER_INFO 时给出 1 到 2 个关键追问。
6. graph_focus 仅在 NEED_DEEP_GRAPH 时提供适合查询 Neo4j 的实体或案由短语。
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

CLARIFIER_SYSTEM_PROMPT = """
你是一个真实的律师。请根据缺少的案情，直接向用户发问。
严禁输出“目前进展”“我还缺少这些关键案情要素”“根据当前分析”等机器分析日志。
只需输出最自然的一句话追问，语气专业、简洁、口语化。
""".strip()

TOOL_REGISTRY = {tool.name: tool for tool in AGENT_TOOLS}
LAW_TOOL = "search_law_articles"
CASE_TOOL = "search_case_text"
GRAPH_TOOL = "query_neo4j_graph"
CHITCHAT_INTENT = "CHITCHAT"
TOOLS_INTENT = "TOOLS"
GREETING_RESPONSE = (
    "你好，我在。你可以直接告诉我你的法律问题，比如劳动纠纷、工伤认定、赔偿标准、合同争议或是否有类似案例。"
)


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

    def _has_substantive_legal_intent(self, question: str) -> bool:
        keywords = [
            "法条",
            "法律",
            "法规",
            "赔偿",
            "赔付",
            "量刑",
            "判几年",
            "工伤",
            "劳动仲裁",
            "合同",
            "纠纷",
            "起诉",
            "犯罪",
            "构成",
            "认定",
            "案例",
            "判决",
            "案由",
            "被告",
            "原告",
            "公司",
            "辞退",
            "离职",
            "工资",
            "补偿",
            "责任",
        ]
        return any(keyword in question for keyword in keywords)

    def _looks_like_chitchat(self, question: str) -> bool:
        cleaned = "".join(question.lower().strip().split())
        if not cleaned:
            return True
        if self._has_substantive_legal_intent(question):
            return False

        greetings = {
            "你好",
            "您好",
            "hello",
            "hi",
            "hey",
            "在吗",
            "在不在",
            "有人吗",
            "忙吗",
            "早上好",
            "中午好",
            "下午好",
            "晚上好",
            "嗨",
            "哈喽",
            "hello?",
            "hi?",
        }
        punctuation = {"?", "？", "!", "！", ".", "。", "~"}
        if cleaned in greetings:
            return True
        return len(cleaned) <= 6 and all(char in punctuation for char in cleaned)

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
            "相似案例",
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
        is_chitchat = self._looks_like_chitchat(question)

        return {
            "has_law_intent": has_law_intent,
            "has_case_intent": has_case_intent,
            "has_graph_intent": has_graph_intent,
            "is_composite": has_law_intent and has_case_intent,
            "is_chitchat": is_chitchat,
        }

    def _fallback_route_decision(self, question: str) -> dict[str, Any]:
        intents = self._detect_intents(question)
        if intents["is_chitchat"]:
            return {"route_intent": CHITCHAT_INTENT, "tool_calls": []}

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

        return {
            "route_intent": TOOLS_INTENT,
            "tool_calls": self._dedupe_tool_calls(routes),
        }

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

    async def _route_decision(self, question: str) -> dict[str, Any]:
        heuristic_decision = self._fallback_route_decision(question)
        if heuristic_decision["route_intent"] == CHITCHAT_INTENT:
            return heuristic_decision

        try:
            content = await self._chat_model(
                self.router_llm,
                [
                    {"role": "system", "content": ROUTER_PROMPT},
                    {"role": "user", "content": question},
                ],
            )
            parsed = self._safe_json_load(
                content,
                {"route_intent": TOOLS_INTENT, "tool_calls": []},
            )
            route_intent = str(parsed.get("route_intent", TOOLS_INTENT)).upper()
            if route_intent == CHITCHAT_INTENT:
                return {"route_intent": CHITCHAT_INTENT, "tool_calls": []}

            tool_calls = parsed.get("tool_calls", [])
            valid_calls = []
            for call in tool_calls:
                name = call.get("name")
                arguments = call.get("arguments", {})
                if name in TOOL_REGISTRY and isinstance(arguments, dict):
                    valid_calls.append({"name": name, "arguments": arguments})

            if valid_calls:
                return {
                    "route_intent": TOOLS_INTENT,
                    "tool_calls": self._enforce_composite_routes(question, valid_calls),
                }
        except Exception as exc:
            logger.warning("Tool routing failed, fallback to heuristic routes: %s", exc)

        return heuristic_decision

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

    async def _generate_clarifier_question(self, state: AgentState) -> str:
        missing_info = state.get("missing_info", [])
        questions = state.get("clarification_questions", [])
        fallback = questions[0] if questions else "请再补充一下案件发生时间、双方关系和争议焦点。"

        try:
            response = await self._chat_model(
                self.expert_llm,
                [
                    {"role": "system", "content": CLARIFIER_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"用户原问题：{state['question']}\n"
                            f"缺失信息：{json.dumps(missing_info, ensure_ascii=False)}\n"
                            f"建议追问：{json.dumps(questions, ensure_ascii=False)}"
                        ),
                    },
                ],
            )
            cleaned = " ".join((response or "").strip().split())
            return cleaned or fallback
        except Exception as exc:
            logger.warning("Clarifier generation failed, fallback to template question: %s", exc)
            return fallback

    async def tool_router_node(self, state: AgentState) -> AgentState:
        current_query = state.get("current_query") or state["question"]
        decision = await self._route_decision(current_query)
        return {
            "current_query": current_query,
            "routed_query": current_query,
            "route_intent": decision["route_intent"],
            "tool_calls": decision["tool_calls"],
        }

    async def greeting_node(self, state: AgentState) -> AgentState:
        return {
            "response": GREETING_RESPONSE,
            "sources": [],
            "is_final": True,
        }

    async def vector_researcher_node(self, state: AgentState) -> AgentState:
        current_query = state.get("current_query") or state["question"]
        tool_calls = state.get("tool_calls") or []

        if not tool_calls or state.get("routed_query") != current_query:
            decision = await self._route_decision(current_query)
            tool_calls = decision["tool_calls"]

        tool_results = await self._execute_tool_calls(tool_calls)
        return {
            "current_query": current_query,
            "routed_query": current_query,
            "route_intent": TOOLS_INTENT,
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
        question = await self._generate_clarifier_question(state)
        return {
            "response": question,
            "sources": self._extract_candidate_sources(state.get("tool_results", [])),
            "is_final": False,
        }

    async def legal_advisor_node(self, state: AgentState) -> AgentState:
        return await self._generate_final_answer(state)

    async def ask(self, user_id: int, session_id: str, question: str) -> dict[str, Any]:
        initial_state: AgentState = {
            "user_id": user_id,
            "session_id": session_id,
            "question": question,
            "current_query": question,
            "route_intent": TOOLS_INTENT,
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

        final_state = await self.graph.ainvoke(
            initial_state,
            config={
                "configurable": {
                    "thread_id": session_id,
                    "user_id": str(user_id),
                }
            },
        )
        return {
            "response": final_state.get("response") or "未找到依据。",
            "sources": final_state.get("sources") or [],
            "is_final": bool(final_state.get("is_final", False)),
            "confidence_score": float(final_state.get("confidence_score", 0.0)),
            "missing_info": final_state.get("missing_info") or [],
        }


legal_agent = LegalGraphAgent()
