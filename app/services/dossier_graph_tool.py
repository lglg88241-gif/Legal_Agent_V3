from __future__ import annotations

import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.conversation_store import (
    build_dossier_participant_lookup,
    get_conversation_for_user,
    get_dossier_for_user,
    get_dossier_participant_for_user,
    list_dossier_conversations,
    list_dossier_participant_mentions,
    list_dossier_participants,
    serialize_dossier,
    serialize_dossier_participant,
    serialize_dossier_participant_mention,
)
from app.services.dossier_graph import (
    build_dossier_graph_summary,
    get_dossier_graph_payload,
)
from app.services.conversation_mode import (
    LEGAL_QA_KEYWORDS,
    normalize_conversation_mode,
    normalize_persistence_consent,
)

DEFAULT_DOSSIER_GRAPH_CONTEXT_LIMIT = 20
MAX_DOSSIER_GRAPH_CONTEXT_LIMIT = 50

_SUMMARY_QUERY_HINTS = (
    "summary",
    "stats",
    "overview",
    "摘要",
    "概览",
    "总览",
    "统计",
    "整体",
    "全局",
    "图谱情况",
    "图谱概览",
)

_GRAPH_CONTEXT_WARNING = "graph_context 仅代表案卷结构化背景，不构成法律依据。"
_DOSSIER_GRAPH_MEMORY_HINTS = (
    "之前提到",
    "之前说过",
    "之前记录",
    "案卷里",
    "案卷中",
    "这个案子",
    "这个案件",
    "这个案卷",
    "公司是谁",
    "有哪些主体",
    "哪些主体",
    "有哪些证据",
    "哪些证据",
    "我和公司之间",
    "已有案情",
    "历史记录",
    "历史案情",
    "当前案卷",
    "已记录",
    "已确认",
    "关键事实",
    "记录了什么",
)
_DOSSIER_GRAPH_MEMORY_CONTEXT_HINTS = (
    "案卷",
    "案子",
    "案件",
    "历史",
    "之前",
    "记录",
    "确认",
)
_DOSSIER_GRAPH_MEMORY_TOPIC_HINTS = (
    "主体",
    "证据",
    "关系",
    "事实",
    "公司",
    "对方",
)
_DOSSIER_GRAPH_ADVICE_HINTS = (
    "根据这个案子",
    "根据这个案件",
    "结合这个案卷",
    "结合这个案子",
    "下一步",
    "接下来",
    "准备哪些证据",
    "需要准备什么证据",
    "需要准备什么材料",
    "仲裁请求怎么写",
    "仲裁申请书怎么写",
    "仲裁申请怎么写",
    "证据目录",
    "证据清单",
    "帮我整理",
    "帮我起草",
    "帮我生成",
    "怎么维权",
    "怎么主张",
    "该怎么做",
    "应该怎么做",
    "我该怎么办",
    "下一步怎么办",
    "接下来怎么办",
    "风险",
    "胜算",
    "申请劳动仲裁",
    "劳动仲裁",
)
_PURE_LEGAL_QUERY_HINTS = (
    "法条",
    "法律规定",
    "法律依据",
    "赔偿标准",
    "量刑标准",
    "构成要件",
    "司法解释",
    "劳动合同法",
    "民法典",
    "刑法",
    "社会保险法",
    "工伤保险条例",
)
_EXPLICIT_ARTICLE_QUERY_PATTERN = re.compile(
    r"(?:劳动合同法|民法典|刑法|劳动法|社会保险法|工伤保险条例)[^。！？\n]{0,24}?第[一二三四五六七八九十百零〇两\d]+条"
)
_ARTICLE_RULE_QUERY_PATTERN = re.compile(
    r"第[一二三四五六七八九十百零〇两\d]+条[^。！？\n]{0,12}(?:规定了什么|怎么规定|是什么|内容是什么)"
)


