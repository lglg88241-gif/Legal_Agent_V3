from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.services.conversation_store import (
    build_dossier_participant_candidates,
    build_dossier_participant_lookup,
    get_dossier_for_user,
    is_generic_participant_label,
    list_dossier_conversations,
    list_dossier_participant_mentions,
    list_dossier_participants,
    serialize_dossier,
    serialize_dossier_participant,
    serialize_dossier_participant_mention,
)
from app.services.dossier_display_graph import build_dossier_display_graph
from app.services.dossier_graph import get_dossier_graph_payload
from app.services.dossier_intelligence import _build_party_naming_task

_TRACE_MODE = "rules"
_TRACE_PROVIDER = "rules"
_TRACE_MODEL = "dossier-coordination-rules-v0"
_TRACE_FALLBACK_REASON = "llm_not_enabled_in_skeleton"

_SELF_ANCHOR_LABELS = {
    "当事人",
    "我",
    "本人",
    "自己",
    "用户",
    "我们",
    "咱们",
}

_PERSON_TYPES = {
    "person",
    "natural_person",
    "person_or_role",
    "角色主体",
    "自然人",
}
_ORGANIZATION_TYPES = {
    "organization",
    "company",
    "employer",
    "organization_entity",
    "组织",
    "公司",
    "用人单位",
}
_AUTHORITY_TYPES = {
    "authority",
    "机构",
    "机关",
}
_PARTY_TYPES = {
    "party",
    "subject",
    "主体",
}
_NON_ANCHOR_NODE_TYPES = {
    "event",
    "fact",
    "evidence",
    "document",
    "amount",
    "money",
    "time",
    "date",
    "law",
    "statute",
    "claim",
    "procedure",
    "relationship_event",
    "action",
    "issue",
    "事件",
    "事实",
    "证据",
    "文书",
    "金额",
    "金钱",
    "时间",
    "日期",
    "法律",
    "法条",
    "法规",
    "请求",
    "诉求",
    "程序",
    "关系事件",
    "行为",
    "争议点",
}


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _normalize_key(value: Any) -> str:
    return _normalize_text(value).lower()


def _positive_int(value: Any) -> int:
    try:
        normalized = int(value or 0)
    except (TypeError, ValueError):
        normalized = 0
    return normalized if normalized > 0 else 0


def _sorted_unique_ints(values: list[Any]) -> list[int]:
    unique_values = {_positive_int(value) for value in values}
    return sorted(value for value in unique_values if value > 0)


def _sorted_unique_strings(values: list[Any]) -> list[str]:
    normalized = {_normalize_text(value) for value in values}
    return sorted(value for value in normalized if value)


def _extract_user_id(current_user: User) -> int:
    raw_user_id = getattr(current_user, "id", None)
    if raw_user_id is None:
        raw_user_id = getattr(current_user, "user_id", None)
    user_id = _positive_int(raw_user_id)
    if user_id <= 0:
        raise ValueError("Current user is required")
    return user_id


