import asyncio
import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from neo4j import GraphDatabase

from app.core.config import settings
from app.services.graph_extractor import (
    get_case_graph,
    get_cross_case_entities,
    get_user_cases_graph_overview,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/graph", tags=["graph"])

GRAPH_SUBGRAPH_QUERY = """
MATCH (n)-[r]-(m)
WHERE any(token IN $tokens WHERE
    coalesce(n.name, '') CONTAINS token OR
    coalesce(n.content, '') CONTAINS token OR
    coalesce(n.cause, '') CONTAINS token OR
    coalesce(n.result, '') CONTAINS token OR
    coalesce(n.judgment, '') CONTAINS token OR
    coalesce(m.name, '') CONTAINS token OR
    coalesce(m.content, '') CONTAINS token OR
    coalesce(m.cause, '') CONTAINS token OR
    coalesce(m.result, '') CONTAINS token OR
    coalesce(m.judgment, '') CONTAINS token
)
RETURN id(n) AS source_id,
       labels(n) AS source_labels,
       properties(n) AS source_properties,
       type(r) AS relation,
       id(m) AS target_id,
       labels(m) AS target_labels,
       properties(m) AS target_properties
LIMIT 20
"""

STOP_PHRASES = [
    "有没有案例",
    "实际案例",
    "类似案例",
    "指导案例",
    "如何认定",
    "怎么判",
    "算不算",
    "是否构成",
    "相关法条",
    "法律依据",
    "请问",
    "一下",
    "案例",
    "法条",
    "法律",
    "依据",
    "判决",
    "结果",
]


def _extract_tokens(query: str) -> list[str]:
    cleaned = query.strip()
    normalized = cleaned
    for phrase in STOP_PHRASES:
        normalized = normalized.replace(phrase, " ")
    normalized = re.sub(r"[，。；：、,.!?！？()（）\[\]\s]+", " ", normalized)

    tokens = [cleaned]
    tokens.extend(re.findall(r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,8}", normalized))

    deduped: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        value = token.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        deduped.append(value)

    return deduped[:8]


def _infer_node_type(labels: list[str], properties: dict) -> str:
    haystack = " ".join(labels) + " " + " ".join(f"{key}:{value}" for key, value in properties.items())
    lowered = haystack.lower()

    if "原告" in haystack or "plaintiff" in lowered:
        return "plaintiff"
    if "被告" in haystack or "defendant" in lowered:
        return "defendant"
    if "案由" in haystack or "cause" in lowered or "纠纷" in haystack:
        return "cause"
    if "判决" in haystack or "裁判" in haystack or "result" in lowered or "judgment" in lowered:
        return "judgment"
    if "法条" in haystack or "law" in lowered or "article" in lowered:
        return "law"
    return "entity"


def _build_node_label(node_id: str, labels: list[str], properties: dict) -> str:
    for key in ("name", "title", "case_name", "cause", "result", "judgment", "content"):
        value = properties.get(key)
        if isinstance(value, str) and value.strip():
            text = value.strip().replace("\n", " ")
            return text[:18] + ("..." if len(text) > 18 else "")

    if labels:
        return "/".join(labels)

    return f"Node {node_id}"


def _fetch_subgraph(query: str) -> dict:
    driver = GraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
    )
    tokens = _extract_tokens(query)
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    try:
        with driver.session() as session:
            records = session.run(GRAPH_SUBGRAPH_QUERY, {"tokens": tokens}).data()
    finally:
        driver.close()

    for index, record in enumerate(records):
        source_id = str(record["source_id"])
        target_id = str(record["target_id"])
        source_labels = record.get("source_labels") or []
        target_labels = record.get("target_labels") or []
        source_properties = record.get("source_properties") or {}
        target_properties = record.get("target_properties") or {}

        if source_id not in nodes:
            nodes[source_id] = {
                "id": source_id,
                "label": _build_node_label(source_id, source_labels, source_properties),
                "type": _infer_node_type(source_labels, source_properties),
                "labels": source_labels,
                "properties": source_properties,
            }

        if target_id not in nodes:
            nodes[target_id] = {
                "id": target_id,
                "label": _build_node_label(target_id, target_labels, target_properties),
                "type": _infer_node_type(target_labels, target_properties),
                "labels": target_labels,
                "properties": target_properties,
            }

        relation = record.get("relation") or "RELATED_TO"
        edges.append(
            {
                "id": f"{source_id}-{target_id}-{relation}-{index}",
                "source": source_id,
                "target": target_id,
                "label": relation,
            }
        )

    return {
        "query": query,
        "tokens": tokens,
        "nodes": list(nodes.values()),
        "edges": edges,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/subgraph")
async def get_subgraph(query: str = Query(..., min_length=1, max_length=200)):
    normalized = query.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="query is required")

    try:
        return await asyncio.to_thread(_fetch_subgraph, normalized)
    except Exception as exc:
        logger.exception("Failed to fetch Neo4j subgraph: %s", exc)
        raise HTTPException(status_code=503, detail="Neo4j subgraph is unavailable") from exc


@router.get("/user/{user_id}/cases")
async def get_user_cases(user_id: int):
    """获取某用户所有案件的图谱概览。"""
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="invalid user_id")

    cases = await get_user_cases_graph_overview(user_id)
    return {
        "user_id": user_id,
        "cases": cases,
        "total": len(cases),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/user/{user_id}/cross-case-entities")
async def get_user_cross_case_entities(user_id: int):
    """发现用户跨案件的共享实体（同一公司/人出现在多个案件中）。"""
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="invalid user_id")

    entities = await get_cross_case_entities(user_id)
    return {
        "user_id": user_id,
        "shared_entities": entities,
        "total": len(entities),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
