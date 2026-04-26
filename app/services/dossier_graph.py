from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from neo4j import GraphDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.conversation_store import (
    build_dossier_participant_lookup,
    get_conversation_for_user,
    get_dossier_for_user,
    list_dossier_conversations,
    list_dossier_participant_mentions,
    list_dossier_participants,
    serialize_dossier,
    serialize_dossier_participant,
    serialize_dossier_participant_mention,
)

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


def _normalize_label_key(label: str | None) -> str:
    return " ".join((label or "").strip().split()).lower()


def _mapping_key(conversation_id: int, raw_label: str, source: str) -> tuple[int, str, str]:
    return (conversation_id, _normalize_label_key(raw_label), source.strip().lower())


def _build_mapping_lookup(
    participant_mappings: list[dict[str, Any]] | None,
) -> dict[tuple[int, str, str], dict[str, Any]]:
    lookup: dict[tuple[int, str, str], dict[str, Any]] = {}
    for item in participant_mappings or []:
        if not isinstance(item, dict):
            continue
        try:
            conversation_id = int(item.get("conversation_id") or 0)
        except (TypeError, ValueError):
            continue
        raw_label = str(item.get("raw_label") or "").strip()
        source = str(item.get("source") or "").strip().lower()
        if conversation_id <= 0 or not raw_label or not source:
            continue
        lookup[_mapping_key(conversation_id, raw_label, source)] = item
    return lookup


