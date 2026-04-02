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

GRAPH_EXTRACTION_PROMPT = """
你是一个法律案件动态知识图谱抽取器。
请从给定的最新对话中，只提取已经明确出现的实体和关系，并严格做实体对齐。

硬性规则：
1. 实体消歧（Entity Alignment）：必须将指代同一事物的实体统一命名。例如将“我”“用户”“自己”“本人”统一为“当事人”；将“对方车”“对方车辆”“对面车辆”统一为“肇事车辆”；将“公司”“单位”“用人单位”在语义相同且未出现多个不同主体时统一为“用人单位”。
2. 本体限制（Ontology Limit）：每个实体必须赋予 category，且类别只能是：【主体、客体、事件、后果】。
3. 只提取对话中已经明确表达的事实，禁止臆测、禁止补充对话中没有出现的信息。
4. 如果信息不足，返回空数组 []。
5. 只能返回 JSON 数组，不能有任何解释。

返回格式必须严格如下：
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

UPSERT_GRAPH_QUERY = """
UNWIND $triples AS triple
WITH triple
WHERE trim(coalesce(triple.source, '')) <> ''
  AND trim(coalesce(triple.relation, '')) <> ''
  AND trim(coalesce(triple.target, '')) <> ''
MERGE (source:DynamicCaseEntity {
    conversation_id: $conversation_id,
    name: triple.source
})
ON CREATE SET source.created_at = datetime()
SET source.updated_at = datetime(),
    source.category = triple.source_category,
    source.node_type = triple.source_category
MERGE (target:DynamicCaseEntity {
    conversation_id: $conversation_id,
    name: triple.target
})
ON CREATE SET target.created_at = datetime()
SET target.updated_at = datetime(),
    target.category = triple.target_category,
    target.node_type = triple.target_category
MERGE (source)-[rel:CASE_RELATION {
    conversation_id: $conversation_id,
    relation: triple.relation,
    source_name: triple.source,
    target_name: triple.target
}]->(target)
ON CREATE SET rel.created_at = datetime()
SET rel.updated_at = datetime()
"""

FETCH_GRAPH_QUERY = """
MATCH (source:DynamicCaseEntity {conversation_id: $conversation_id})-[rel:CASE_RELATION {conversation_id: $conversation_id}]->(target:DynamicCaseEntity {conversation_id: $conversation_id})
RETURN source.name AS source_name,
       source.category AS source_category,
       properties(source) AS source_props,
       rel.relation AS relation,
       properties(rel) AS rel_props,
       target.name AS target_name,
       target.category AS target_category,
       properties(target) AS target_props
