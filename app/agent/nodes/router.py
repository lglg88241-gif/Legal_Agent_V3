"""
Four-track router: GREETING / GENERAL_CONSULT / CASE_UPDATE / OUT_OF_SCOPE.

Contains all intent detection, heuristic routing, and LLM-based
routing logic. Pure-Python functions are deterministic; the LLM
call is only used as a refinement on top of heuristic fallbacks.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.agent.graph import AgentState
from app.agent.nodes._llm import (
    CHITCHAT_INTENT,
    GRAPH_TOOL,
    LAW_TOOL,
    CASE_TOOL,
    TOOLS_INTENT,
)
from app.agent.prompts.advisor import TRIPLE_TRACK_ROUTER_PROMPT_V2

logger = logging.getLogger(__name__)

OUT_OF_SCOPE_INTENT = "OUT_OF_SCOPE"

_SCOPE_GUARD_WINDOW = 4
_FOREIGN_JURISDICTION_KEYWORDS = (
    "美国",
    "英国",
    "日本",
    "韩国",
    "德国",
    "法国",
    "欧盟",
    "欧洲",
    "澳大利亚",
    "加拿大",
    "新加坡",
    "加州",
    "纽约",
    "美国联邦",
    "联邦法院",
    "联邦法",
)
_FOREIGN_LAW_KEYWORDS = (
    "法律",
    "法规",
    "劳动法",
    "劳动合同法",
    "法院",
    "法案",
    "判例",
    "判决",
    "司法解释",
    "条例",
    "仲裁",
    "法条",
    "交通法规",
)
_NON_LEGAL_PATTERNS = (
    "帮我写代码",
    "写一段代码",
    "写个脚本",
    "python代码",
    "java代码",
    "javascript代码",
    "帮我写python",
    "帮我写java",
    "编程",
    "明天股票会涨吗",
    "股票会涨吗",
    "推荐股票",
    "基金会涨吗",
    "推荐基金",
    "帮我翻译",
    "翻译一段",
    "翻译成英文",
    "翻译成中文",
    "今天天气",
    "明天天气",
    "天气怎么样",
    "会下雨吗",
    "帮我写作文",
    "帮我做作业",
    "菜谱",
    "减肥计划",
)


def _normalize_question(text: str) -> str:
    return "".join((text or "").lower().strip().split())


def _iter_keyword_positions(text: str, keyword: str) -> list[tuple[int, int]]:
    positions: list[tuple[int, int]] = []
    start = 0
    while True:
        index = text.find(keyword, start)
        if index < 0:
            return positions
        positions.append((index, index + len(keyword)))
        start = index + len(keyword)


def _keywords_are_nearby(
    text: str,
    anchors: tuple[str, ...],
    targets: tuple[str, ...],
    window: int = _SCOPE_GUARD_WINDOW,
) -> bool:
    for anchor in anchors:
        for anchor_start, anchor_end in _iter_keyword_positions(text, anchor):
            for target in targets:
                for target_start, target_end in _iter_keyword_positions(text, target):
                    gap = max(target_start - anchor_end, anchor_start - target_end, 0)
                    if gap <= window:
                        return True
    return False


def looks_like_foreign_law_query(question: str) -> bool:
    cleaned = _normalize_question(question)
    if not cleaned:
        return False
    if not any(keyword in cleaned for keyword in _FOREIGN_JURISDICTION_KEYWORDS):
        return False
    return _keywords_are_nearby(
        cleaned,
        _FOREIGN_JURISDICTION_KEYWORDS,
        _FOREIGN_LAW_KEYWORDS,
    )


def looks_like_non_legal_request(question: str) -> bool:
    cleaned = _normalize_question(question)
    if not cleaned:
        return False
    return any(pattern in cleaned for pattern in _NON_LEGAL_PATTERNS)


def detect_out_of_scope_reason(question: str) -> str | None:
    if looks_like_foreign_law_query(question):
        return "FOREIGN_LAW"
    if looks_like_non_legal_request(question):
        return "NON_LEGAL"
    return None


# ======================================================================
# Pure-Python intent detectors (no LLM, no IO)
# ======================================================================

def has_substantive_legal_intent(self, question: str) -> bool:
    keywords = [
        "法条", "法律", "法规", "赔偿", "赔付", "量刑", "判几年",
        "工伤", "劳动仲裁", "合同", "纠纷", "起诉", "犯罪", "构成",
        "认定", "案例", "判决", "案由", "被告", "原告", "公司",
        "辞退", "离职", "工资", "补偿", "责任",
    ]
    return any(keyword in question for keyword in keywords)


def history_indicates_legal_dialogue(self, chat_history: str) -> bool:
    history = (chat_history or "").strip()
    if not history:
        return False
    keywords = [
        "法条", "法律", "赔偿", "工伤", "劳动", "合同", "纠纷",
        "仲裁", "诉讼", "责任", "案由", "案例", "判决", "赔多少",
        "算不算", "是否", "认定", "解除", "公司", "离职", "工资", "补偿",
    ]
    return any(keyword in history for keyword in keywords)


def is_contextual_followup(self, question: str, chat_history: str) -> bool:
    cleaned = "".join(question.lower().strip().split())
    if not cleaned or not self._history_indicates_legal_dialogue(chat_history):
        return False
    short_replies = {
        "是", "对", "对的", "嗯", "嗯嗯", "有", "没有", "不是",
        "是的", "有的", "没有的", "对啊", "是啊", "好", "好的", "行",
    }
    return cleaned in short_replies or len(cleaned) <= 4


def looks_like_chitchat(self, question: str, chat_history: str = "") -> bool:
    cleaned = "".join(question.lower().strip().split())
    if not cleaned:
        return True
    if self._is_contextual_followup(question, chat_history):
        return False
    if self._has_substantive_legal_intent(question):
        return False
    greetings = {
        "你好", "您好", "hello", "hi", "hey", "在吗", "在不在",
        "有人吗", "忙吗", "早上好", "中午好", "下午好", "晚上好",
        "嗨", "哈喽", "hello?", "hi?",
    }
    punctuation = {"?", "？", "!", "！", ".", "。", "~"}
    if cleaned in greetings:
        return True
    return len(cleaned) <= 6 and all(char in punctuation for char in cleaned)


def detect_intents(self, question: str, chat_history: str = "") -> dict[str, bool]:
    lowered = question.lower()
    law_keywords = [
        "法条", "法律", "法规", "量刑", "依据", "民法典", "刑法",
        "劳动合同法", "赔偿", "算不算", "怎么判", "如何认定", "是否构成", "工伤",
    ]
    case_keywords = [
        "案例", "类案", "判决", "案情", "指导案例", "裁判",
        "有没有案例", "实际案例", "相似案例",
    ]
    graph_keywords = [
        "案由", "纠纷", "罪名", "原告", "被告", "公司",
        "劳动争议", "逃税", "合同纠纷", "entity", "case",
    ]
    has_law_intent = any(keyword in question for keyword in law_keywords)
    has_case_intent = any(keyword in question for keyword in case_keywords)
    has_graph_intent = any(keyword in question for keyword in graph_keywords) or "case" in lowered
    is_chitchat = self._looks_like_chitchat(question, chat_history=chat_history)
    is_out_of_scope = detect_out_of_scope_reason(question) is not None
    return {
        "has_law_intent": has_law_intent,
        "has_case_intent": has_case_intent,
        "has_graph_intent": has_graph_intent,
        "is_composite": has_law_intent and has_case_intent,
        "is_chitchat": is_chitchat,
        "is_out_of_scope": is_out_of_scope,
    }


def fallback_route_decision(self, question: str, chat_history: str = "") -> dict[str, Any]:
    intents = self._detect_intents(question, chat_history=chat_history)
    if intents["is_out_of_scope"]:
        return {"route_intent": OUT_OF_SCOPE_INTENT, "tool_calls": []}
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


def dedupe_tool_calls(self, tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
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


def enforce_composite_routes(self, question: str, tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
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


# ======================================================================
# V3 heuristic helpers (module-level, no self)
# ======================================================================

def is_case_update(self, question: str, chat_history: str = "", case_summary: str = "") -> bool:
    text = " ".join((question or "").strip().split())
    if not text:
        return False
    update_tokens = [
        "是", "不是", "没有", "有", "对", "不对", "补充", "更正",
        "改成", "记错", "纠正", "其实", "实际上", "后来",
        "老板", "对方", "那辆车", "那个公司", "他", "她",
    ]
    if any(token in text for token in update_tokens):
        return bool((chat_history or "").strip() or (case_summary or "").strip()) or len(text) <= 12
    return self._is_contextual_followup(text, chat_history) and bool((case_summary or "").strip() or (chat_history or "").strip())


def is_general_consult(self, question: str, case_summary: str = "") -> bool:
    text = " ".join((question or "").strip().split())
    if not text:
        return False
    if self._has_substantive_legal_intent(text) and not self._patched_is_case_update(text, case_summary=case_summary):
        return True
    consult_tokens = [
        "怎么判", "赔多少", "量刑", "法条", "法律依据",
        "算不算", "是否构成", "认定", "标准", "流程", "程序",
    ]
    return any(token in text for token in consult_tokens)


def question_looks_like_brief_followup(question: str) -> bool:
    text = " ".join((question or "").strip().split())
    if not text:
        return False
    brief_followup_tokens = [
        "怎么办", "怎么维权", "怎么处理", "怎么赔", "赔多少",
        "怎么告", "怎么仲裁", "接下来呢", "然后呢",
    ]
    if any(token in text for token in brief_followup_tokens):
        return True
    condensed = "".join(text.split())
    return len(condensed) <= 8 and any(token in condensed for token in ["怎么办", "怎么赔", "怎么告", "维权"])


def history_has_case_continuity(chat_history: str, case_summary: str) -> bool:
    if (case_summary or "").strip():
        return True
    history = " ".join((chat_history or "").strip().split())
    if not history:
        return False
    case_markers = [
        "公司", "老板", "工资", "赔偿", "劳动合同", "仲裁",
        "原告", "被告", "法院", "工伤", "交通事故", "入职",
        "离职", "解除劳动关系", "金额", "对方",
    ]
    matched = sum(1 for marker in case_markers if marker in history)
    return matched >= 2


def build_tool_calls_for_intent(self, route_intent: str, query: str) -> list[dict[str, Any]]:
    cleaned_query = " ".join((query or "").strip().split())
    if not cleaned_query or route_intent in {"GREETING", OUT_OF_SCOPE_INTENT}:
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


# ======================================================================
# LLM-enhanced four-track router
# ======================================================================

async def route_decision(
    self,
    question: str,
    chat_history: str = "",
    case_summary: str = "",
    is_precise_mode: bool = False,
) -> dict[str, Any]:
    history_text = (chat_history or "").strip() or "无历史对话"
    has_case_summary = bool((case_summary or "").strip())
    summary_text = (case_summary or "").strip() or "无案情摘要"
    cleaned_question = " ".join((question or "").strip().split())

    if detect_out_of_scope_reason(cleaned_question):
        return {"route_intent": OUT_OF_SCOPE_INTENT, "tool_calls": []}

    if is_precise_mode:
        retrieval_query = summary_text if has_case_summary else cleaned_question
        return {
            "route_intent": "CASE_UPDATE",
            "tool_calls": self._patched_build_tool_calls_for_intent("CASE_UPDATE", retrieval_query),
        }

    if history_has_case_continuity(chat_history, case_summary) and (
        self._patched_is_case_update(cleaned_question, chat_history=chat_history, case_summary=case_summary)
        or question_looks_like_brief_followup(cleaned_question)
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
        cleaned_question, chat_history=chat_history, case_summary=case_summary,
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
        if route_intent not in {"GREETING", "GENERAL_CONSULT", "CASE_UPDATE", OUT_OF_SCOPE_INTENT}:
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


# ======================================================================
# Graph node
# ======================================================================

async def tool_router_node(self, state: AgentState) -> AgentState:
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