def _apply_participant_mappings_to_nodes(
    nodes: list[dict[str, Any]],
    participant_mappings: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    mapping_lookup = _build_mapping_lookup(participant_mappings)
    if not mapping_lookup:
        return nodes

    normalized_nodes: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict):
            normalized_nodes.append(node)
            continue

        conversation_id = node.get("conversation_id")
        if conversation_id is None and isinstance(node.get("properties"), dict):
            conversation_id = node["properties"].get("conversation_id")
        try:
            conversation_id = int(conversation_id or 0)
        except (TypeError, ValueError):
            conversation_id = 0

        raw_label = str(node.get("label") or "").strip()
        mapping = mapping_lookup.get(_mapping_key(conversation_id, raw_label, "graph"))
        if mapping is None:
            normalized_nodes.append(node)
            continue

        normalized_node = dict(node)
        normalized_node["raw_label"] = raw_label
        normalized_node["display_label"] = (
            str(mapping.get("participant_label") or "").strip() or raw_label
        )
        normalized_node["participant_id"] = mapping.get("participant_id")
        normalized_node["participant_role"] = str(mapping.get("participant_role") or "").strip()
        normalized_node["participant_party_side"] = (
            str(mapping.get("participant_party_side") or "").strip() or "unknown"
        )
        normalized_node["participant_entity_type"] = (
            str(mapping.get("participant_entity_type") or "").strip() or "unknown"
        )
        normalized_nodes.append(normalized_node)
    return normalized_nodes


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
    confirmed_participants: list[dict[str, Any]] | None = None,
    participant_mappings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized_ids = _normalize_conversation_ids(conversation_ids)
    response: dict[str, Any] = {
        "status": "success",
        "dossier_id": dossier_id,
        "participant_aliases": participant_aliases if isinstance(participant_aliases, dict) else {},
        "confirmed_participants": (
            confirmed_participants if isinstance(confirmed_participants, list) else []
        ),
        "participant_mappings": (
            participant_mappings if isinstance(participant_mappings, list) else []
        ),
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

    nodes = _apply_participant_mappings_to_nodes(
        graph_data.get("nodes", []),
        response["participant_mappings"],
    )
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


def _format_participant_alias_line(
    key: str,
    participant_aliases: dict[str, Any] | None,
) -> str | None:
    if not isinstance(participant_aliases, dict):
        return None

    alias = participant_aliases.get(key)
    if not isinstance(alias, dict):
        return None

    label = str(alias.get("label") or "").strip()
    role = str(alias.get("role") or "").strip()
    if not label and not role:
        return None

    alias_title = "主要当事人" if key == "primary_party" else "对方"
    if label and role:
        return f"- {alias_title}：{label}（{role}）"
    if label:
        return f"- {alias_title}：{label}"
    return f"- {alias_title}：{role}"


def _format_confirmed_participant_line(participant: dict[str, Any]) -> str | None:
    if not isinstance(participant, dict):
        return None
    label = str(participant.get("label") or "").strip()
    role = str(participant.get("role") or "").strip()
    party_side = str(participant.get("party_side") or "").strip() or "unknown"
    if not label and not role:
        return None
    if label and role:
        return f"- {party_side}: {label}（{role}）"
    return f"- {party_side}: {label or role}"


def build_dossier_graph_summary(
    payload: dict[str, Any] | None,
    *,
    max_nodes: int = 12,
    max_edges: int = 20,
) -> str | None:
    if not isinstance(payload, dict):
        return None

    graph = payload.get("graph") if isinstance(payload.get("graph"), dict) else {}
    stats = graph.get("stats") if isinstance(graph.get("stats"), dict) else {}
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
    participant_aliases = (
        payload.get("participant_aliases")
        if isinstance(payload.get("participant_aliases"), dict)
        else {}
    )
    confirmed_participants = (
        payload.get("confirmed_participants")
        if isinstance(payload.get("confirmed_participants"), list)
        else []
    )

    node_lookup: dict[str, dict[str, Any]] = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "").strip()
        if node_id:
            node_lookup[node_id] = node

    node_lines: list[str] = []
    for node in nodes[: max(max_nodes, 0)]:
        if not isinstance(node, dict):
            continue
        label = (
            str(node.get("display_label") or "").strip()
            or str(node.get("label") or "").strip()
            or f"node:{node.get('id')}"
        )
        raw_label = str(node.get("raw_label") or "").strip()
        node_type = str(node.get("type") or "").strip() or "未分类"
        conversation_id = node.get("conversation_id")
        if conversation_id is None and isinstance(node.get("properties"), dict):
            conversation_id = node["properties"].get("conversation_id")
        suffix = (
            f"（conversation_id={conversation_id}）"
            if conversation_id is not None
            else ""
        )
        raw_suffix = f" | raw={raw_label}" if raw_label and raw_label != label else ""
        node_lines.append(f"- {label} [{node_type}]{suffix}{raw_suffix}")
    if not node_lines:
        node_lines.append("- 暂无实体")

    edge_lines: list[str] = []
    for edge in edges[: max(max_edges, 0)]:
        if not isinstance(edge, dict):
            continue
        source_node = node_lookup.get(str(edge.get("source") or "").strip(), {})
        target_node = node_lookup.get(str(edge.get("target") or "").strip(), {})
        source_label = (
            str(
                source_node.get("display_label")
                or source_node.get("label")
                or edge.get("source")
                or ""
            ).strip()
            or "未知实体"
        )
        target_label = (
            str(
                target_node.get("display_label")
                or target_node.get("label")
                or edge.get("target")
                or ""
            ).strip()
            or "未知实体"
        )
        relation_label = (
            str(edge.get("label") or edge.get("type") or "CASE_RELATION").strip()
            or "CASE_RELATION"
        )
        properties = edge.get("properties", {}) if isinstance(edge.get("properties"), dict) else {}
        conversation_id = properties.get("conversation_id")
        suffix = (
            f"（conversation_id={conversation_id}）"
            if conversation_id is not None
            else ""
        )
        edge_lines.append(f"- {source_label} --{relation_label}--> {target_label}{suffix}")
    if not edge_lines:
        edge_lines.append("- 暂无关系")

    confirmed_participant_lines = [
        line
        for line in (_format_confirmed_participant_line(item) for item in confirmed_participants)
        if line
    ]
    alias_lines = confirmed_participant_lines or [
        line
        for line in (
            _format_participant_alias_line("primary_party", participant_aliases),
            _format_participant_alias_line("opposing_party", participant_aliases),
        )
        if line
    ]
    if not alias_lines:
        alias_lines.append("- 暂无用户确认的主体称呼")

    note_lines = [
        "- 图谱由绑定会话中的结构化案情信息聚合生成，可能包含系统推断的未确认关系。",
        "- 如图谱内容与本轮用户输入冲突，应优先澄清。",
        "- 图谱不能替代法律检索依据。",
        "- 不得把图谱中的推断关系直接当作已证实事实。",
        "- confirmed participant labels 只在当前案卷内表示用户确认的称呼，不等同真实姓名。",
        "- participant aliases 只在当前案卷内表示用户确认的称呼，不等同真实姓名。",
        "- 未经用户确认的主体只能视为未确认 mention，不得直接当作同一主体。",
        "- 不得跨案卷自动合并同名主体；不同案卷中的“我”“公司”“对方”默认不是同一实体。",
    ]
    if len(nodes) > max_nodes:
        note_lines.append(f"- 仅展示前 {max_nodes} 个实体。")
    if len(edges) > max_edges:
        note_lines.append(f"- 仅展示前 {max_edges} 条关系。")
    warning = str(payload.get("warning") or "").strip()
    if warning:
        note_lines.append(f"- 图谱服务提示：{warning}。")

    return (
        "<dossier_graph_summary>\n"
        f"图谱统计：绑定会话 {int(stats.get('conversation_count') or 0)} 个，"
        f"实体 {int(stats.get('node_count') or 0)} 个，"
        f"关系 {int(stats.get('edge_count') or 0)} 条。\n"
        "用户确认的主体称呼：\n"
        f"{chr(10).join(alias_lines)}\n\n"
        "主要实体：\n"
        f"{chr(10).join(node_lines)}\n\n"
        "主要关系：\n"
        f"{chr(10).join(edge_lines)}\n\n"
        "注意：\n"
        f"{chr(10).join(note_lines)}\n"
        "</dossier_graph_summary>"
    )


async def build_dossier_graph_summary_for_conversation(
    db: AsyncSession,
    *,
    user_id: int,
    conversation_id: int,
    max_nodes: int = 12,
    max_edges: int = 20,
) -> str | None:
    conversation = await get_conversation_for_user(db, user_id, conversation_id)
    if conversation is None:
        return None

    dossier_id = getattr(conversation, "dossier_id", None)
    if dossier_id is None:
        return None

    dossier = await get_dossier_for_user(db, user_id, dossier_id)
    if dossier is None:
        return None

    conversations = await list_dossier_conversations(db, user_id, dossier.id)
    conversation_ids = [
        item.id
        for item in conversations
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
            serialize_dossier_participant(participant)
            for participant in participants
        ],
        participant_mappings=[
            serialize_dossier_participant_mention(mention, participant_lookup)
            for mention in mentions
        ],
    )
    return build_dossier_graph_summary(
        payload,
        max_nodes=max_nodes,
        max_edges=max_edges,
    )
