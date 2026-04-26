from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from neo4j import GraphDatabase

from app.core.config import settings

logger = logging.getLogger(__name__)

DOSSIER_GRAPH_NODES_QUERY = """
MATCH (n:DynamicCaseEntity {user_id: $user_id})
WHERE n.conversation_id IN $conversation_ids
RETURN id(n) AS node_id,
       labels(n) AS labels,
       properties(n) AS properties
ORDER BY toString(id(n))
LIMIT 2000
"""

DOSSIER_GRAPH_EDGES_QUERY = """
MATCH (source:DynamicCaseEntity {user_id: $user_id})-[rel:CASE_RELATION]->(target:DynamicCaseEntity {user_id: $user_id})
WHERE source.conversation_id IN $conversation_ids
  AND target.conversation_id IN $conversation_ids
  AND rel.conversation_id IN $conversation_ids
RETURN id(rel) AS edge_id,
       id(source) AS source_id,
       id(target) AS target_id,
       type(rel) AS edge_type,
       properties(rel) AS properties
ORDER BY toString(id(rel))
LIMIT 4000
"""


def _build_driver():
    return GraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
    )


def _normalize_conversation_ids(conversation_ids: list[int] | None) -> list[int]:
    normalized: list[int] = []
    seen: set[int] = set()
    for conversation_id in conversation_ids or []:
        if not isinstance(conversation_id, int) or conversation_id <= 0 or conversation_id in seen:
            continue
        seen.add(conversation_id)
        normalized.append(conversation_id)
    return normalized


def _build_empty_graph(conversation_count: int) -> dict[str, Any]:
    return {
        "nodes": [],
        "edges": [],
        "stats": {
            "conversation_count": max(int(conversation_count or 0), 0),
            "node_count": 0,
            "edge_count": 0,
        },
    }


def _infer_node_type(labels: list[str], properties: dict[str, Any]) -> str:
    node_type = str(properties.get("node_type") or properties.get("category") or "").strip()
    if node_type:
        return node_type

    for label in labels:
        cleaned = str(label or "").strip()
        if cleaned and cleaned != "DynamicCaseEntity":
            return cleaned
    return "DynamicCaseEntity"


def _build_node_label(node_id: str, properties: dict[str, Any]) -> str:
    for key in ("name", "label", "title"):
        value = properties.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"node:{node_id}"


def _fetch_dossier_graph_sync(
    user_id: int,
    conversation_ids: list[int],
) -> dict[str, list[dict[str, Any]]]:
    normalized_ids = _normalize_conversation_ids(conversation_ids)
    if not normalized_ids:
        return {"nodes": [], "edges": []}

    driver = _build_driver()
    try:
        with driver.session() as session:
            node_records = session.run(
                DOSSIER_GRAPH_NODES_QUERY,
                {"user_id": user_id, "conversation_ids": normalized_ids},
            ).data()
            edge_records = session.run(
                DOSSIER_GRAPH_EDGES_QUERY,
                {"user_id": user_id, "conversation_ids": normalized_ids},
            ).data()
    finally:
        driver.close()

    nodes: list[dict[str, Any]] = []
    for record in node_records:
        node_id = str(record.get("node_id"))
        labels = [str(label) for label in (record.get("labels") or [])]
        properties = dict(record.get("properties") or {})
        conversation_id = properties.get("conversation_id")
        nodes.append(
            {
                "id": node_id,
                "label": _build_node_label(node_id, properties),
                "type": _infer_node_type(labels, properties),
                "conversation_id": conversation_id,
                "labels": labels,
                "properties": properties,
            }
        )

    edges: list[dict[str, Any]] = []
    for record in edge_records:
        edge_id = str(record.get("edge_id"))
        edge_type = str(record.get("edge_type") or "CASE_RELATION").strip() or "CASE_RELATION"
        properties = dict(record.get("properties") or {})
        edges.append(
            {
                "id": edge_id,
                "source": str(record.get("source_id")),
                "target": str(record.get("target_id")),
                "type": edge_type,
                "label": str(properties.get("relation") or edge_type),
                "properties": properties,
            }
        )

    return {"nodes": nodes, "edges": edges}


async def get_dossier_graph_payload(
    *,
    user_id: int,
    dossier_id: int,
    conversation_ids: list[int],
    participant_aliases: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_ids = _normalize_conversation_ids(conversation_ids)
    response: dict[str, Any] = {
        "status": "success",
        "dossier_id": dossier_id,
        "participant_aliases": participant_aliases if isinstance(participant_aliases, dict) else {},
        "graph": _build_empty_graph(len(normalized_ids)),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if not normalized_ids:
        return response

    try:
        graph_data = await asyncio.to_thread(_fetch_dossier_graph_sync, user_id, normalized_ids)
    except Exception as exc:
        logger.warning(
            "Failed to load dossier graph | dossier_id=%s user_id=%s conversation_ids=%s error=%s",
            dossier_id,
            user_id,
            normalized_ids,
            exc,
        )
        response["warning"] = "Neo4j dossier graph is unavailable"
        return response

    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    response["graph"] = {
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "conversation_count": len(normalized_ids),
            "node_count": len(nodes),
            "edge_count": len(edges),
        },
    }
    return response