ORDER BY source_name, relation, target_name
LIMIT 200
"""

DELETE_GRAPH_QUERY = """
MATCH (n:DynamicCaseEntity {conversation_id: $conversation_id})
DETACH DELETE n
"""

_extractor_llm: ChatZhipuAI | None = None
_extractor_lock = threading.Lock()

ENTITY_ALIAS_MAP = {
    "我": "当事人",
    "用户": "当事人",
    "自己": "当事人",
    "本人": "当事人",
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


def _get_extractor_llm() -> ChatZhipuAI:
    global _extractor_llm

    if _extractor_llm is None:
        with _extractor_lock:
            if _extractor_llm is None:
                _extractor_llm = ChatZhipuAI(
                    model="glm-4",
                    api_key=settings.ZHIPUAI_API_KEY,
                    temperature=0.1,
                )
    return _extractor_llm


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

    if any(token in name for token in ("赔偿", "补偿", "损失", "伤残", "医疗费", "后果", "责任", "工伤待遇")):
        return CATEGORY_CONSEQUENCE
    if any(token in name for token in ("事故", "争议", "仲裁", "解除", "辞退", "工伤认定", "违约", "索赔", "受伤", "撞伤")):
        return CATEGORY_EVENT
    if any(token in name for token in ("车辆", "合同", "工资", "社保", "证据", "岗位", "劳动合同", "保险")):
        return CATEGORY_OBJECT
    return CATEGORY_SUBJECT


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

        source = _canonicalize_entity(str(item.get("source", "")))
        relation = " ".join(str(item.get("relation", "")).strip().split())
        target = _canonicalize_entity(str(item.get("target", "")))
        if not source or not relation or not target:
            continue

        source_category = _normalize_category(item.get("source_category"), source)
        target_category = _normalize_category(item.get("target_category"), target)
        key = (source, relation, target)
        if key in seen:
            continue
        seen.add(key)

        normalized.append(
            {
                "source": source,
                "source_category": source_category,
                "relation": relation,
                "target": target,
                "target_category": target_category,
            }
        )

    return normalized


async def extract_case_triples(chat_history: str) -> list[dict[str, str]]:
    history_text = (chat_history or "").strip()
    if not history_text:
        return []

    try:
        response = await _get_extractor_llm().ainvoke(
            [
                SystemMessage(content=GRAPH_EXTRACTION_PROMPT),
                HumanMessage(content=f"最新对话：\n{history_text}"),
            ]
        )
        content = response.content if isinstance(response.content, str) else str(response.content)
        parsed = json.loads(repair_json(content))
        triples = _normalize_triples(parsed)
        logger.info("Extracted %s aligned case triples from chat history", len(triples))
        return triples
    except Exception as exc:
        logger.warning("Graph extraction failed: %s", exc)
        return []


def _upsert_case_graph_sync(conversation_id: int, triples: list[dict[str, str]]) -> int:
    if not triples:
        return 0

    driver = _build_driver()
    try:
        with driver.session() as session:
            session.run(
                UPSERT_GRAPH_QUERY,
                {
                    "conversation_id": conversation_id,
                    "triples": triples,
                },
            ).consume()
    finally:
        driver.close()

    return len(triples)


async def upsert_case_graph(conversation_id: int, triples: list[dict[str, str]]) -> int:
    if not triples:
        return 0

    try:
        return await asyncio.to_thread(_upsert_case_graph_sync, conversation_id, triples)
    except Exception as exc:
        logger.warning("Neo4j upsert failed for conversation %s: %s", conversation_id, exc)
        return 0


async def sync_case_graph_from_history(conversation_id: int, chat_history: str) -> int:
    triples = await extract_case_triples(chat_history)
    if not triples:
        return 0
    return await upsert_case_graph(conversation_id, triples)


def _fetch_case_graph_sync(conversation_id: int) -> dict[str, Any]:
    driver = _build_driver()
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    try:
        with driver.session() as session:
            records = session.run(FETCH_GRAPH_QUERY, {"conversation_id": conversation_id}).data()
    finally:
        driver.close()

    for index, record in enumerate(records):
        source_name = str(record.get("source_name") or "").strip()
        target_name = str(record.get("target_name") or "").strip()
        relation = str(record.get("relation") or "").strip()
        if not source_name or not target_name or not relation:
            continue

        source_category = _normalize_category(record.get("source_category"), source_name)
        target_category = _normalize_category(record.get("target_category"), target_name)
        source_id = f"{conversation_id}:node:{source_name}"
        target_id = f"{conversation_id}:node:{target_name}"

        if source_id not in nodes:
            source_props = record.get("source_props") or {}
            nodes[source_id] = {
                "id": source_id,
                "label": source_name,
                "type": source_category,
                "category": source_category,
                "labels": ["DynamicCaseEntity", source_category],
                "properties": source_props,
            }

        if target_id not in nodes:
            target_props = record.get("target_props") or {}
            nodes[target_id] = {
                "id": target_id,
                "label": target_name,
                "type": target_category,
                "category": target_category,
                "labels": ["DynamicCaseEntity", target_category],
                "properties": target_props,
            }

        edges.append(
            {
                "id": f"{conversation_id}:{index}:{relation}",
                "source": source_id,
                "target": target_id,
                "label": relation,
                "properties": record.get("rel_props") or {},
            }
        )

    return {
        "query": f"会话 {conversation_id} 案情图谱",
        "nodes": list(nodes.values()),
        "edges": edges,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def get_case_graph(conversation_id: int) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(_fetch_case_graph_sync, conversation_id)
    except Exception as exc:
        logger.warning("Failed to load case graph for conversation %s: %s", conversation_id, exc)
        return {
            "query": f"会话 {conversation_id} 案情图谱",
            "nodes": [],
            "edges": [],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }


async def format_graph_memory(conversation_id: int, limit: int = 20) -> str:
    graph = await get_case_graph(conversation_id)
    nodes_by_id = {node.get("id"): node for node in graph.get("nodes", [])}
    lines: list[str] = []

    for edge in graph.get("edges", [])[:limit]:
        source_node = nodes_by_id.get(edge.get("source"))
        target_node = nodes_by_id.get(edge.get("target"))
        if not source_node or not target_node:
            continue

        source = str(source_node.get("label") or "").strip()
        source_category = str(source_node.get("category") or "").strip()
        relation = str(edge.get("label") or "").strip()
        target = str(target_node.get("label") or "").strip()
        target_category = str(target_node.get("category") or "").strip()
        if source and relation and target:
            lines.append(f"{source}（{source_category}）-{relation}-{target}（{target_category}）")

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
