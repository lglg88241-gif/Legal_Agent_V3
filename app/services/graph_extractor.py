from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any

from json_repair import repair_json
from langchain_community.chat_models import ChatZhipuAI
from langchain_core.messages import HumanMessage, SystemMessage
from neo4j import GraphDatabase

from app.core.config import settings

logger = logging.getLogger(__name__)

CATEGORY_SUBJECT = "主体"
CATEGORY_OBJECT = "客体"
CATEGORY_EVENT = "事件"
CATEGORY_CONSEQUENCE = "后果"
ALLOWED_CATEGORIES = {
    CATEGORY_SUBJECT,
    CATEGORY_OBJECT,
    CATEGORY_EVENT,
    CATEGORY_CONSEQUENCE,
}
ALLOWED_ACTIONS = {"ADD", "UPDATE", "DELETE"}

GRAPH_EXTRACTION_PROMPT = """
你是一个法律案件动态图谱抽取器。
请从输入文本中提取已经明确出现的事实三元组，并做实体对齐。

规则：
1. 只提取已经明确表达的事实，禁止臆测。
2. 同一事物必须统一命名，例如“我/本人/用户”统一为“当事人”。
3. 每个实体都必须给出 category，且只能是：主体、客体、事件、后果。
4. 只返回 JSON 数组，不要输出任何解释。

返回格式：
[
  {
    "source": "实体A",
    "source_category": "主体",
    "relation": "关系",
    "target": "实体B",
    "target_category": "事件"
  }
]
""".strip()

BELIEF_REVISION_PROMPT = """
你是一个严谨的案情图谱管理员。
这是当前图谱状态：{current_graph}
这是用户的最新输入：{user_input}

请判断新输入是对案情的补充，还是对之前错误信息的修正/推翻。
请严格输出 JSON，动作类型只能是以下三种之一：
ADD
UPDATE
DELETE

要求：
1. 如果是新增事实，输出 ADD。
2. 如果是修正已有事实，输出 UPDATE，并明确 old_target 与 new_target。
3. 如果是推翻错误事实，输出 DELETE。
4. 所有实体都要做命名对齐，例如“我/本人/用户”统一为“当事人”。
5. 所有 category 只能是：主体、客体、事件、后果。
6. 只输出 JSON，不要输出解释性文字。

输出格式：
{
  "actions": [
    {
      "action": "ADD",
      "source": "当事人",
      "source_category": "主体",
      "relation": "主张",
      "target": "赔偿",
      "target_category": "后果"
    },
    {
      "action": "UPDATE",
      "source": "当事人",
      "source_category": "主体",
      "relation": "过马路状态",
      "old_target": "闯红灯",
      "new_target": "绿灯通行",
      "target_category": "事件"
    },
    {
      "action": "DELETE",
      "source": "当事人",
      "relation": "存在事实",
      "target": "错误事实"
    }
  ]
}
""".strip()

FETCH_FACTS_QUERY = """
MATCH (source:DynamicCaseEntity {conversation_id: $conversation_id})-[rel:CASE_RELATION {conversation_id: $conversation_id}]->(target:DynamicCaseEntity {conversation_id: $conversation_id})
RETURN source.name AS source_name,
       source.category AS source_category,
       rel.relation AS relation,
       target.name AS target_name,
       target.category AS target_category
ORDER BY source_name, relation, target_name
LIMIT 500
"""

DELETE_GRAPH_QUERY = """
MATCH (n:DynamicCaseEntity {conversation_id: $conversation_id})
DETACH DELETE n
"""

_graph_llm: ChatZhipuAI | None = None
_graph_lock = threading.Lock()

ENTITY_ALIAS_MAP = {
    "我": "当事人",
    "本人": "当事人",
    "自己": "当事人",
    "用户": "当事人",
    "我们": "当事人",
    "咱们": "当事人",
    "对方车": "肇事车辆",
    "对方车辆": "肇事车辆",
    "对面车辆": "肇事车辆",
    "对方的车": "肇事车辆",
    "肇事车": "肇事车辆",
    "公司": "用人单位",
    "单位": "用人单位",
    "用工单位": "用人单位",
}


def _get_graph_llm() -> ChatZhipuAI:
    global _graph_llm

    if _graph_llm is None:
        with _graph_lock:
            if _graph_llm is None:
                _graph_llm = ChatZhipuAI(
                    model="glm-4",
                    api_key=settings.ZHIPUAI_API_KEY,
                    temperature=0.1,
                )
    return _graph_llm


def _build_driver():
    return GraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
    )