def _graph_payload_graph(raw_graph_payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = raw_graph_payload if isinstance(raw_graph_payload, dict) else {}
    graph = payload.get("graph")
    return graph if isinstance(graph, dict) else {}


def _graph_nodes(raw_graph_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    graph = _graph_payload_graph(raw_graph_payload)
    nodes = graph.get("nodes")
    return [node for node in nodes if isinstance(node, dict)] if isinstance(nodes, list) else []


def _graph_edges(raw_graph_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    graph = _graph_payload_graph(raw_graph_payload)
    edges = graph.get("edges")
    return [edge for edge in edges if isinstance(edge, dict)] if isinstance(edges, list) else []


def _raw_node_id(node: dict[str, Any]) -> str:
    return _normalize_text(node.get("id") or node.get("graph_node_id"))


def _raw_node_label(node: dict[str, Any]) -> str:
    candidates = [
        node.get("raw_label"),
        node.get("label"),
        node.get("graph_node_label"),
        node.get("display_label"),
    ]
    properties = node.get("properties")
    if isinstance(properties, dict):
        candidates.extend(
            [
                properties.get("raw_label"),
                properties.get("name"),
                properties.get("label"),
                properties.get("title"),
            ]
        )

    for candidate in candidates:
        normalized = _normalize_text(candidate)
        if normalized:
            return normalized
    return ""


def _raw_node_type(node: dict[str, Any]) -> str:
    candidates = [
        node.get("type"),
        node.get("entity_type"),
        node.get("graph_node_type"),
    ]
    properties = node.get("properties")
    if isinstance(properties, dict):
        candidates.extend(
            [
                properties.get("node_type"),
                properties.get("entity_type"),
                properties.get("category"),
            ]
        )

    for candidate in candidates:
        normalized = _normalize_text(candidate)
        if normalized:
            return normalized
    return "unknown"


def _raw_node_conversation_id(node: dict[str, Any]) -> int:
    direct_id = _positive_int(node.get("conversation_id"))
    if direct_id > 0:
        return direct_id

    properties = node.get("properties")
    if isinstance(properties, dict):
        property_id = _positive_int(properties.get("conversation_id"))
        if property_id > 0:
            return property_id
    return 0


def _source_conversation_ids_from_node(node: dict[str, Any]) -> list[int]:
    values: list[int] = []
    source_ids = node.get("source_conversation_ids")
    if isinstance(source_ids, list):
        values.extend(source_ids)

    direct_id = _raw_node_conversation_id(node)
    if direct_id > 0:
        values.append(direct_id)
    return _sorted_unique_ints(values)


def _raw_node_ids_from_node(node: dict[str, Any]) -> list[str]:
    raw_node_ids = node.get("raw_node_ids")
    if isinstance(raw_node_ids, list):
        normalized = _sorted_unique_strings(raw_node_ids)
        if normalized:
            return normalized

    node_id = _raw_node_id(node)
    return [node_id] if node_id else []


def _raw_labels_from_node(node: dict[str, Any]) -> list[str]:
    raw_labels = node.get("raw_labels")
    if isinstance(raw_labels, list):
        normalized = _sorted_unique_strings(raw_labels)
        if normalized:
            return normalized

    label = _raw_node_label(node)
    return [label] if label else []


def _coordination_entity_type(label: str, raw_type: str) -> str:
    normalized_label = _normalize_text(label)
    normalized_type = _normalize_key(raw_type)

    if normalized_label in _SELF_ANCHOR_LABELS or normalized_type == "party":
        return "party"
    if normalized_type in {_normalize_key(value) for value in _ORGANIZATION_TYPES}:
        return "organization"
    if normalized_type in {_normalize_key(value) for value in _PERSON_TYPES}:
        return "person"
    if normalized_type in {_normalize_key(value) for value in _AUTHORITY_TYPES}:
        return "authority"
    if normalized_type in {_normalize_key(value) for value in _PARTY_TYPES}:
        return "party"
    return "other"


def _is_anchor_candidate(node: dict[str, Any]) -> bool:
    label = _raw_node_label(node)
    raw_type = _normalize_key(_raw_node_type(node))

    if raw_type in {_normalize_key(value) for value in _NON_ANCHOR_NODE_TYPES}:
        return False
    if _normalize_text(label) in _SELF_ANCHOR_LABELS:
        return True
    if raw_type in {_normalize_key(value) for value in _PERSON_TYPES | _ORGANIZATION_TYPES | _AUTHORITY_TYPES | _PARTY_TYPES}:
        return True
    return False


def _raw_indexes(raw_graph_payload: dict[str, Any] | None) -> dict[str, Any]:
    nodes = _graph_nodes(raw_graph_payload)
    edges = _graph_edges(raw_graph_payload)
    node_lookup: dict[str, dict[str, Any]] = {}
    node_conversation_lookup: dict[str, int] = {}

    for node in nodes:
        raw_node_id = _raw_node_id(node)
        if not raw_node_id:
            continue
        node_lookup[raw_node_id] = node
        node_conversation_lookup[raw_node_id] = _raw_node_conversation_id(node)

    return {
        "nodes": nodes,
        "edges": edges,
        "node_lookup": node_lookup,
        "node_conversation_lookup": node_conversation_lookup,
    }


def _infer_edge_conversation_id(
    edge: dict[str, Any],
    *,
    node_conversation_lookup: dict[str, int],
    entity_raw_ids: set[str],
) -> int:
    properties = edge.get("properties")
    if isinstance(properties, dict):
        property_conversation_id = _positive_int(properties.get("conversation_id"))
        if property_conversation_id > 0:
            return property_conversation_id

    source_raw_id = _normalize_text(edge.get("source"))
    target_raw_id = _normalize_text(edge.get("target"))
    source_conversation_id = _positive_int(node_conversation_lookup.get(source_raw_id))
    target_conversation_id = _positive_int(node_conversation_lookup.get(target_raw_id))

    if source_conversation_id > 0 and target_conversation_id > 0 and source_conversation_id == target_conversation_id:
        return source_conversation_id
    if source_raw_id in entity_raw_ids and source_conversation_id > 0:
        return source_conversation_id
    if target_raw_id in entity_raw_ids and target_conversation_id > 0:
        return target_conversation_id
    if source_conversation_id > 0:
        return source_conversation_id
    if target_conversation_id > 0:
        return target_conversation_id
    return 0


def _edge_count_by_conversation(
    *,
    raw_node_ids: list[str],
    raw_indexes: dict[str, Any],
) -> dict[str, int]:
    entity_raw_ids = {
        _normalize_text(raw_node_id)
        for raw_node_id in raw_node_ids
        if _normalize_text(raw_node_id)
    }
    if not entity_raw_ids:
        return {}

    counts: dict[int, int] = {}
    node_conversation_lookup = raw_indexes.get("node_conversation_lookup", {})
    for edge in raw_indexes.get("edges", []):
        if not isinstance(edge, dict):
            continue
        source_raw_id = _normalize_text(edge.get("source"))
        target_raw_id = _normalize_text(edge.get("target"))
        if source_raw_id not in entity_raw_ids and target_raw_id not in entity_raw_ids:
            continue

        conversation_id = _infer_edge_conversation_id(
            edge,
            node_conversation_lookup=node_conversation_lookup,
            entity_raw_ids=entity_raw_ids,
        )
        if conversation_id <= 0:
            continue
        counts[conversation_id] = counts.get(conversation_id, 0) + 1

    return {str(key): counts[key] for key in sorted(counts)}


def _best_conversation_for_entity(
    *,
    raw_node_ids: list[str],
    source_conversation_ids: list[int],
    raw_indexes: dict[str, Any],
) -> tuple[int | None, str, dict[str, int]]:
    edge_count_by_conversation = _edge_count_by_conversation(
        raw_node_ids=raw_node_ids,
        raw_indexes=raw_indexes,
    )
    if edge_count_by_conversation:
        best_conversation_id = max(
            (
                (int(conversation_id), count)
                for conversation_id, count in edge_count_by_conversation.items()
            ),
            key=lambda item: (item[1], -item[0]),
        )[0]
        return (
            best_conversation_id,
            "该会话包含该主体最多的关系边",
            edge_count_by_conversation,
        )

    source_ids = _sorted_unique_ints(source_conversation_ids)
    if source_ids:
        return (
            source_ids[0],
            "回退到该主体的首个来源会话",
            edge_count_by_conversation,
        )
    return (None, "当前未找到可用会话来源", edge_count_by_conversation)


def _candidate_node_label(node: dict[str, Any]) -> str:
    return _normalize_text(
        node.get("graph_node_label") or node.get("raw_label") or node.get("label")
    )


def _candidate_node_raw_id(node: dict[str, Any]) -> str:
    return _normalize_text(node.get("graph_node_id") or node.get("id"))


def _candidate_node_conversation_id(node: dict[str, Any]) -> int:
    return _positive_int(node.get("conversation_id"))


def _pending_identity_entity_type(
    source_node: dict[str, Any],
    target_node: dict[str, Any],
) -> str:
    source_type = _coordination_entity_type(
        _candidate_node_label(source_node),
        str(source_node.get("graph_node_type") or source_node.get("entity_type") or ""),
    )
    target_type = _coordination_entity_type(
        _candidate_node_label(target_node),
        str(target_node.get("graph_node_type") or target_node.get("entity_type") or ""),
    )
    if source_type == target_type:
        return source_type
    if source_type != "other":
        return source_type
    if target_type != "other":
        return target_type
    return "other"


def _build_confirmed_entities(
    *,
    display_graph: dict[str, Any] | None,
    raw_indexes: dict[str, Any],
    covered_conversation_ids: set[int],
    covered_raw_node_ids: set[str],
) -> list[dict[str, Any]]:
    nodes = display_graph.get("nodes") if isinstance(display_graph, dict) else []
    entities: list[dict[str, Any]] = []

    for node in nodes if isinstance(nodes, list) else []:
        if not isinstance(node, dict):
            continue
        source_conversation_ids = _source_conversation_ids_from_node(node)
        if not bool(node.get("confirmed")) or len(source_conversation_ids) < 2:
            continue

        raw_node_ids = _raw_node_ids_from_node(node)
        source_labels = _raw_labels_from_node(node)
        label = _normalize_text(node.get("display_label")) or (source_labels[0] if source_labels else "")
        best_conversation_id, best_reason, edge_count_map = _best_conversation_for_entity(
            raw_node_ids=raw_node_ids,
            source_conversation_ids=source_conversation_ids,
            raw_indexes=raw_indexes,
        )
        participant_id = _positive_int(node.get("participant_id")) or None

        entities.append(
            {
                "id": _normalize_text(node.get("id")) or f"participant:{participant_id or len(entities) + 1}",
                "label": label,
                "status": "confirmed",
                "entity_type": _coordination_entity_type(label, str(node.get("entity_type") or "")),
                "source_conversation_ids": source_conversation_ids,
                "source_labels": source_labels,
                "raw_node_ids": raw_node_ids,
                "participant_id": participant_id,
                "best_conversation_id": best_conversation_id,
                "best_conversation_reason": best_reason,
                "edge_count_by_conversation": edge_count_map,
                "suggested_action": "view",
            }
        )
        covered_conversation_ids.update(source_conversation_ids)
        covered_raw_node_ids.update(raw_node_ids)

    return entities


def _build_pending_identity_entities_and_tasks(
    *,
    participant_candidates: list[dict[str, Any]] | None,
    raw_indexes: dict[str, Any],
    covered_conversation_ids: set[int],
    covered_raw_node_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entities: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []

    for index, candidate in enumerate(participant_candidates or [], start=1):
        if not isinstance(candidate, dict):
            continue
        if _normalize_text(candidate.get("task_type")) != "cross_conversation_entity_resolution":
            continue

        source_node = candidate.get("source_node") if isinstance(candidate.get("source_node"), dict) else {}
        target_node = candidate.get("target_node") if isinstance(candidate.get("target_node"), dict) else {}
        source_conversation_ids = _sorted_unique_ints(
            [
                _candidate_node_conversation_id(source_node),
                _candidate_node_conversation_id(target_node),
            ]
        )
        raw_node_ids = _sorted_unique_strings(
            [
                _candidate_node_raw_id(source_node),
                _candidate_node_raw_id(target_node),
            ]
        )
        source_labels = _sorted_unique_strings(
            list(candidate.get("raw_labels") or [])
            + [
                _candidate_node_label(source_node),
                _candidate_node_label(target_node),
            ]
        )
        label = _normalize_text(candidate.get("suggested_label")) or (source_labels[0] if source_labels else f"pending-identity-{index}")
        best_conversation_id, best_reason, edge_count_map = _best_conversation_for_entity(
            raw_node_ids=raw_node_ids,
            source_conversation_ids=source_conversation_ids,
            raw_indexes=raw_indexes,
        )
        task_id = _normalize_text(candidate.get("task_key") or candidate.get("candidate_key")) or f"identity-task:{index}"
        entity_id = f"pending-identity:{task_id}"
        participant_id = _positive_int(
            candidate.get("recommended_participant_id") or candidate.get("existing_participant_id")
        ) or None

        entities.append(
            {
                "id": entity_id,
                "label": label,
                "status": "pending_identity",
                "entity_type": _pending_identity_entity_type(source_node, target_node),
                "source_conversation_ids": source_conversation_ids,
                "source_labels": source_labels,
                "raw_node_ids": raw_node_ids,
                "participant_id": participant_id,
                "best_conversation_id": best_conversation_id,
                "best_conversation_reason": best_reason,
                "edge_count_by_conversation": edge_count_map,
                "suggested_action": "confirm_identity",
            }
        )
        tasks.append(
            {
                "id": task_id,
                "type": "entity_identity",
                "status": "pending",
                "entity_ids": [entity_id],
                "source_conversation_ids": source_conversation_ids,
                "reason": _normalize_text(candidate.get("reason")),
                "confidence": candidate.get("confidence"),
            }
        )
        covered_conversation_ids.update(source_conversation_ids)
        covered_raw_node_ids.update(raw_node_ids)

    return entities, tasks


def _build_pending_naming_entities_and_tasks(
    *,
    intelligence_payload: dict[str, Any] | None,
    raw_indexes: dict[str, Any],
    covered_conversation_ids: set[int],
    covered_raw_node_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entities: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    confirmation_tasks = (
        intelligence_payload.get("confirmation_tasks")
        if isinstance(intelligence_payload, dict)
        else []
    )

    for index, task in enumerate(confirmation_tasks if isinstance(confirmation_tasks, list) else [], start=1):
        if not isinstance(task, dict):
            continue
        if _normalize_text(task.get("task_type")) != "party_naming":
            continue

        evidence = task.get("evidence") if isinstance(task.get("evidence"), dict) else {}
        source_conversation_ids = _sorted_unique_ints(evidence.get("source_conversation_ids") or [])
        raw_node_ids = _sorted_unique_strings(evidence.get("raw_node_ids") or [])
        source_labels = _sorted_unique_strings(
            list(evidence.get("raw_labels") or []) + [_normalize_text(task.get("current_label"))]
        )
        label = _normalize_text(task.get("current_label")) or (source_labels[0] if source_labels else "当事人")
        best_conversation_id, best_reason, edge_count_map = _best_conversation_for_entity(
            raw_node_ids=raw_node_ids,
            source_conversation_ids=source_conversation_ids,
            raw_indexes=raw_indexes,
        )
        raw_node_suffix = ":".join(raw_node_ids) if raw_node_ids else str(index)
        task_id = f"party-naming:{raw_node_suffix}"
        entity_id = f"pending-naming:{raw_node_suffix}"

        entities.append(
            {
                "id": entity_id,
                "label": label,
                "status": "pending_naming",
                "entity_type": "party",
                "source_conversation_ids": source_conversation_ids,
                "source_labels": source_labels,
                "raw_node_ids": raw_node_ids,
                "participant_id": None,
                "best_conversation_id": best_conversation_id,
                "best_conversation_reason": best_reason,
                "edge_count_by_conversation": edge_count_map,
                "suggested_action": "name_party",
            }
        )
        tasks.append(
            {
                "id": task_id,
                "type": "party_naming",
                "status": "pending",
                "entity_ids": [entity_id],
                "source_conversation_ids": source_conversation_ids,
                "reason": _normalize_text(task.get("reason")),
                "confidence": None,
            }
        )
        covered_conversation_ids.update(source_conversation_ids)
        covered_raw_node_ids.update(raw_node_ids)

    return entities, tasks


def _anchor_priority(node: dict[str, Any]) -> tuple[int, int, int, str]:
    label = _raw_node_label(node)
    entity_type = _coordination_entity_type(label, _raw_node_type(node))
    raw_node_id = _raw_node_id(node)

    if _normalize_text(label) in _SELF_ANCHOR_LABELS:
        rank = 0
    elif entity_type == "organization":
        rank = 1
    elif entity_type == "person":
        rank = 2
    elif entity_type == "party":
        rank = 3
    elif entity_type == "authority":
        rank = 4
    else:
        rank = 5

    generic_penalty = 1 if is_generic_participant_label(label) else 0
    return (rank, generic_penalty, -len(_normalize_text(label)), raw_node_id)


def _build_isolated_anchor_entities(
    *,
    raw_graph_payload: dict[str, Any] | None,
    bound_conversation_ids: list[int] | None,
    raw_indexes: dict[str, Any],
    covered_conversation_ids: set[int],
    covered_raw_node_ids: set[str],
) -> list[dict[str, Any]]:
    nodes = _graph_nodes(raw_graph_payload)
    if bound_conversation_ids:
        uncovered_conversation_ids = [
            conversation_id
            for conversation_id in _sorted_unique_ints(bound_conversation_ids)
            if conversation_id not in covered_conversation_ids
        ]
    else:
        uncovered_conversation_ids = [
            conversation_id
            for conversation_id in _sorted_unique_ints([_raw_node_conversation_id(node) for node in nodes])
            if conversation_id not in covered_conversation_ids
        ]

    isolated_entities: list[dict[str, Any]] = []
    for conversation_id in uncovered_conversation_ids:
        anchor_candidates = [
            node
            for node in nodes
            if _raw_node_conversation_id(node) == conversation_id
            and _is_anchor_candidate(node)
            and _raw_node_id(node) not in covered_raw_node_ids
        ]
        if not anchor_candidates:
            continue

        anchor_candidates.sort(key=_anchor_priority)
        anchor_node = anchor_candidates[0]
        raw_node_id = _raw_node_id(anchor_node)
        label = _raw_node_label(anchor_node) or raw_node_id
        source_labels = [label]
        best_conversation_id, best_reason, edge_count_map = _best_conversation_for_entity(
            raw_node_ids=[raw_node_id],
            source_conversation_ids=[conversation_id],
            raw_indexes=raw_indexes,
        )
        if not edge_count_map:
            best_reason = "当前仅来源于该会话，作为孤立主体锚点展示"

        isolated_entities.append(
            {
                "id": f"isolated-anchor:{conversation_id}:{raw_node_id}",
                "label": label,
                "status": "isolated_anchor",
                "entity_type": _coordination_entity_type(label, _raw_node_type(anchor_node)),
                "source_conversation_ids": [conversation_id],
                "source_labels": source_labels,
                "raw_node_ids": [raw_node_id],
                "participant_id": None,
                "best_conversation_id": best_conversation_id or conversation_id,
                "best_conversation_reason": best_reason,
                "edge_count_by_conversation": edge_count_map,
                "suggested_action": "review_anchor",
            }
        )

    return isolated_entities


def build_dossier_coordination_graph(
    *,
    raw_graph_payload: dict[str, Any] | None,
    display_graph: dict[str, Any] | None,
    participant_candidates: list[dict[str, Any]] | None,
    intelligence_payload: dict[str, Any] | None,
    bound_conversation_ids: list[int] | None = None,
) -> dict[str, Any]:
    raw_indexes = _raw_indexes(raw_graph_payload)
    covered_conversation_ids: set[int] = set()
    covered_raw_node_ids: set[str] = set()

    confirmed_entities = _build_confirmed_entities(
        display_graph=display_graph,
        raw_indexes=raw_indexes,
        covered_conversation_ids=covered_conversation_ids,
        covered_raw_node_ids=covered_raw_node_ids,
    )
    pending_identity_entities, pending_identity_tasks = _build_pending_identity_entities_and_tasks(
        participant_candidates=participant_candidates,
        raw_indexes=raw_indexes,
        covered_conversation_ids=covered_conversation_ids,
        covered_raw_node_ids=covered_raw_node_ids,
    )
    pending_naming_entities, pending_naming_tasks = _build_pending_naming_entities_and_tasks(
        intelligence_payload=intelligence_payload,
        raw_indexes=raw_indexes,
        covered_conversation_ids=covered_conversation_ids,
        covered_raw_node_ids=covered_raw_node_ids,
    )
    isolated_anchor_entities = _build_isolated_anchor_entities(
        raw_graph_payload=raw_graph_payload,
        bound_conversation_ids=bound_conversation_ids,
        raw_indexes=raw_indexes,
        covered_conversation_ids=covered_conversation_ids,
        covered_raw_node_ids=covered_raw_node_ids,
    )

    entities = (
        confirmed_entities
        + pending_identity_entities
        + pending_naming_entities
        + isolated_anchor_entities
    )
    tasks = pending_identity_tasks + pending_naming_tasks

    return {
        "entities": entities,
        "edges": [],
        "tasks": tasks,
        "stats": {
            "bound_conversations": len(_sorted_unique_ints(bound_conversation_ids or [])),
            "connected_entities": len(confirmed_entities),
            "pending_identity": len(pending_identity_entities),
            "pending_naming": len(pending_naming_entities),
            "isolated_anchors": len(isolated_anchor_entities),
        },
        "trace": {
            "mode": _TRACE_MODE,
            "provider": _TRACE_PROVIDER,
            "model": _TRACE_MODEL,
            "fallback_reason": _TRACE_FALLBACK_REASON,
        },
    }


async def build_dossier_coordination_graph_for_user(
    db: AsyncSession,
    *,
    current_user: User,
    dossier_id: int,
) -> dict[str, Any]:
    user_id = _extract_user_id(current_user)
    dossier = await get_dossier_for_user(db, user_id, dossier_id)
    if dossier is None:
        raise ValueError("Dossier not found")

    conversations = await list_dossier_conversations(db, user_id, dossier.id)
    conversation_ids = [
        conversation.id
        for conversation in conversations
        if isinstance(getattr(conversation, "id", None), int) and conversation.id > 0
    ]
    participant_aliases = serialize_dossier(dossier).get("participant_aliases", {})
    participants = await list_dossier_participants(db, user_id, dossier.id)
    mentions = await list_dossier_participant_mentions(db, user_id, dossier.id)
    participant_lookup = build_dossier_participant_lookup(participants)

    raw_graph_payload = await get_dossier_graph_payload(
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
    display_graph = build_dossier_display_graph(
        raw_graph=raw_graph_payload.get("graph"),
        confirmed_participants=raw_graph_payload.get("confirmed_participants"),
        participant_mappings=raw_graph_payload.get("participant_mappings"),
    )
    participant_candidates = build_dossier_participant_candidates(
        graph_payload=raw_graph_payload,
        participants=participants,
        confirmed_mentions=mentions,
        participant_aliases=participant_aliases,
    )

    confirmation_tasks: list[dict[str, Any]] = []
    party_naming_task = _build_party_naming_task(raw_graph_payload, display_graph)
    if party_naming_task is not None:
        confirmation_tasks.append(party_naming_task)

    coordination_graph = build_dossier_coordination_graph(
        raw_graph_payload=raw_graph_payload,
        display_graph=display_graph,
        participant_candidates=participant_candidates,
        intelligence_payload={"confirmation_tasks": confirmation_tasks},
        bound_conversation_ids=conversation_ids,
    )
    return {
        "coordination_graph": coordination_graph,
        "generated_at": raw_graph_payload.get("generated_at"),
    }