def _extract_user_id(current_user: Any) -> int:
    if current_user is None:
        raise ValueError("Authenticated user is required")

    raw_user_id = getattr(current_user, "user_id", None)
    if raw_user_id is None:
        raw_user_id = getattr(current_user, "id", None)

    try:
        user_id = int(raw_user_id or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("Authenticated user is required") from exc

    if user_id <= 0:
        raise ValueError("Authenticated user is required")
    return user_id


def _normalize_limit(limit: int | None) -> int:
    try:
        normalized = int(limit or DEFAULT_DOSSIER_GRAPH_CONTEXT_LIMIT)
    except (TypeError, ValueError):
        normalized = DEFAULT_DOSSIER_GRAPH_CONTEXT_LIMIT
    if normalized <= 0:
        normalized = DEFAULT_DOSSIER_GRAPH_CONTEXT_LIMIT
    return min(normalized, MAX_DOSSIER_GRAPH_CONTEXT_LIMIT)


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _looks_like_explicit_law_article_query(text: str) -> bool:
    normalized = " ".join(str(text or "").strip().split())
    if not normalized:
        return False
    return bool(
        _EXPLICIT_ARTICLE_QUERY_PATTERN.search(normalized)
        or _ARTICLE_RULE_QUERY_PATTERN.search(normalized)
    )


def _looks_like_memory_query(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    if any(hint in normalized for hint in _DOSSIER_GRAPH_MEMORY_HINTS):
        return True
    return (
        any(hint in normalized for hint in _DOSSIER_GRAPH_MEMORY_CONTEXT_HINTS)
        and any(hint in normalized for hint in _DOSSIER_GRAPH_MEMORY_TOPIC_HINTS)
    )


def _looks_like_advice_query(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    return any(hint in normalized for hint in _DOSSIER_GRAPH_ADVICE_HINTS)


def _looks_like_pure_legal_query(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    if _looks_like_explicit_law_article_query(normalized):
        return True
    if any(keyword.lower() in normalized for keyword in LEGAL_QA_KEYWORDS):
        return not (_looks_like_memory_query(normalized) or _looks_like_advice_query(normalized))
    return any(hint.lower() in normalized for hint in _PURE_LEGAL_QUERY_HINTS) and not (
        _looks_like_memory_query(normalized) or _looks_like_advice_query(normalized)
    )


def classify_dossier_graph_query(text: str | None) -> str:
    normalized = " ".join((text or "").strip().split())
    if not normalized:
        return "none"
    if _looks_like_explicit_law_article_query(normalized) or _looks_like_pure_legal_query(normalized):
        return "legal"
    if _looks_like_advice_query(normalized):
        return "advice"
    if _looks_like_memory_query(normalized):
        return "memory"
    return "other"


def is_dossier_memory_query(text: str | None) -> bool:
    return classify_dossier_graph_query(text) == "memory"


def should_query_dossier_graph_context(
    *,
    conversation_mode: str | None,
    dossier_id: int | None,
    route_intent: str | None = None,
    user_message: str,
    persistence_consent: str | None = None,
) -> bool:
    try:
        normalized_dossier_id = int(dossier_id or 0)
    except (TypeError, ValueError):
        normalized_dossier_id = 0
    if normalized_dossier_id <= 0:
        return False

    normalized_mode = normalize_conversation_mode(conversation_mode)
    normalized_consent = normalize_persistence_consent(persistence_consent)
    normalized_route = str(route_intent or "").strip().upper()
    normalized_message = " ".join((user_message or "").strip().split())
    if not normalized_message:
        return False

    if normalized_consent == "temporary":
        return False
    if normalized_mode == "LEGAL_QA" or normalized_route == "LEGAL_QA":
        return False
    query_kind = classify_dossier_graph_query(normalized_message)
    if query_kind == "legal":
        return False
    if query_kind not in {"memory", "advice"}:
        return False

    return normalized_mode in {"TRACKED_CASE", "CASE_ANALYSIS", "UNKNOWN"}


def _is_summary_query(query: str) -> bool:
    normalized = _normalize_text(query)
    if not normalized:
        return True
    return any(hint in normalized for hint in _SUMMARY_QUERY_HINTS)


def _string_matches_query(value: Any, query: str) -> bool:
    candidate = _normalize_text(value)
    if not candidate or not query:
        return False
    if query in candidate:
        return True
    if len(candidate) <= 1:
        return candidate == query
    return candidate in query


def _node_matches_query(node: dict[str, Any], query: str) -> bool:
    fields = [
        node.get("display_label"),
        node.get("raw_label"),
        node.get("label"),
        node.get("type"),
    ]
    return any(_string_matches_query(value, query) for value in fields)


def _edge_matches_query(edge: dict[str, Any], query: str) -> bool:
    return any(
        _string_matches_query(edge.get(key), query)
        for key in ("label", "type")
    )


def _truncate_items(items: list[dict[str, Any]], limit: int) -> tuple[list[dict[str, Any]], bool]:
    if len(items) <= limit:
        return items, False
    return items[:limit], True


def _build_scope(*, user_id: int, dossier_id: int, conversation_id: int) -> dict[str, int]:
    return {
        "user_id": user_id,
        "dossier_id": dossier_id,
        "conversation_id": conversation_id,
    }


def _build_base_response(
    *,
    user_id: int,
    dossier_id: int,
    conversation_id: int,
    query: str,
    query_type: str,
    limit: int,
    stats: dict[str, Any],
    participant_aliases: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "status": "success",
        "source": "graph_context",
        "legal_basis": False,
        "query": query,
        "query_type": query_type,
        "limit": limit,
        "scope": _build_scope(
            user_id=user_id,
            dossier_id=dossier_id,
            conversation_id=conversation_id,
        ),
        "stats": stats,
        "participant_aliases": participant_aliases,
        "participants": [],
        "participant_mentions": [],
        "matched_nodes": [],
        "matched_edges": [],
        "graph_summary": None,
        "warnings": warnings,
        "notes": [_GRAPH_CONTEXT_WARNING],
        "truncated": False,
    }


async def load_dossier_graph_context_for_conversation(
    db: AsyncSession,
    *,
    current_user: Any,
    conversation_id: int,
    user_message: str,
    route_intent: str | None = None,
    limit: int = 12,
) -> dict[str, Any] | None:
    user_id = _extract_user_id(current_user)
    conversation = await get_conversation_for_user(db, user_id, conversation_id)
    if conversation is None:
        return None

    dossier_id = getattr(conversation, "dossier_id", None)
    if not should_query_dossier_graph_context(
        conversation_mode=getattr(conversation, "conversation_mode", None),
        dossier_id=dossier_id,
        route_intent=route_intent,
        user_message=user_message,
        persistence_consent=getattr(conversation, "persistence_consent", None),
    ):
        return None

    return await query_dossier_graph_context(
        db,
        current_user=current_user,
        conversation_id=conversation_id,
        dossier_id=int(dossier_id),
        query=user_message,
        limit=limit,
    )


async def query_dossier_graph_context(
    db: AsyncSession,
    *,
    current_user: Any,
    conversation_id: int,
    dossier_id: int,
    query: str,
    focus_participant_id: int | None = None,
    limit: int = DEFAULT_DOSSIER_GRAPH_CONTEXT_LIMIT,
) -> dict[str, Any]:
    user_id = _extract_user_id(current_user)
    normalized_limit = _normalize_limit(limit)
    normalized_query = str(query or "").strip()

    conversation = await get_conversation_for_user(db, user_id, conversation_id)
    if conversation is None:
        raise ValueError("Conversation not found")

    dossier = await get_dossier_for_user(db, user_id, dossier_id)
    if dossier is None:
        raise ValueError("Dossier not found")

    if getattr(conversation, "dossier_id", None) != dossier.id:
        raise ValueError("Conversation is not bound to the requested dossier")

    participant = None
    if focus_participant_id is not None:
        participant = await get_dossier_participant_for_user(
            db,
            user_id,
            dossier.id,
            focus_participant_id,
        )
        if participant is None:
            raise ValueError("Dossier participant not found")

    dossier_conversations = await list_dossier_conversations(db, user_id, dossier.id)
    conversation_ids = [
        item.id
        for item in dossier_conversations
        if isinstance(getattr(item, "id", None), int) and item.id > 0
    ]
    participant_aliases = serialize_dossier(dossier).get("participant_aliases", {})
    participants = await list_dossier_participants(db, user_id, dossier.id)
    mentions = await list_dossier_participant_mentions(db, user_id, dossier.id)
    participant_lookup = build_dossier_participant_lookup(participants)

    payload = await get_dossier_graph_payload(
        user_id=user_id,
        dossier_id=dossier.id,
        conversation_ids=conversation_ids,
        participant_aliases=participant_aliases,
        confirmed_participants=[
            serialize_dossier_participant(item)
            for item in participants
        ],
        participant_mappings=[
            serialize_dossier_participant_mention(item, participant_lookup)
            for item in mentions
        ],
    )

    graph = payload.get("graph") if isinstance(payload.get("graph"), dict) else {}
    stats = graph.get("stats") if isinstance(graph.get("stats"), dict) else {}
    nodes = [item for item in graph.get("nodes", []) if isinstance(item, dict)]
    edges = [item for item in graph.get("edges", []) if isinstance(item, dict)]
    warnings: list[str] = []
    warning = str(payload.get("warning") or "").strip()
    if warning:
        warnings.append(warning)

    query_type = (
        "participant"
        if participant is not None
        else ("summary" if _is_summary_query(normalized_query) else "keyword")
    )
    response = _build_base_response(
        user_id=user_id,
        dossier_id=dossier.id,
        conversation_id=conversation.id,
        query=normalized_query,
        query_type=query_type,
        limit=normalized_limit,
        stats=stats,
        participant_aliases=participant_aliases,
        warnings=warnings,
    )

    if query_type == "summary":
        response["participants"], participants_truncated = _truncate_items(
            [serialize_dossier_participant(item) for item in participants],
            normalized_limit,
        )
        response["matched_nodes"], nodes_truncated = _truncate_items(nodes, normalized_limit)
        response["matched_edges"], edges_truncated = _truncate_items(edges, normalized_limit)
        response["graph_summary"] = build_dossier_graph_summary(
            payload,
            max_nodes=min(normalized_limit, 12),
            max_edges=min(normalized_limit, 20),
        )
        response["truncated"] = participants_truncated or nodes_truncated or edges_truncated
        if response["truncated"]:
            response["notes"].append("结果已按 limit 截断，仅返回当前案卷图谱的部分预览。")
        return response

    if query_type == "participant":
        assert participant is not None
        participant_mentions = [
            mention
            for mention in mentions
            if getattr(mention, "participant_id", None) == participant.id
        ]
        mention_payloads = [
            serialize_dossier_participant_mention(mention, participant_lookup)
            for mention in participant_mentions
        ]
        raw_labels = {
            _normalize_text(item.get("raw_label"))
            for item in mention_payloads
            if isinstance(item, dict)
        }
        conversation_scope = {
            int(item.get("conversation_id") or 0)
            for item in mention_payloads
            if isinstance(item, dict)
        }

        matched_nodes = [
            node
            for node in nodes
            if int(node.get("participant_id") or 0) == participant.id
            or (
                _normalize_text(node.get("raw_label")) in raw_labels
                and int(node.get("conversation_id") or 0) in conversation_scope
            )
        ]
        matched_node_ids = {
            str(node.get("id") or "").strip()
            for node in matched_nodes
            if str(node.get("id") or "").strip()
        }
        matched_edges = [
            edge
            for edge in edges
            if str(edge.get("source") or "").strip() in matched_node_ids
            or str(edge.get("target") or "").strip() in matched_node_ids
        ]

        response["participants"] = [serialize_dossier_participant(participant)]
        response["participant_mentions"], mentions_truncated = _truncate_items(
            mention_payloads,
            normalized_limit,
        )
        response["matched_nodes"], nodes_truncated = _truncate_items(
            matched_nodes,
            normalized_limit,
        )
        response["matched_edges"], edges_truncated = _truncate_items(
            matched_edges,
            normalized_limit,
        )
        response["truncated"] = mentions_truncated or nodes_truncated or edges_truncated
        if response["truncated"]:
            response["notes"].append("主体相关图谱结果已按 limit 截断。")
        return response

    matched_nodes = [
        node
        for node in nodes
        if _node_matches_query(node, _normalize_text(normalized_query))
    ]
    matched_node_ids = {
        str(node.get("id") or "").strip()
        for node in matched_nodes
        if str(node.get("id") or "").strip()
    }
    matched_edges = []
    for edge in edges:
        source_id = str(edge.get("source") or "").strip()
        target_id = str(edge.get("target") or "").strip()
        if (
            _edge_matches_query(edge, _normalize_text(normalized_query))
            or source_id in matched_node_ids
            or target_id in matched_node_ids
        ):
            matched_edges.append(edge)
            if source_id:
                matched_node_ids.add(source_id)
            if target_id:
                matched_node_ids.add(target_id)

    if matched_node_ids:
        matched_nodes = [
            node
            for node in nodes
            if str(node.get("id") or "").strip() in matched_node_ids
        ]

    related_participant_ids = {
        int(node.get("participant_id") or 0)
        for node in matched_nodes
        if int(node.get("participant_id") or 0) > 0
    }
    participant_payloads = [
        serialize_dossier_participant(item)
        for item in participants
        if item.id in related_participant_ids
        or _string_matches_query(item.label, _normalize_text(normalized_query))
        or _string_matches_query(item.role, _normalize_text(normalized_query))
    ]
    mention_payloads = [
        serialize_dossier_participant_mention(item, participant_lookup)
        for item in mentions
        if int(getattr(item, "participant_id", 0) or 0) in related_participant_ids
        or _string_matches_query(getattr(item, "raw_label", ""), _normalize_text(normalized_query))
    ]

    response["participants"], participants_truncated = _truncate_items(
        participant_payloads,
        normalized_limit,
    )
    response["participant_mentions"], mentions_truncated = _truncate_items(
        mention_payloads,
        normalized_limit,
    )
    response["matched_nodes"], nodes_truncated = _truncate_items(
        matched_nodes,
        normalized_limit,
    )
    response["matched_edges"], edges_truncated = _truncate_items(
        matched_edges,
        normalized_limit,
    )
    response["truncated"] = (
        participants_truncated
        or mentions_truncated
        or nodes_truncated
        or edges_truncated
    )
    if response["truncated"]:
        response["notes"].append("关键词图谱结果已按 limit 截断。")
    return response