def _canonicalize_entity(name: str) -> str:
    cleaned = " ".join((name or "").strip().split())
    if not cleaned:
        return ""
    return ENTITY_ALIAS_MAP.get(cleaned, cleaned)


def _normalize_category(value: Any, entity_name: str = "") -> str:
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned in ALLOWED_CATEGORIES:
            return cleaned

    name = entity_name.strip()
    if not name:
        return CATEGORY_OBJECT

    if any(token in name for token in ("赔偿", "补偿", "损失", "医疗费", "工伤待遇", "伤残", "后果", "责任")):
        return CATEGORY_CONSEQUENCE
    if any(token in name for token in ("事故", "争议", "仲裁", "解除", "辞退", "工伤认定", "索赔", "违约", "受伤")):
        return CATEGORY_EVENT
    if any(token in name for token in ("车辆", "合同", "工资", "社保", "岗位", "证据", "保险")):
        return CATEGORY_OBJECT
    return CATEGORY_SUBJECT


def _normalize_relation(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _safe_json_load(raw_content: str, fallback: Any) -> Any:
    try:
        return json.loads(repair_json(raw_content))
    except Exception as exc:
        logger.warning("Failed to parse graph JSON payload: %s", exc)
        return fallback


def _normalize_triples(raw_payload: Any) -> list[dict[str, str]]:
    if isinstance(raw_payload, dict):
        candidates = raw_payload.get("triples") or raw_payload.get("relations") or raw_payload.get("data") or []
    elif isinstance(raw_payload, list):
        candidates = raw_payload
    else:
        candidates = []

    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    for item in candidates:
        if not isinstance(item, dict):
            continue

        source = _canonicalize_entity(item.get("source", ""))
        relation = _normalize_relation(item.get("relation", ""))
        target = _canonicalize_entity(item.get("target", ""))
        if not source or not relation or not target:
            continue

        key = (source, relation, target)
        if key in seen:
            continue
        seen.add(key)

        normalized.append(
            {
                "source": source,
                "source_category": _normalize_category(item.get("source_category"), source),
                "relation": relation,
                "target": target,
                "target_category": _normalize_category(item.get("target_category"), target),
            }
        )

    return normalized


def _normalize_actions(raw_payload: Any) -> list[dict[str, str]]:
    if isinstance(raw_payload, dict):
        candidates = raw_payload.get("actions", [])
    elif isinstance(raw_payload, list):
        candidates = raw_payload
    else:
        candidates = []

    normalized: list[dict[str, str]] = []

    for item in candidates:
        if not isinstance(item, dict):
            continue

        action = str(item.get("action", "")).strip().upper()
        if action not in ALLOWED_ACTIONS:
            continue

        source = _canonicalize_entity(item.get("source", ""))
        relation = _normalize_relation(item.get("relation", ""))
        target = _canonicalize_entity(item.get("target", "") or item.get("new_target", ""))
        old_target = _canonicalize_entity(item.get("old_target", "") or item.get("target", ""))

        if action == "ADD" and (not source or not relation or not target):
            continue
        if action == "UPDATE" and (not source or not relation or not old_target or not target):
            continue
        if action == "DELETE" and (not source or not relation or not old_target):
            continue

        normalized.append(
            {
                "action": action,
                "source": source,
                "source_category": _normalize_category(item.get("source_category"), source),
                "relation": relation,
                "target": target,
                "target_category": _normalize_category(item.get("target_category"), target),
                "old_target": old_target,
            }
        )

    return normalized


def _cleanup_orphans(session, conversation_id: int) -> None:
    session.run(
        """
        MATCH (n:DynamicCaseEntity {conversation_id: $conversation_id})
        OPTIONAL MATCH (n)-[r:CASE_RELATION {conversation_id: $conversation_id}]-()
        WITH n, count(r) AS rel_count
        WHERE rel_count = 0
        DELETE n
        """,
        {"conversation_id": conversation_id},
    ).consume()


def _merge_fact(session, conversation_id: int, fact: dict[str, str]) -> None:
    session.run(
        """
        MERGE (source:DynamicCaseEntity {conversation_id: $conversation_id, name: $source})
        ON CREATE SET source.created_at = datetime()
        SET source.updated_at = datetime(),
            source.category = $source_category,
            source.node_type = $source_category
        MERGE (target:DynamicCaseEntity {conversation_id: $conversation_id, name: $target})
        ON CREATE SET target.created_at = datetime()
        SET target.updated_at = datetime(),
            target.category = $target_category,
            target.node_type = $target_category
        MERGE (source)-[rel:CASE_RELATION {
            conversation_id: $conversation_id,
            relation: $relation,
            source_name: $source,
            target_name: $target
        }]->(target)
        ON CREATE SET rel.created_at = datetime()
        SET rel.updated_at = datetime()
        """,
        {
            "conversation_id": conversation_id,
            "source": fact["source"],
            "source_category": fact["source_category"],
            "relation": fact["relation"],
            "target": fact["target"],
            "target_category": fact["target_category"],
        },
    ).consume()


def _delete_fact(session, conversation_id: int, source: str, relation: str, target: str) -> None:
    session.run(
        """
        MATCH (:DynamicCaseEntity {conversation_id: $conversation_id, name: $source})-[rel:CASE_RELATION {
            conversation_id: $conversation_id,
            relation: $relation,
            source_name: $source,
            target_name: $target
        }]->(:DynamicCaseEntity {conversation_id: $conversation_id, name: $target})
        DELETE rel
        """,
        {
            "conversation_id": conversation_id,
            "source": source,
            "relation": relation,
            "target": target,
        },
    ).consume()


def _execute_graph_actions_sync(conversation_id: int, actions: list[dict[str, str]]) -> dict[str, Any]:
    driver = _build_driver()
    applied = 0

    try:
        with driver.session() as session:
            for action in actions:
                action_type = action["action"]
                if action_type == "ADD":
                    _merge_fact(
                        session,
                        conversation_id,
                        {
                            "source": action["source"],
                            "source_category": action["source_category"],
                            "relation": action["relation"],
                            "target": action["target"],
                            "target_category": action["target_category"],
                        },
                    )
                    applied += 1
                elif action_type == "UPDATE":
                    _delete_fact(
                        session,
                        conversation_id,
                        action["source"],
                        action["relation"],
                        action["old_target"],
                    )
                    _merge_fact(
                        session,
                        conversation_id,
                        {
                            "source": action["source"],
                            "source_category": action["source_category"],
                            "relation": action["relation"],
                            "target": action["target"],
                            "target_category": action["target_category"],
                        },
                    )
                    applied += 1
                elif action_type == "DELETE":
                    _delete_fact(
                        session,
                        conversation_id,
                        action["source"],
                        action["relation"],
                        action["old_target"],
                    )
                    applied += 1

            _cleanup_orphans(session, conversation_id)
    finally:
        driver.close()

    return {
        "conversation_id": conversation_id,
        "actions_applied": applied,
        "actions": actions,
    }


def _fetch_case_graph_facts_sync(conversation_id: int) -> list[dict[str, str]]:
    driver = _build_driver()
    try:
        with driver.session() as session:
            records = session.run(FETCH_FACTS_QUERY, {"conversation_id": conversation_id}).data()
    finally:
        driver.close()

    facts: list[dict[str, str]] = []
    for record in records:
        source = str(record.get("source_name") or "").strip()
        relation = str(record.get("relation") or "").strip()
        target = str(record.get("target_name") or "").strip()
        if not source or not relation or not target:
            continue
        facts.append(
            {
                "source": source,
                "source_category": _normalize_category(record.get("source_category"), source),
                "relation": relation,
                "target": target,
                "target_category": _normalize_category(record.get("target_category"), target),
            }
        )
    return facts


async def extract_case_triples(text: str) -> list[dict[str, str]]:
    clean_text = (text or "").strip()
    if not clean_text:
        return []

    try:
        response = await _get_graph_llm().ainvoke(
            [
                SystemMessage(content=GRAPH_EXTRACTION_PROMPT),
                HumanMessage(content=f"输入文本：\n{clean_text}"),
            ]
        )
        content = response.content if isinstance(response.content, str) else str(response.content)
        return _normalize_triples(_safe_json_load(content, []))
    except Exception as exc:
        logger.warning("Graph extraction failed: %s", exc)
        return []


async def get_case_graph_facts(conversation_id: int) -> list[dict[str, str]]:
    try:
        return await asyncio.to_thread(_fetch_case_graph_facts_sync, conversation_id)
    except Exception as exc:
        logger.warning("Failed to load case graph facts for conversation %s: %s", conversation_id, exc)
        return []


async def format_current_graph_state(conversation_id: int) -> str:
    facts = await get_case_graph_facts(conversation_id)
    return json.dumps(facts, ensure_ascii=False)


async def plan_graph_actions(current_graph: str, user_input: str) -> list[dict[str, str]]:
    clean_input = (user_input or "").strip()
    if not clean_input:
        return []

    try:
        response = await _get_graph_llm().ainvoke(
            [
                SystemMessage(content="你必须只输出合法 JSON。"),
                HumanMessage(
                    content=BELIEF_REVISION_PROMPT.format(
                        current_graph=current_graph or "[]",
                        user_input=clean_input,
                    )
                ),
            ]
        )
        content = response.content if isinstance(response.content, str) else str(response.content)
        return _normalize_actions(_safe_json_load(content, {"actions": []}))
    except Exception as exc:
        logger.warning("Belief revision planning failed: %s", exc)
        return []


async def execute_graph_actions(conversation_id: int, actions: list[dict[str, str]]) -> dict[str, Any]:
    if not actions:
        return {
            "conversation_id": conversation_id,
            "actions_applied": 0,
            "actions": [],
        }

    try:
        return await asyncio.to_thread(_execute_graph_actions_sync, conversation_id, actions)
    except Exception as exc:
        logger.warning("Graph action execution failed for conversation %s: %s", conversation_id, exc)
        return {
            "conversation_id": conversation_id,
            "actions_applied": 0,
            "actions": actions,
            "error": str(exc),
        }


async def upsert_case_graph(conversation_id: int, triples: list[dict[str, str]]) -> int:
    add_actions = [
        {
            "action": "ADD",
            "source": triple["source"],
            "source_category": triple["source_category"],
            "relation": triple["relation"],
            "target": triple["target"],
            "target_category": triple["target_category"],
            "old_target": "",
        }
        for triple in triples
    ]
    result = await execute_graph_actions(conversation_id, add_actions)
    return int(result.get("actions_applied", 0))


async def sync_case_graph_from_history(conversation_id: int, chat_history: str) -> int:
    triples = await extract_case_triples(chat_history)
    if not triples:
        return 0
    return await upsert_case_graph(conversation_id, triples)


async def refresh_case_graph(conversation_id: int, user_input: str, chat_history: str = "") -> dict[str, Any]:
    current_graph = await format_current_graph_state(conversation_id)
    actions = await plan_graph_actions(current_graph, user_input)

    if not actions:
        fallback_text = (user_input or "").strip() or (chat_history or "").strip()
        triples = await extract_case_triples(fallback_text)
        actions = [
            {
                "action": "ADD",
                "source": triple["source"],
                "source_category": triple["source_category"],
                "relation": triple["relation"],
                "target": triple["target"],
                "target_category": triple["target_category"],
                "old_target": "",
            }
            for triple in triples
        ]

    return await execute_graph_actions(conversation_id, actions)


async def get_case_graph(conversation_id: int) -> dict[str, Any]:
    facts = await get_case_graph_facts(conversation_id)
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    for index, fact in enumerate(facts):
        source_name = fact["source"]
        target_name = fact["target"]
        source_category = fact["source_category"]
        target_category = fact["target_category"]
        relation = fact["relation"]
        source_id = f"{conversation_id}:node:{source_name}"
        target_id = f"{conversation_id}:node:{target_name}"

        if source_id not in nodes:
            nodes[source_id] = {
                "id": source_id,
                "label": source_name,
                "type": source_category,
                "category": source_category,
                "labels": ["DynamicCaseEntity", source_category],
                "properties": {
                    "conversation_id": conversation_id,
                    "name": source_name,
                    "category": source_category,
                },
            }

        if target_id not in nodes:
            nodes[target_id] = {
                "id": target_id,
                "label": target_name,
                "type": target_category,
                "category": target_category,
                "labels": ["DynamicCaseEntity", target_category],
                "properties": {
                    "conversation_id": conversation_id,
                    "name": target_name,
                    "category": target_category,
                },
            }

        edges.append(
            {
                "id": f"{conversation_id}:{index}:{relation}",
                "source": source_id,
                "target": target_id,
                "label": relation,
                "properties": {
                    "conversation_id": conversation_id,
                    "relation": relation,
                },
            }
        )

    return {
        "query": f"会话 {conversation_id} 案情图谱",
        "nodes": list(nodes.values()),
        "edges": edges,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def format_graph_memory(conversation_id: int, limit: int = 20) -> str:
    facts = await get_case_graph_facts(conversation_id)
    lines = [
        f"{fact['source']}（{fact['source_category']}）-{fact['relation']}-{fact['target']}（{fact['target_category']}）"
        for fact in facts[:limit]
    ]
    return "\n".join(lines)


def _delete_case_graph_sync(conversation_id: int) -> None:
    driver = _build_driver()
    try:
        with driver.session() as session:
            session.run(DELETE_GRAPH_QUERY, {"conversation_id": conversation_id}).consume()
    finally:
        driver.close()


async def delete_case_graph(conversation_id: int) -> None:
    try:
        await asyncio.to_thread(_delete_case_graph_sync, conversation_id)
    except Exception as exc:
        logger.warning("Failed to delete case graph for conversation %s: %s", conversation_id, exc)
