from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from json_repair import repair_json
from langchain_community.chat_models import ChatZhipuAI
from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.graph import AgentState, build_legal_agent_graph
from app.agent.tools import AGENT_TOOLS
from app.agent.prompts.advisor import (  # noqa: E402
    REFLECTOR_SYSTEM_PROMPT_V2,
    CLARIFIER_SYSTEM_PROMPT_V2,
    TRIPLE_TRACK_ROUTER_PROMPT_V2,
    FACT_UPDATER_SYSTEM_PROMPT_V2,
)
from app.core.config import settings
from app.services.graph_extractor import format_graph_memory

logger = logging.getLogger(__name__)

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

    def _proactive_graph_mode_enabled(self, state: AgentState) -> bool:
        return bool(state.get("proactive_graph_mode", True))

    def _build_reflector_user_prompt(
        self,
        state: AgentState,
        chat_history: str,
        context_blocks: str,
    ) -> str:
        mode_enabled = self._proactive_graph_mode_enabled(state)
        mode_label = "ON" if mode_enabled else "OFF"
        mode_instruction = (
            "主动案情梳理模式已开启。除了法律要件是否缺失，你还必须检查用户最新问题里是否存在指代不明的实体、代词或模糊称谓。"
            "如果存在，把最关键的 1 到 2 个实体澄清要求写入 missing_info。"
        )
        if not mode_enabled:
            mode_instruction = (
                "主动案情梳理模式已关闭。你只关注法律要件是否缺失，忽略“他”“老板”“对方”“那辆车”这类实体指代歧义。"
            )

        return (
            f"用户问题：{state['question']}\n\n"
            f"主动案情梳理模式：{mode_label}\n"
            f"{mode_instruction}\n\n"
            f"历史对话：\n{chat_history}\n\n"
            "请注意：用户在历史对话中已经提供过的案情要素，绝对视为已知证据，严禁将其再次列入 missing_info 列表。\n\n"
            f"当前检索查询：{state.get('current_query', state['question'])}\n"
            f"当前循环次数：{state.get('loop_count', 0)}\n\n"
            f"当前检索结果：\n{context_blocks or '无有效检索结果'}"
        )

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

    def _history_indicates_legal_dialogue(self, chat_history: str) -> bool:
        history = (chat_history or "").strip()
        if not history:
            return False

        keywords = [
            "法条",
            "法律",
            "赔偿",
            "工伤",
            "劳动",
            "合同",
            "纠纷",
            "仲裁",
            "诉讼",
            "责任",
            "案由",
            "案例",
            "判决",
            "赔多少",
            "算不算",
            "是否",
            "认定",
            "解除",
            "公司",
            "离职",
            "工资",
            "补偿",
        ]
        return any(keyword in history for keyword in keywords)

    def _is_contextual_followup(self, question: str, chat_history: str) -> bool:
        cleaned = "".join(question.lower().strip().split())
        if not cleaned or not self._history_indicates_legal_dialogue(chat_history):
            return False

        short_replies = {
            "是",
            "对",
            "对的",
            "嗯",
            "嗯嗯",
            "有",
            "没有",
            "不是",
            "是的",
            "有的",
            "没有的",
            "对啊",
            "是啊",
            "好",
            "好的",
            "行",
        }
        return cleaned in short_replies or len(cleaned) <= 4

    def _looks_like_chitchat(self, question: str, chat_history: str = "") -> bool:
        cleaned = "".join(question.lower().strip().split())
        if not cleaned:
            return True
        if self._is_contextual_followup(question, chat_history):
            return False
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

    def _detect_intents(self, question: str, chat_history: str = "") -> dict[str, bool]:
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
        is_chitchat = self._looks_like_chitchat(question, chat_history=chat_history)

        return {
            "has_law_intent": has_law_intent,
            "has_case_intent": has_case_intent,
            "has_graph_intent": has_graph_intent,
            "is_composite": has_law_intent and has_case_intent,
            "is_chitchat": is_chitchat,
        }

    def _fallback_route_decision(self, question: str, chat_history: str = "") -> dict[str, Any]:
        intents = self._detect_intents(question, chat_history=chat_history)
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

    async def greeting_node(self, state: AgentState) -> AgentState:
        return {
            "response": GREETING_RESPONSE,
            "sources": [],
            "is_final": True,
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

    async def reflector_node(self, state: AgentState) -> AgentState:
        reflection = await self._generate_reflection(state)
        return {
            **reflection,
            "loop_count": int(state.get("loop_count", 0)) + 1,
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


# TRIPLE_TRACK_ROUTER_PROMPT_V2 and FACT_UPDATER_SYSTEM_PROMPT_V2
# are now imported from app.agent.prompts.advisor (see top-level imports).


def _patched_is_case_update(self: LegalGraphAgent, question: str, chat_history: str = "", case_summary: str = "") -> bool:
    text = " ".join((question or "").strip().split())
    if not text:
        return False

    update_tokens = [
        "是",
        "不是",
        "没有",
        "有",
        "对",
        "不对",
        "补充",
        "更正",
        "改成",
        "记错",
        "纠正",
        "其实",
        "实际上",
        "后来",
        "老板",
        "对方",
        "那辆车",
        "那个公司",
        "他",
        "她",
    ]
    if any(token in text for token in update_tokens):
        return bool((chat_history or "").strip() or (case_summary or "").strip()) or len(text) <= 12
    return self._is_contextual_followup(text, chat_history) and bool((case_summary or "").strip() or (chat_history or "").strip())


def _patched_is_general_consult(self: LegalGraphAgent, question: str, case_summary: str = "") -> bool:
    text = " ".join((question or "").strip().split())
    if not text:
        return False
    if self._has_substantive_legal_intent(text) and not self._patched_is_case_update(text, case_summary=case_summary):
        return True

    consult_tokens = [
        "怎么判",
        "赔多少",
        "量刑",
        "法条",
        "法律依据",
        "算不算",
        "是否构成",
        "认定",
        "标准",
        "流程",
        "程序",
    ]
    return any(token in text for token in consult_tokens)


def _question_looks_like_brief_followup(question: str) -> bool:
    text = " ".join((question or "").strip().split())
    if not text:
        return False

    brief_followup_tokens = [
        "怎么办",
        "怎么维权",
        "怎么处理",
        "怎么赔",
        "赔多少",
        "怎么告",
        "怎么仲裁",
        "接下来呢",
        "然后呢",
    ]
    if any(token in text for token in brief_followup_tokens):
        return True

    condensed = "".join(text.split())
    return len(condensed) <= 8 and any(token in condensed for token in ["怎么办", "怎么赔", "怎么告", "维权"])


def _history_has_case_continuity(chat_history: str, case_summary: str) -> bool:
    if (case_summary or "").strip():
        return True

    history = " ".join((chat_history or "").strip().split())
    if not history:
        return False

    case_markers = [
        "公司",
        "老板",
        "工资",
        "赔偿",
        "劳动合同",
        "仲裁",
        "原告",
        "被告",
        "法院",
        "工伤",
        "交通事故",
        "入职",
        "离职",
        "解除劳动关系",
        "金额",
        "对方",
    ]
    matched = sum(1 for marker in case_markers if marker in history)
    return matched >= 2


def _patched_build_tool_calls_for_intent(
    self: LegalGraphAgent,
    route_intent: str,
    query: str,
) -> list[dict[str, Any]]:
    cleaned_query = " ".join((query or "").strip().split())
    if not cleaned_query or route_intent == "GREETING":
        return []

    if route_intent == "CASE_UPDATE":
        routes = [
            {"name": LAW_TOOL, "arguments": {"query": cleaned_query}},
            {"name": CASE_TOOL, "arguments": {"query": cleaned_query}},
        ]
        if any(token in cleaned_query for token in ["公司", "老板", "对方", "原告", "被告", "案由", "罪名"]):
            routes.append({"name": GRAPH_TOOL, "arguments": {"entity_name": cleaned_query}})
        return self._dedupe_tool_calls(routes)

    fallback = self._fallback_route_decision(cleaned_query)
    return fallback.get("tool_calls", [])


async def _patched_route_decision(
    self: LegalGraphAgent,
    question: str,
    chat_history: str = "",
    case_summary: str = "",
    is_precise_mode: bool = False,
) -> dict[str, Any]:
    history_text = (chat_history or "").strip() or "无历史对话"
    has_case_summary = bool((case_summary or "").strip())
    summary_text = (case_summary or "").strip() or "无案情摘要"
    cleaned_question = " ".join((question or "").strip().split())

    if is_precise_mode:
        retrieval_query = summary_text if has_case_summary else cleaned_question
        return {
            "route_intent": "CASE_UPDATE",
            "tool_calls": self._patched_build_tool_calls_for_intent("CASE_UPDATE", retrieval_query),
        }

    if _history_has_case_continuity(chat_history, case_summary) and (
        self._patched_is_case_update(cleaned_question, chat_history=chat_history, case_summary=case_summary)
        or _question_looks_like_brief_followup(cleaned_question)
    ):
        retrieval_query = summary_text if has_case_summary else cleaned_question
        return {
            "route_intent": "CASE_UPDATE",
            "tool_calls": self._patched_build_tool_calls_for_intent("CASE_UPDATE", retrieval_query),
        }

    if self._looks_like_chitchat(cleaned_question, chat_history=chat_history) and not self._patched_is_case_update(
        cleaned_question, chat_history=chat_history, case_summary=case_summary
    ):
        return {"route_intent": "GREETING", "tool_calls": []}

    fallback_intent = "CASE_UPDATE" if self._patched_is_case_update(
        cleaned_question,
        chat_history=chat_history,
        case_summary=case_summary,
    ) else "GENERAL_CONSULT"
    if not self._patched_is_general_consult(cleaned_question, case_summary=case_summary):
        fallback_intent = "CASE_UPDATE" if fallback_intent != "GREETING" and summary_text != "无案情摘要" else fallback_intent

    try:
        content = await self._chat_model(
            self.router_llm,
            [
                {"role": "system", "content": TRIPLE_TRACK_ROUTER_PROMPT_V2},
                {
                    "role": "user",
                    "content": (
                        f"历史对话：\n{history_text}\n\n"
                        f"当前卷宗摘要：\n{summary_text}\n\n"
                        f"用户最新输入：{cleaned_question}"
                    ),
                },
            ],
        )
        parsed = self._safe_json_load(content, {"route_intent": fallback_intent})
        route_intent = str(parsed.get("route_intent", fallback_intent)).upper()
        if route_intent not in {"GREETING", "GENERAL_CONSULT", "CASE_UPDATE"}:
            route_intent = fallback_intent
    except Exception as exc:
        logger.warning("Triple-track routing failed, fallback to heuristic route: %s", exc)
        route_intent = fallback_intent

    retrieval_query = cleaned_question
    if route_intent == "CASE_UPDATE" and has_case_summary:
        retrieval_query = summary_text

    return {
        "route_intent": route_intent,
        "tool_calls": self._patched_build_tool_calls_for_intent(route_intent, retrieval_query),
    }


async def _patched_fact_updater_node(self: LegalGraphAgent, state: AgentState) -> AgentState:
    previous_summary = (state.get("case_summary") or "").strip()
    question = " ".join((state.get("question") or "").strip().split())
    fallback_summary = previous_summary or question

    try:
        content = await self._chat_model(
            self.expert_llm,
            [
                {"role": "system", "content": FACT_UPDATER_SYSTEM_PROMPT_V2},
                {
                    "role": "user",
                    "content": (
                        f"旧摘要：{previous_summary or '无'}\n\n"
                        f"用户最新输入：{question}"
                    ),
                },
            ],
        )
        parsed = self._safe_json_load(content, {"case_summary": fallback_summary})
        case_summary = parsed.get("case_summary", fallback_summary)
        if not isinstance(case_summary, str):
            case_summary = fallback_summary
        case_summary = " ".join(case_summary.strip().split())
    except Exception as exc:
        logger.warning("Fact updater failed, fallback to previous summary: %s", exc)
        case_summary = fallback_summary

    retrieval_query = case_summary or question
    return {
        "case_summary": case_summary,
        "current_query": retrieval_query,
        "routed_query": "",
        "tool_calls": self._patched_build_tool_calls_for_intent("CASE_UPDATE", retrieval_query),
    }


async def _patched_tool_router_node(self: LegalGraphAgent, state: AgentState) -> AgentState:
    current_query = state.get("current_query") or state["question"]
    chat_history = state.get("chat_history", "")
    case_summary = state.get("case_summary", "")
    decision = await self._patched_route_decision(
        current_query,
        chat_history=chat_history,
        case_summary=case_summary,
        is_precise_mode=bool(state.get("is_precise_mode", False)),
    )
    return {
        "current_query": current_query,
        "routed_query": current_query,
        "route_intent": decision["route_intent"],
        "tool_calls": decision["tool_calls"],
    }


async def _patched_vector_researcher_node(self: LegalGraphAgent, state: AgentState) -> AgentState:
    current_query = state.get("current_query") or state["question"]
    route_intent = str(state.get("route_intent", "GENERAL_CONSULT")).upper()
    chat_history = state.get("chat_history", "")
    case_summary = state.get("case_summary", "")
    conversation_id = state.get("conversation_id")
    tool_calls = state.get("tool_calls") or []

    if not tool_calls or state.get("routed_query") != current_query:
        tool_calls = self._patched_build_tool_calls_for_intent(route_intent, current_query)

    enriched_tool_calls = []
    for call in tool_calls:
        arguments = dict(call["arguments"])
        if call["name"] in {LAW_TOOL, CASE_TOOL}:
            arguments["chat_history"] = case_summary or chat_history
        enriched_tool_calls.append({"name": call["name"], "arguments": arguments})

    graph_context = (state.get("graph_context") or "").strip() or (state.get("graph_memory") or "").strip()
    retrieval_task = self._execute_tool_calls(enriched_tool_calls)
    graph_task = None
    if conversation_id:
        graph_task = format_graph_memory(int(conversation_id))

    if graph_task is not None:
        tool_results, latest_graph_memory = await asyncio.gather(retrieval_task, graph_task)
        graph_context = (
            f"当前案件图谱事实：\n{latest_graph_memory.strip()}"
            if isinstance(latest_graph_memory, str) and latest_graph_memory.strip()
            else graph_context
        )
    else:
        tool_results = await retrieval_task

    return {
        "current_query": current_query,
        "routed_query": current_query,
        "route_intent": route_intent,
        "tool_calls": enriched_tool_calls,
        "tool_results": self._merge_tool_results([], tool_results),
        "graph_context": graph_context,
        "graph_memory": graph_context,
    }


def _patched_build_reflector_user_prompt(
    self: LegalGraphAgent,
    state: AgentState,
    chat_history: str,
    context_blocks: str,
) -> str:
    mode_enabled = self._proactive_graph_mode_enabled(state)
    mode_label = "ON" if mode_enabled else "OFF"
    case_summary = (state.get("case_summary") or "").strip() or "无案情摘要"
    mode_instruction = (
        "主动案情梳理模式已开启。除了法律要件是否缺失，你还必须检查案情摘要和最新输入里是否存在指代不明的实体、代词或模糊称谓。"
        "如果存在，把最关键的 1 到 2 个实体澄清要求写入 missing_info。"
    )
    if not mode_enabled:
        mode_instruction = (
            "主动案情梳理模式已关闭。你只关注法律要件是否缺失，忽略“他”“老板”“对方”“那辆车”这类实体指代歧义。"
        )

    return (
        f"用户问题：{state['question']}\n\n"
        f"主动案情梳理模式：{mode_label}\n"
        f"{mode_instruction}\n\n"
        f"当前卷宗净白摘要：\n{case_summary}\n\n"
        "请注意：摘要中已经明确的事实，一律视为已知证据，严禁重复写入 missing_info。\n\n"
        f"当前检索查询：{state.get('current_query', state['question'])}\n"
        f"当前循环次数：{state.get('loop_count', 0)}\n\n"
        f"当前检索结果：\n{context_blocks or '无有效检索结果'}"
    )


async def _patched_ask(
    self: LegalGraphAgent,
    user_id: int,
    session_id: str,
    question: str,
    chat_history: str = "",
    case_summary: str = "",
    graph_memory: str = "",
    is_precise_mode: bool = False,
    proactive_graph_mode: bool = True,
    conversation_id: int | None = None,
) -> dict[str, Any]:
    initial_state: AgentState = {
        "user_id": user_id,
        "conversation_id": int(conversation_id) if conversation_id is not None else 0,
        "session_id": session_id,
        "question": question,
        "is_precise_mode": bool(is_precise_mode),
        "proactive_graph_mode": bool(proactive_graph_mode),
        "chat_history": chat_history,
        "case_summary": case_summary,
        "graph_context": graph_memory,
        "graph_memory": graph_memory,
        "current_query": question,
        "route_intent": "GENERAL_CONSULT",
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
        "route_intent": str(final_state.get("route_intent") or "GENERAL_CONSULT").upper(),
        "case_summary": final_state.get("case_summary") or case_summary or "",
    }


def _normalize_summary_text(text: str) -> str:
    return " ".join((text or "").strip().split())


def _extract_numeric_amount(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:元|块|人民币|月薪|工资)", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _extract_month_span(text: str) -> int | None:
    worked_match = re.search(r"(?:工作|任职|入职后|用工)\D{0,6}(\d+)\s*个?月", text)
    if worked_match:
        try:
            return max(int(worked_match.group(1)), 0)
        except ValueError:
            return None

    month_match = re.search(r"(\d+)\s*个月", text)
    if month_match:
        try:
            return max(int(month_match.group(1)), 0)
        except ValueError:
            return None

    range_match = re.search(
        r"(\d{4})[.\-/年](\d{1,2})[.\-/月]?(?:\d{1,2}日?)?.{0,12}?"
        r"(?:到|至|-|~)"
        r".{0,12}?"
        r"(\d{4})[.\-/年](\d{1,2})[.\-/月]?(?:\d{1,2}日?)?",
        text,
    )
    if not range_match:
        return None

    start_year, start_month, end_year, end_month = map(int, range_match.groups())
    months = (end_year - start_year) * 12 + (end_month - start_month)
    return months + 1 if months >= 0 else None


def _build_case_fact_lines(case_summary: str, question: str, graph_context: str) -> list[str]:
    facts: list[str] = []
    for raw in (case_summary, question, graph_context):
        clean = _normalize_summary_text(raw)
        if not clean:
            continue
        for part in re.split(r"[；;。！？\n]+", clean):
            item = part.strip(" ，,")
            if not item or item in facts:
                continue
            facts.append(item)
    return facts[:8]


def _build_practical_suggestions(case_summary: str, question: str) -> list[str]:
    merged = f"{case_summary} {question}"
    suggestions: list[str] = []
    if any(token in merged for token in ("未签合同", "没签合同", "未签劳动合同", "没有劳动合同")):
        suggestions.append("立即固定入职时间、工资标准、考勤记录、工资流水、聊天记录，作为劳动关系和未签书面合同事实的核心证据。")
        suggestions.append("如担心对方转移财产、注销公司或抽逃资产，可评估申请财产保全。")
    if any(token in merged for token in ("拖欠工资", "欠薪", "未发工资", "克扣工资")):
        suggestions.append("同步整理工资条、银行流水、考勤和催要记录，优先准备劳动仲裁材料。")
    if any(token in merged for token in ("辞退", "开除", "解除劳动关系", "逼离职", "被迫离职")):
        suggestions.append("保留解除通知、录音、绩效考核和岗位调整记录，用于判断是否构成违法解除或被迫解除。")
    if not suggestions:
        suggestions.append("先把关键时间、金额、主体身份和证据材料整理成时间轴，再推进具体请求。")
    return suggestions[:3]


def _build_rule_analysis(case_summary: str, question: str) -> tuple[list[str], list[str]]:
    merged = f"{case_summary} {question}"
    rules: list[str] = []
    unknowns: list[str] = []

    if any(token in merged for token in ("未签合同", "没签合同", "未签劳动合同", "没有劳动合同")):
        rules.append("若已实际用工而单位在法定期限内未签书面劳动合同，通常可主张未签书面劳动合同的二倍工资差额。")
        salary = _extract_numeric_amount(merged)
        months = _extract_month_span(merged)
        if salary is not None and months is not None:
            compensable_months = max(min(months - 1, 11), 0)
            rules.append(
                f"按现有事实估算，若月工资约为 {salary:.0f} 元、未签约期间约 {months} 个月，"
                f"二倍工资差额可先按 {compensable_months} 个月工资、约 {salary * compensable_months:.0f} 元测算。"
            )
        else:
            missing_parts = []
            if salary is None:
                missing_parts.append("月工资")
            if months is None:
                missing_parts.append("未签合同持续时间")
            if missing_parts:
                unknowns.append(f"二倍工资差额的精确金额仍取决于{'、'.join(missing_parts)}。")

    if any(token in merged for token in ("劳动关系", "入职", "考勤", "发工资", "工资流水")):
        rules.append("即便没有书面劳动合同，只要存在管理、考勤、报酬支付等事实，仍可优先从劳动关系成立角度主张权利。")

    if any(token in merged for token in ("公司变更", "变更主体", "更名", "注销", "股东变更")):
        rules.append("若用工主体发生更名、分立、并购或资产转移，需要同步核查实际用工主体、工资支付主体和责任承继链条。")

    return rules[:4], unknowns[:3]


def _build_advisor_fallback(case_summary: str, question: str, graph_context: str) -> str:
    facts = _build_case_fact_lines(case_summary, question, graph_context)
    rules, unknowns = _build_rule_analysis(case_summary, question)
    suggestions = _build_practical_suggestions(case_summary, question)

    sections: list[str] = []
    if facts:
        sections.append("已知事实：\n- " + "\n- ".join(facts))
    if rules:
        sections.append("法律分析：\n- " + "\n- ".join(rules))
    else:
        sections.append("法律分析：\n- 现有案情已经足以先做基础判断，应围绕劳动关系、合同义务或赔偿责任继续推进。")
    if unknowns:
        sections.append("不确定事项：\n- " + "\n- ".join(unknowns))
    sections.append("实操建议：\n- " + "\n- ".join(suggestions))
    return "\n\n".join(sections)


def _safe_extract_amount_v3(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:元|块|人民币|月薪|工资)", text)
    if not match:
        match = re.search(r"(?:月薪|工资|底薪|薪资|薪水)\s*(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _safe_extract_months_v3(text: str) -> int | None:
    relative_span = re.search(
        r"去年\s*(\d{1,2})\s*月.*?今年\s*(\d{1,2})\s*月(?:底|末)?",
        text,
    )
    if relative_span:
        try:
            start_month = int(relative_span.group(1))
            end_month = int(relative_span.group(2))
            if 1 <= start_month <= 12 and 1 <= end_month <= 12:
                months = (12 - start_month + 1) + end_month
                return months if months > 0 else None
        except ValueError:
            return None

    worked_match = re.search(r"(?:工作|任职|入职后|用工)\D{0,6}(\d+)\s*个月", text)
    if worked_match:
        try:
            return max(int(worked_match.group(1)), 0)
        except ValueError:
            return None

    month_match = re.search(r"(\d+)\s*个月", text)
    if month_match:
        try:
            return max(int(month_match.group(1)), 0)
        except ValueError:
            return None
    return None


def _safe_extract_owed_wages(text: str, monthly_salary: float | None = None) -> float:
    """提取拖欠工资金额。支持 '拖欠N月工资' 或直接金额 '欠薪XXXX元'。"""
    direct = re.search(r"(?:欠薪|拖欠工资|欠发工资)\s*(\d+(?:\.\d+)?)\s*(?:元|块|万)?", text)
    if direct:
        val = float(direct.group(1))
        if "万" in text[direct.start():direct.end() + 2]:
            val *= 10000
        return val

    month_match = re.search(r"(?:拖欠|欠)\s*(\d+|[一二三四五六七八九十两]+)\s*(?:个?月|个月的?)\s*(?:工资|薪)", text)
    if not month_match:
        month_match = re.search(r"(?:拖欠|欠)\s*([一二三四五六七八九十两]+|[0-9]+)\s*月\s*(?:工资|薪)?", text)
    if month_match and monthly_salary:
        cn_map = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
                  "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        raw = month_match.group(1)
        n = cn_map.get(raw, None)
        if n is None:
            try:
                n = int(raw)
            except ValueError:
                return 0.0
        return monthly_salary * n
    return 0.0


def _safe_extract_deposit(text: str) -> float:
    """提取押金金额。支持 'N万押金' 'N元押金' '押金N万' 等。"""
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:万|万元)\s*(?:押金|保证金)", text)
    if match:
        return float(match.group(1)) * 10000
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:元|块)\s*(?:押金|保证金)", text)
    if match:
        return float(match.group(1))
    match = re.search(r"(?:押金|保证金)\s*(\d+(?:\.\d+)?)\s*(?:万|万元)", text)
    if match:
        return float(match.group(1)) * 10000
    match = re.search(r"(?:押金|保证金)\s*(\d+(?:\.\d+)?)\s*(?:元|块)?", text)
    if match:
        return float(match.group(1))
    return 0.0


def _build_layered_advisor_payload_v3(case_summary: str, question: str, graph_context: str) -> dict[str, Any]:
    merged = " ".join(part for part in [case_summary, question, graph_context] if part).strip()
    facts = _build_case_fact_lines(case_summary, question, graph_context)
    amount = _safe_extract_amount_v3(merged)
    months = _safe_extract_months_v3(merged)

    calculation_lines: list[str] = []
    strategy_lines: list[str] = []
    action_lines: list[str] = []
    unknowns: list[str] = []

    has_unsigned_contract = any(token in merged for token in ("未签合同", "没签合同", "没签劳动合同", "未签劳动合同", "没有劳动合同"))
    double_salary_total = 0.0
    if has_unsigned_contract:
        calculation_lines.append("只要已经形成实际用工关系，即使没有书面劳动合同，也可以先主张未签书面劳动合同的二倍工资差额。")
        if amount is not None and months is not None:
            compensable_months = max(min(months - 1, 11), 0)
            double_salary_total = amount * compensable_months
            calculation_lines.append(
                f"按现有信息，计算公式可以先写为：月工资 {amount:.0f} 元 × 可主张月数 {compensable_months} = 二倍工资差额约 {double_salary_total:.0f} 元。"
            )
        else:
            if amount is None:
                unknowns.append("月工资标准")
            if months is None:
                unknowns.append("未签合同持续月数")

    owed_wages = _safe_extract_owed_wages(merged, amount)
    deposit = _safe_extract_deposit(merged)
    has_owed = any(token in merged for token in ("拖欠", "欠薪", "未发工资", "克扣工资", "欠发"))
    has_deposit = any(token in merged for token in ("押金", "保证金"))

    if owed_wages > 0:
        calculation_lines.append(f"拖欠工资部分：约 {owed_wages:.0f} 元。")
    elif has_owed and amount is not None:
        calculation_lines.append("存在工资拖欠事实，待确认具体欠薪月数后可精确计算。")

    if deposit > 0:
        calculation_lines.append(f"押金/保证金部分：{deposit:.0f} 元，用人单位收取押金本身违反《劳动合同法》第九条，可依法要求全额退还。")
    elif has_deposit:
        calculation_lines.append("存在押金争议，用人单位向劳动者收取押金违反《劳动合同法》第九条，可要求退还。")

    claim_total = double_salary_total + owed_wages + deposit
    if claim_total > 0:
        parts = []
        if double_salary_total > 0:
            parts.append(f"二倍工资差额 {double_salary_total:.0f} 元")
        if owed_wages > 0:
            parts.append(f"拖欠工资 {owed_wages:.0f} 元")
        if deposit > 0:
            parts.append(f"押金退还 {deposit:.0f} 元")
        calculation_lines.append(
            f"以上各项合计可主张总额约 {claim_total:.0f} 元（{' + '.join(parts)}）。"
        )

    if any(token in merged for token in ("劳动关系", "入职", "考勤", "工资流水", "发工资")):
        calculation_lines.append("现有事实已经足以先从劳动关系成立、工资支付事实和未签约责任三个层面同时主张权利。")

    if any(token in merged for token in ("查封", "保全", "资产", "新公司", "人格混同", "关联公司", "主体变更", "更名", "注销", "换皮", "关门", "搬走", "换了个名")):
        strategy_lines.append("如果目标是查封新公司或关联主体，现阶段不要先卡在“证据够不够”这个问题上，而是先按人格混同、责任承继、实际用工主体三个方向布置材料。")
        strategy_lines.append("下一步应优先准备：工商档案、股东和法定代表人信息、工资发放账户、社保公积金缴纳主体、办公地点、业务连续性、聊天记录和财务往来材料。")

    if not strategy_lines:
        strategy_lines.append("高风险诉求可以先拆成两步走：先固定债权和责任主体，再根据对方资产和主体结构决定是否申请保全、追加被申请人或单独起诉。")

    if has_unsigned_contract:
        action_lines.append("先把入职时间、工资标准、考勤记录、工资流水、聊天记录整理成时间轴，这些足以支撑第一轮仲裁请求。")
    if any(token in merged for token in ("拖欠工资", "欠薪", "未发工资", "克扣工资", "拖欠")):
        action_lines.append("欠薪部分建议同步准备工资条、银行流水、催要记录和考勤，便于把工资、经济补偿或赔偿一并主张。")
    if any(token in merged for token in ("辞退", "开除", "解除劳动关系", "逼离职", "被迫离职")):
        action_lines.append("如果还涉及辞退或被迫离职，解除通知、谈话录音、岗位调整记录和绩效材料要单独归档。")
    if any(token in merged for token in ("新公司", "注销", "关联公司", "查封", "保全", "换皮", "关门", "搬走")):
        action_lines.append("如果担心对方转移资产，可在仲裁或起诉前同步梳理保全财产线索，而不是等全部证据齐全后再行动。")
    if not action_lines:
        action_lines.append("先把可用证据按“身份关系、金额、时间、对方主体”四类归档，后续维权路径就能快速落地。")

    needs_core_followup = False
    if has_unsigned_contract and (amount is None or months is None):
        needs_core_followup = True
    elif not (case_summary or "").strip():
        needs_core_followup = True

    if unknowns:
        sandwich_followup = (
            "你现在已经提供了有法律价值的关键事实，这些信息足以先帮你判断维权方向。"
            "按目前情况看，权利主张是有空间的，不需要因为材料还没凑齐就停在原地。"
            f"为了把金额和请求做得更准，我还需要你补 1 到 2 个核心信息：{'、'.join(unknowns[:2])}。"
        )
    else:
        sandwich_followup = (
            "你现有的信息已经能支持初步维权判断了。"
            "按现在的案情，主张路径已经比较清楚。"
            "如果你愿意，我接下来更建议直接帮你整理仲裁请求清单、证据目录，或者起草一份劳动仲裁申请书草稿。"
        )

    constructive_next_step = (
        "目前账目和维权方向已经基本清楚。接下来你可以选择先去劳动监察投诉，或者直接申请劳动仲裁。"
        "如果你愿意，我下一步可以直接帮你起草《劳动仲裁申请书》草稿，或者把证据目录整理成可提交版本。"
    )

    answer_parts: list[str] = []
    if facts:
        answer_parts.append("已知事实：\n- " + "\n- ".join(facts))
    if calculation_lines:
        answer_parts.append("算账与确权层：\n- " + "\n- ".join(calculation_lines))
    else:
        answer_parts.append("算账与确权层：\n- 现有信息虽然还不完整，但已经足以先明确可主张的权利方向，不需要整体停住。")
    answer_parts.append("高风险执行层：\n- " + "\n- ".join(strategy_lines))
    answer_parts.append("下一步落地动作：\n- " + "\n- ".join(action_lines))
    if needs_core_followup:
        answer_parts.append("柔性追问：\n- " + sandwich_followup)
    else:
        answer_parts.append("建设性推进：\n- " + constructive_next_step)

    return {
        "answer": "\n\n".join(answer_parts),
        "needs_core_followup": needs_core_followup,
        "followup": sandwich_followup,
        "constructive_next_step": constructive_next_step,
        "unknowns": unknowns[:2],
    }


async def _patched_generate_reflection_v3(self: LegalGraphAgent, state: AgentState) -> dict[str, Any]:
    payload = _build_layered_advisor_payload_v3(
        (state.get("case_summary") or "").strip(),
        state["question"],
        (state.get("graph_context") or "").strip() or (state.get("graph_memory") or "").strip(),
    )
    if payload["needs_core_followup"]:
        return {
            "reflection_status": "NEED_USER_INFO",
            "confidence_score": 0.62,
            "reflection_analysis": "可以先回答，但若要把金额或请求精确落表，还需要补充极少量核心字段。",
            "missing_info": payload["unknowns"],
            "clarification_questions": [payload["followup"]],
            "graph_focus": (state.get("case_summary") or "").strip() or state.get("current_query", state["question"]),
        }

    return {
        "reflection_status": "SUFFICIENT",
        "confidence_score": 0.84,
        "reflection_analysis": "核心条件已经足够，应直接输出分层答案，并进入执行导向的下一步建议。",
        "missing_info": [],
        "clarification_questions": [payload["constructive_next_step"]],
        "graph_focus": (state.get("case_summary") or "").strip() or state.get("current_query", state["question"]),
    }


async def _patched_generate_clarifier_question_v3(self: LegalGraphAgent, state: AgentState) -> str:
    payload = _build_layered_advisor_payload_v3(
        (state.get("case_summary") or "").strip(),
        state["question"],
        (state.get("graph_context") or "").strip() or (state.get("graph_memory") or "").strip(),
    )
    if payload["needs_core_followup"]:
        return payload["followup"]
    return payload["constructive_next_step"]


async def _patched_generate_final_answer_v3(self: LegalGraphAgent, state: AgentState) -> dict[str, Any]:
    tool_results = state.get("tool_results", [])
    candidate_sources = self._extract_candidate_sources(tool_results)
    retrieved_docs = self._build_context_blocks(tool_results)
    case_summary = (state.get("case_summary") or "").strip()
    graph_context = ((state.get("graph_context") or "").strip() or (state.get("graph_memory") or "").strip())
    layered_payload = _build_layered_advisor_payload_v3(case_summary, state["question"], graph_context)
    rag_context = retrieved_docs or "无有效检索结果"
    fallback = {
        "answer": layered_payload["answer"],
        "sources": [],
    }
    advisor_system_prompt = f"""
你是高情商、高落地性的中国大陆专业法律 Agent。

<global_rules>
1. 绝对禁止全局拒答。无论条件多匮乏，都必须先尽最大努力回答，再决定是否继续推进。
2. `case_summary` 是最高事实基准。只要摘要里已经有工资、时长、未签合同、主体变更等基础数据，就必须先算账、确权或输出策略。
3. 严禁因为缺少高阶证据就停止基础计算、维权分析或执行建议。
</global_rules>

<layered_reasoning>
1. 先做 `算账与确权层`：能算就直接给公式、口径、预估金额。
2. 再做 `高风险执行层`：对于查封资产、跨主体追责、人格混同等诉求，直接给策略和材料清单，不要先索要证据才回答。
3. 最后做 `推进层`：
   - 若核心字段真不够，才进入柔性追问，而且必须使用三明治沟通法。
   - 若基础账目和维权路径已清楚，就停止问事实，改成建设性提问，推动进入投诉、仲裁、起诉或文书起草阶段。
</layered_reasoning>

<adaptive_questioning>
场景A：必须追问时，必须按三段式输出：
1. 肯定并赋能：说明用户现有信息为什么有价值。
2. 阶段性结论：先给利好或初步判断。
3. 柔性目标追问：只追问 1 到 2 个最关键字段，并说明这是为了帮他把金额或请求做精确。

场景B：核心条件已充足时，不再索要事实，改问是否需要我继续起草仲裁申请书、证据目录、投诉话术或请求清单。
</adaptive_questioning>

<style_rules>
1. 输出必须温暖、专业、具体，不能像冷冰冰地查户口。
2. 不允许出现“依据不足所以无法分析”“请先把证据补齐再说”这类退缩表达。
3. 允许说明哪些部分已经能主张、哪些部分后续要补强，但不能因此停止回答。
</style_rules>

<rag_binding priority="MAX">
1. 下面的 `<rag_context>` 是你进行法律定性、金额计算、法条援引、案例说明时唯一允许直接锚定的外部依据。
2. 只要 `<rag_context>` 中出现了具体法条名称、案例名称或来源文件名，你在相关结论后必须显式穿插这些名称。
3. 你不得脱离 `<rag_context>` 自行补写新的法条标题、案例名称、裁判文号或来源文件名。
4. 如果 `<rag_context>` 没有给出足够的法条名称，你仍然可以先按案情做事实分层回答，但不能伪造法律出处。
</rag_binding>

<context>
案件摘要：
{case_summary or "无案件摘要"}

图谱上下文：
{graph_context or "无图谱上下文"}

<rag_context>
{rag_context}
</rag_context>
</context>

<output_contract>
1. 只返回 JSON。
2. 至少包含 `answer` 和 `sources`。
3. `answer` 必须覆盖：算账与确权层、高风险执行层、下一步推进。
4. `sources` 只能从候选来源中选择，不能编造。
5. 若 `<rag_context>` 中存在法条名、案例名或来源文件名，`answer` 中对应结论后必须显式写出这些名称。
</output_contract>
""".strip()

    content = await self._chat_model(
        self.expert_llm,
        [
            {"role": "system", "content": advisor_system_prompt},
            {
                "role": "user",
                "content": (
                    f"用户问题：{state['question']}\n\n"
                    f"候选来源：{json.dumps(candidate_sources, ensure_ascii=False)}\n\n"
                    f"<rag_context>\n{rag_context}\n</rag_context>\n\n"
                    f"以下是已经完成的基础回答骨架，你只能优化它，不能删掉其中的算账、策略或下一步建议：\n{layered_payload['answer']}"
                ),
            },
        ],
    )
    parsed = self._safe_json_load(content, fallback)

    answer = parsed.get("answer") or parsed.get("response") or fallback["answer"]
    if not isinstance(answer, str) or not answer.strip():
        answer = fallback["answer"]
    if ("未找到依据" in answer) or ("依据不足" in answer):
        answer = fallback["answer"]

    sources = self._normalize_sources(parsed.get("sources", []), candidate_sources)
    return {
        "response": answer.strip(),
        "sources": sources,
        "is_final": True,
    }


LegalGraphAgent._route_decision = _patched_route_decision
LegalGraphAgent._patched_route_decision = _patched_route_decision
LegalGraphAgent._patched_build_tool_calls_for_intent = _patched_build_tool_calls_for_intent
LegalGraphAgent._patched_is_case_update = _patched_is_case_update
LegalGraphAgent._patched_is_general_consult = _patched_is_general_consult
LegalGraphAgent._patched_build_reflector_user_prompt = _patched_build_reflector_user_prompt
LegalGraphAgent.tool_router_node = _patched_tool_router_node
LegalGraphAgent.fact_updater_node = _patched_fact_updater_node
LegalGraphAgent.vector_researcher_node = _patched_vector_researcher_node
LegalGraphAgent._generate_final_answer = _patched_generate_final_answer_v3
LegalGraphAgent._generate_reflection = _patched_generate_reflection_v3
LegalGraphAgent._generate_clarifier_question = _patched_generate_clarifier_question_v3
LegalGraphAgent.ask = _patched_ask

legal_agent = LegalGraphAgent()
