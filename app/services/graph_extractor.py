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

GRAPH_EXTRACTION_PROMPT = """
你是一个法律案件知识图谱抽取器。
请从给定的最新对话中，提取当前案件中已经明确出现的实体与法律关系。

输出要求：
1. 只能返回 JSON。
2. 返回格式必须是三元组数组：
[
  {"source": "A", "relation": "R", "target": "B"}
]
3. 只提取对话中已经明确表达的事实，禁止臆测、禁止补充对话中没有出现的信息。
4. 若没有足够信息，返回空数组 []。
5. relation 使用简洁中文短语，例如：被雇佣于、发生劳动争议、驾驶、撞伤、主张赔偿、签订合同。
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
    source.node_type = coalesce(source.node_type, triple.source_type, 'entity')
MERGE (target:DynamicCaseEntity {
    conversation_id: $conversation_id,
    name: triple.target
})
ON CREATE SET target.created_at = datetime()
SET target.updated_at = datetime(),
    target.node_type = coalesce(target.node_type, triple.target_type, 'entity')
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
       source.node_type AS source_type,
       properties(source) AS source_props,
       rel.relation AS relation,
       properties(rel) AS rel_props,
       target.name AS target_name,
       target.node_type AS target_type,
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


def _infer_node_type(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        return "entity"

    if any(token in cleaned for token in ("公司", "单位", "企业", "工厂", "平台")):
        return "company"
    if any(token in cleaned for token in ("原告", "被告", "用户", "司机", "员工", "老板", "乘客", "劳动者")):
        return "person"
    if any(token in cleaned for token in ("车辆", "汽车", "电动车", "摩托车", "货车")):
        return "asset"
    if any(token in cleaned for token in ("工伤", "劳动争议", "交通事故", "违约", "赔偿", "侵权", "案由")):
        return "issue"
    return "entity"


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

        source = " ".join(str(item.get("source", "")).strip().split())
        relation = " ".join(str(item.get("relation", "")).strip().split())
        target = " ".join(str(item.get("target", "")).strip().split())
        if not source or not relation or not target:
            continue

        key = (source, relation, target)
        if key in seen:
            continue
        seen.add(key)

        normalized.append(
            {
                "source": source,
                "relation": relation,
                "target": target,
                "source_type": _infer_node_type(source),
                "target_type": _infer_node_type(target),
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
        logger.info("Extracted %s case triples from chat history", len(triples))
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

        source_id = f"{conversation_id}:source:{source_name}"
        target_id = f"{conversation_id}:target:{target_name}"

        if source_id not in nodes:
            source_props = record.get("source_props") or {}
            nodes[source_id] = {
                "id": source_id,
                "label": source_name,
                "type": str(record.get("source_type") or "entity"),
                "labels": ["DynamicCaseEntity"],
                "properties": source_props,
            }

        if target_id not in nodes:
            target_props = record.get("target_props") or {}
            nodes[target_id] = {
                "id": target_id,
                "label": target_name,
                "type": str(record.get("target_type") or "entity"),
                "labels": ["DynamicCaseEntity"],
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
    lines: list[str] = []
    for edge in graph.get("edges", [])[:limit]:
        source_node = next((node for node in graph.get("nodes", []) if node.get("id") == edge.get("source")), None)
        target_node = next((node for node in graph.get("nodes", []) if node.get("id") == edge.get("target")), None)
        if not source_node or not target_node:
            continue
        source = str(source_node.get("label") or "").strip()
        relation = str(edge.get("label") or "").strip()
        target = str(target_node.get("label") or "").strip()
        if source and relation and target:
            lines.append(f"{source}-{relation}-{target}")

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
