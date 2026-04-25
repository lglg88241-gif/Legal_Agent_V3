"""
Four-track router: GREETING / GENERAL_CONSULT / CASE_UPDATE / OUT_OF_SCOPE.

Contains all intent detection, heuristic routing, and LLM-based
routing logic. Pure-Python functions are deterministic; the LLM
call is only used as a refinement on top of heuristic fallbacks.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.agent.graph import AgentState
from app.agent.nodes.fact_updater import _extract_monthly_wage
from app.agent.nodes._llm import (
    CHITCHAT_INTENT,
    GRAPH_TOOL,
    LAW_TOOL,
    CASE_TOOL,
    TOOLS_INTENT,
)
from app.agent.prompts.advisor import TRIPLE_TRACK_ROUTER_PROMPT_V2
from app.core.llm_provider import get_llm_router

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
    "离婚法",
    "婚姻法",
    "民法",
    "刑法",
    "税法",
    "移民法",
    "诉讼法",
    "证据法",
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
    "改代码",
    "写程序",
    "写函数",
    "调试代码",
    "debug",
    "编程",
    "明天股票会涨吗",
    "股票会涨吗",
    "股票走势",
    "推荐股票",
    "推荐一只股票",
    "基金会涨吗",
    "推荐基金",
    "基金怎么买",
    "帮我翻译",
    "翻译一段",
    "翻译成英文",
    "翻译成中文",
    "翻成英文",
    "翻成中文",
    "今天天气",
    "明天天气",
    "天气怎么样",
    "天气预报",
    "气温多少",
    "会下雨吗",
    "帮我写作文",
    "帮我做作业",
    "写作业",
    "菜谱",
    "怎么做饭",
    "减肥计划",
    "怎么减肥",
)
_CHINESE_LEGAL_KEYWORDS = {
    "劳动合同", "劳动仲裁", "劳动争议", "工资", "加班费", "辞退", "解雇",
    "赔偿", "补偿金", "工伤", "社保", "公积金", "仲裁", "诉讼",
    "合同纠纷", "离婚", "抚养", "继承", "遗产", "房产", "租赁",
    "借款", "欠款", "担保", "侵权", "交通事故", "刑事", "取保候审",
    "拘留", "逮捕", "起诉", "上诉", "法院", "检察院", "律师",
    "民法典", "刑法", "劳动法", "婚姻法", "公司法",
}


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


def _contains_chinese_legal_keyword(text: str) -> bool:
    """检查文本是否包含明显的中国法律关键词"""
    return any(kw in text for kw in _CHINESE_LEGAL_KEYWORDS)


_PURE_LAW_REFERENCE_PATTERNS = (
    re.compile(r"\u7b2c\s*[0-9\u4e00-\u9fff]+\s*\u6761"),
    re.compile(r"[\u4e00-\u9fff]{2,20}\u6cd5"),
)
_PURE_LAW_REFERENCE_TERMS = (
    "\u6cd5\u5f8b",
    "\u6cd5\u6761",
    "\u6761\u6587",
    "\u89c4\u5b9a",
    "\u6cd5\u89c4",
    "\u6761\u6b3e",
    "\u5185\u5bb9",
    "\u662f\u4ec0\u4e48",
    "\u4ec0\u4e48\u610f\u601d",
    "\u600e\u4e48\u89c4\u5b9a",
)
_PURE_LAW_FACT_MARKERS = (
    "\u6211",
    "\u672c\u4eba",
    "\u6211\u4eec",
    "\u516c\u53f8",
    "\u8001\u677f",
    "\u6708\u85aa",
    "\u5de5\u8d44",
    "\u5de5\u4f5c",
    "\u88ab\u8f9e\u9000",
    "\u6ca1\u7b7e\u5408\u540c",
    "\u672a\u7b7e\u5408\u540c",
    "\u5de5\u4f24",
    "\u79bb\u5a5a",
    "\u6d41\u6c34",
    "\u804a\u5929\u8bb0\u5f55",
)
_PURE_LAW_AMOUNT_PATTERN = re.compile(r"\d{2,}(?:\.\d+)?\s*(?:\u5143|\u4e07)")


def looks_like_pure_law_reference_question(question: str) -> bool:
    cleaned = " ".join((question or "").strip().split())
    if not cleaned:
        return False

    has_reference = any(term in cleaned for term in _PURE_LAW_REFERENCE_TERMS) or any(
        pattern.search(cleaned) for pattern in _PURE_LAW_REFERENCE_PATTERNS
    )
    if not has_reference:
        return False

    has_case_fact = any(marker in cleaned for marker in _PURE_LAW_FACT_MARKERS) or bool(
        _PURE_LAW_AMOUNT_PATTERN.search(cleaned)
    )
    if has_case_fact:
        return False

    return cleaned.endswith(("?", "\uff1f")) or any(
        term in cleaned
        for term in (
            "\u89c4\u5b9a\u4e86\u4ec0\u4e48",
            "\u8bf4\u4e86\u4ec0\u4e48",
            "\u662f\u4ec0\u4e48\u610f\u601d",
            "\u600e\u4e48\u89c4\u5b9a",
            "\u6709\u4ec0\u4e48\u5185\u5bb9",
        )
    )


LAW_INTENT_KEYWORDS = (
    "法条", "法律", "法规", "量刑", "依据", "民法典", "刑法",
    "劳动合同法", "赔偿", "算不算", "怎么判", "如何认定", "是否构成", "工伤",
    "权益", "权利", "主张", "能要", "能不能要", "可以要",
    "清单", "违法解除", "违法辞退", "违法开除", "未签", "没签",
    "经济补偿", "代通知金", "试用期", "无固定期限",
    "社保", "公积金", "加班费", "年假",
)

CASE_INTENT_KEYWORDS = (
    "案例", "类案", "判决", "案情", "指导案例", "裁判",
    "有没有案例", "实际案例", "相似案例", "判例", "怎么判", "法院判",
    "先例", "类似案件",
)

GRAPH_INTENT_KEYWORDS = (
    "案由", "纠纷", "罪名", "原告", "被告", "公司",
    "劳动争议", "逃税", "合同纠纷", "entity", "case",
)

_GRAPH_ENTITY_PATTERNS = (
    re.compile(r"([A-Za-z0-9\u4e00-\u9fa5]{2,40}(?:公司|集团|学校|医院|银行|事务所|工厂|厂|店))"),
    re.compile(r"(?:原告|被告|申请人|被申请人|上诉人|被上诉人|老板|对方)[:：]?\s*([A-Za-z0-9\u4e00-\u9fa5]{2,20})"),
)
_GRAPH_ENTITY_REJECT_TOKENS = (
    "我在", "我被", "请告诉我", "告诉我", "拖欠工资", "违法辞退", "违法解除",
    "没签", "未签", "月薪", "工作", "能主张", "法律条文", "典型案例",
)
_GRAPH_ENTITY_PUNCTUATION = "，。！？?；;：:"


def _hit_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _extract_graph_entity_name(query: str) -> str | None:
    cleaned = " ".join((query or "").strip().split())
    if not cleaned:
        return None

    for pattern in _GRAPH_ENTITY_PATTERNS:
        match = pattern.search(cleaned)
        if not match:
            continue
        candidate = match.group(1).strip()
        if (
            candidate
            and candidate not in {"公司", "老板", "对方", "原告", "被告"}
            and not any(token in candidate for token in _GRAPH_ENTITY_REJECT_TOKENS)
            and not any(punct in candidate for punct in _GRAPH_ENTITY_PUNCTUATION)
        ):
            return candidate

    bare = cleaned.strip(_GRAPH_ENTITY_PUNCTUATION + " ")
    if (
        bare
        and len(bare) <= 12
        and not any(token in bare for token in _GRAPH_ENTITY_REJECT_TOKENS)
        and not any(punct in bare for punct in _GRAPH_ENTITY_PUNCTUATION)
    ):
        return bare
    return None


# ======================================================================
# Pure-Python intent detectors (no LLM, no IO)
# ======================================================================

def has_substantive_legal_intent(self, question: str) -> bool:
    if looks_like_pure_law_reference_question(question):
        return True
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
    has_law_intent = _hit_any(question, LAW_INTENT_KEYWORDS) or looks_like_pure_law_reference_question(question)
    has_case_intent = _hit_any(question, CASE_INTENT_KEYWORDS)
    has_graph_intent = _hit_any(question, GRAPH_INTENT_KEYWORDS) or "case" in lowered
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
    graph_entity = _extract_graph_entity_name(question) if intents["has_graph_intent"] else None
    if graph_entity:
        routes.append({"name": GRAPH_TOOL, "arguments": {"entity_name": graph_entity}})
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
        graph_entity = _extract_graph_entity_name(question) if intents["has_graph_intent"] else None
        if graph_entity and GRAPH_TOOL not in names:
            enforced.append({"name": GRAPH_TOOL, "arguments": {"entity_name": graph_entity}})
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
    if looks_like_pure_law_reference_question(text):
        return True
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
        "还能要求什么赔偿", "还能主张什么", "我还能拿什么",
        "还可以要什么", "还可以主张什么", "还能申请什么", "继续怎么维权",
        "那我怎么办", "那怎么赔", "那我还能要求什么",
    ]
    if any(token in text for token in brief_followup_tokens):
        return True
    condensed = "".join(text.split())
    return len(condensed) <= 14 and any(
        token in condensed
        for token in [
            "怎么办", "怎么赔", "怎么告", "维权",
            "还能要求什么", "还能主张什么", "还可以要什么",
            "还可以主张什么",
            "还能申请什么", "拿什么赔偿",
        ]
    )


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
        graph_entity = _extract_graph_entity_name(cleaned_query)
        if graph_entity:
            routes.append({"name": GRAPH_TOOL, "arguments": {"entity_name": graph_entity}})
        return self._dedupe_tool_calls(routes)
    if route_intent == "GENERAL_CONSULT":
        if looks_like_pure_law_reference_question(cleaned_query):
            return [{"name": LAW_TOOL, "arguments": {"query": cleaned_query}}]
        intents = self._detect_intents(cleaned_query)
        routes: list[dict[str, Any]] = []
        if intents["has_law_intent"] or intents["has_case_intent"]:
            routes.append({"name": LAW_TOOL, "arguments": {"query": cleaned_query}})
            routes.append({"name": CASE_TOOL, "arguments": {"query": cleaned_query}})
        graph_entity = _extract_graph_entity_name(cleaned_query) if intents["has_graph_intent"] else None
        if graph_entity:
            routes.append({"name": GRAPH_TOOL, "arguments": {"entity_name": graph_entity}})
        if routes:
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

    if looks_like_pure_law_reference_question(cleaned_question):
        return {
            "route_intent": "GENERAL_CONSULT",
            "tool_calls": self._patched_build_tool_calls_for_intent("GENERAL_CONSULT", cleaned_question),
        }

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
        llm_router = getattr(self, "llm_router", None) or get_llm_router()
        provider = llm_router.get_provider("router")
        logger.info("Router provider: %s", provider.value)
        messages = [
                {"role": "system", "content": TRIPLE_TRACK_ROUTER_PROMPT_V2},
                {
                    "role": "user",
                    "content": (
                        f"历史对话：\n{history_text}\n\n"
                        f"当前卷宗摘要：\n{summary_text}\n\n"
                        f"用户最新输入：{cleaned_question}"
                    ),
                },
            ]
        content = await self._chat_model(self.router_llm, messages, provider=provider)
        parsed = self._safe_json_load(content, {"route_intent": fallback_intent})
        route_intent = str(parsed.get("route_intent", fallback_intent)).upper()
        if route_intent not in {"GREETING", "GENERAL_CONSULT", "CASE_UPDATE", OUT_OF_SCOPE_INTENT}:
            route_intent = fallback_intent
        # Surgery #12A: local 3B router guardrail
        # 规则1：没有历史对话、没有案情摘要时，首轮输入不允许被 LLM 提升为 CASE_UPDATE
        if (
            route_intent == "CASE_UPDATE"
            and not (chat_history or "").strip()
            and not has_case_summary
        ):
            route_intent = "GENERAL_CONSULT"
        # 规则2：已有历史/摘要且问题是短句跟进时，不允许 LLM 降级为 GENERAL_CONSULT
        if (
            route_intent == "GENERAL_CONSULT"
            and history_has_case_continuity(chat_history, case_summary)
            and question_looks_like_brief_followup(cleaned_question)
        ):
            route_intent = "CASE_UPDATE"
        # --- OOS guardrail: 若确定性规则未命中，则不接受 LLM 直接判 OOS ---
        if (
            route_intent == OUT_OF_SCOPE_INTENT
            and detect_out_of_scope_reason(cleaned_question) is None
        ):
            logger.warning(
                "OOS guardrail triggered: LLM said OUT_OF_SCOPE but deterministic "
                "OOS detector found no reason. Overriding to GENERAL_CONSULT. "
                "question=%s",
                cleaned_question[:120],
            )
            route_intent = "GENERAL_CONSULT"
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
    _question = state.get("question") or ""
    _wage_from_question = _extract_monthly_wage(_question)
    _existing_wage = state.get("_detected_monthly_wage")
    _wage_to_persist = (
        _wage_from_question
        if (_wage_from_question is not None and _wage_from_question > 0)
        else _existing_wage
    )
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
        **({"_detected_monthly_wage": _wage_to_persist} if _wage_to_persist else {}),
    }
