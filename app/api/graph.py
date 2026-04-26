from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from neo4j import GraphDatabase

from app.core.config import settings
from app.core.security import AuthenticatedUser, get_current_user
from app.services.graph_extractor import (
    get_cross_case_entities,
    get_user_cases_graph_overview,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/graph", tags=["graph"])

GRAPH_SUBGRAPH_QUERY = """
MATCH (n:LawArticle)-[r:REFERENCES]-(m:LawArticle)
WHERE any(token IN $tokens WHERE
    coalesce(n.law_title, '') CONTAINS token OR
    coalesce(n.article_label, '') CONTAINS token OR
    toString(coalesce(n.article_num, '')) CONTAINS token OR
    coalesce(m.law_title, '') CONTAINS token OR
    coalesce(m.article_label, '') CONTAINS token OR
    toString(coalesce(m.article_num, '')) CONTAINS token
)
RETURN elementId(n) AS source_id,
       labels(n) AS source_labels,
       properties(n) AS source_properties,
       type(r) AS relation,
       elementId(m) AS target_id,
       labels(m) AS target_labels,
       properties(m) AS target_properties
LIMIT 20
"""


def _extract_tokens(query: str) -> list[str]:
    cleaned = " ".join((query or "").strip().split())
    if not cleaned:
        return []

    tokens = [cleaned]
    tokens.extend(re.findall(r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,12}", cleaned))

    deduped: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        value = token.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped[:8]


def _infer_node_type(labels: list[str], properties: dict[str, Any]) -> str:
    haystack = " ".join(labels) + " " + " ".join(f"{key}:{value}" for key, value in properties.items())
    lowered = haystack.lower()

    if "plaintiff" in lowered:
        return "plaintiff"
    if "defendant" in lowered:
        return "defendant"
    if "cause" in lowered:
        return "cause"
    if "judgment" in lowered or "result" in lowered:
        return "judgment"
    if "law" in lowered or "article" in lowered:
        return "law"
    return "entity"


def _build_node_label(node_id: str, labels: list[str], properties: dict[str, Any]) -> str:
    law_title = str(properties.get("law_title") or "").strip()
    article_label = str(properties.get("article_label") or "").strip()
    combined = f"{law_title}{article_label}".strip()
    if combined:
        return combined[:18] + ("..." if len(combined) > 18 else "")

    for key in ("name", "title", "case_name", "cause", "result", "judgment", "content"):
        value = properties.get(key)
        if isinstance(value, str) and value.strip():
            text = value.strip().replace("\n", " ")
            return text[:18] + ("..." if len(text) > 18 else "")

    if labels:
        return "/".join(labels)
    return f"Node {node_id}"


def _fetch_subgraph(query: str) -> dict[str, Any]:
    driver = GraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
    )
    tokens = _extract_tokens(query)
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

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

        relation = record.get("relation") or "REFERENCES"
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


def _require_same_user(path_user_id: int, current_user: AuthenticatedUser) -> None:
    if path_user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Forbidden")


@router.get("/subgraph")
async def get_subgraph(
    query: str = Query(..., min_length=1, max_length=200),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    _ = current_user
    normalized = query.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="query is required")

    try:
        return await asyncio.to_thread(_fetch_subgraph, normalized)
    except Exception as exc:
        logger.exception("Failed to fetch Neo4j subgraph: %s", exc)
        raise HTTPException(status_code=503, detail="Neo4j subgraph is unavailable") from exc


@router.get("/me/cases")
async def get_my_cases(
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    cases = await get_user_cases_graph_overview(current_user.user_id)
    return {
        "user_id": current_user.user_id,
        "cases": cases,
        "total": len(cases),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/user/{user_id}/cases")
async def get_user_cases(
    user_id: int,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="invalid user_id")
    _require_same_user(user_id, current_user)

    cases = await get_user_cases_graph_overview(user_id)
    return {
        "user_id": user_id,
        "cases": cases,
        "total": len(cases),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/me/cross-case-entities")
async def get_my_cross_case_entities(
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    entities = await get_cross_case_entities(current_user.user_id)
    return {
        "user_id": current_user.user_id,
        "shared_entities": entities,
        "total": len(entities),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/user/{user_id}/cross-case-entities")
async def get_user_cross_case_entities(
    user_id: int,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="invalid user_id")
    _require_same_user(user_id, current_user)

    entities = await get_cross_case_entities(user_id)
    return {
        "user_id": user_id,
        "shared_entities": entities,
        "total": len(entities),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
