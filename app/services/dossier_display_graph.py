from __future__ import annotations

from typing import Any

DISPLAY_PARTICIPANT_KIND = "participant"
DISPLAY_RAW_ENTITY_KIND = "raw_entity"
DISPLAY_RAW_EVENT_KIND = "raw_event"
DISPLAY_RAW_OTHER_KIND = "raw_other"

_RAW_EVENT_NODE_TYPES = {
    "event",
    "fact",
    "claim",
    "procedure",
    "relationship_event",
    "action",
    "issue",
    "事件",
    "事实",
    "诉求",
    "程序",
    "程序事项",
    "关系事件",
    "动作",
    "争议点",
}

_RAW_OTHER_NODE_TYPES = {
    "evidence",
    "document",
    "amount",
    "money",
    "time",
    "date",
    "law",
    "statute",
    "object",
    "consequence",
    "证据",
    "文书",
    "金额",
    "金钱",
    "时间",
    "日期",
    "法律",
    "法条",
    "法規",
    "法条依据",
    "对象",
    "后果",
}

_PARTICIPANT_LIKE_NODE_TYPES = {
    "person",
    "organization",
    "company",
    "employer",
    "authority",
    "vehicle",
    "unknown",
    "subject",
    "person_or_role",
    "natural_person",
    "自然人",
    "组织",
    "公司",
    "用人单位",
    "机构",
    "车辆",
    "主体",
    "角色主体",
}

_DISPLAY_ENTITY_TYPE_OVERRIDES = {
    "company": "organization",
    "employer": "organization",
    "natural_person": "person",
    "person_or_role": "person",
    "subject": "unknown",
}


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _normalize_key(value: Any) -> str:
    return _normalize_text(value).lower()


def _node_id(node: dict[str, Any]) -> str:
    raw_id = _normalize_text(node.get("id"))
    if raw_id:
        return raw_id

    conversation_id = _node_conversation_id(node)
    label = _node_label(node)
    if conversation_id > 0 and label:
        return f"{conversation_id}:{_normalize_key(label)}"
    if label:
        return f"node:{_normalize_key(label)}"
    return "node:unknown"


def _node_label(node: dict[str, Any]) -> str:
    candidates = [
        node.get("raw_label"),
        node.get("label"),
        node.get("graph_node_label"),
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


def _node_conversation_id(node: dict[str, Any]) -> int:
    candidates = [node.get("conversation_id")]
    properties = node.get("properties")
    if isinstance(properties, dict):
        candidates.append(properties.get("conversation_id"))

    for candidate in candidates:
        try:
            conversation_id = int(candidate or 0)
        except (TypeError, ValueError):
            conversation_id = 0
        if conversation_id > 0:
            return conversation_id
    return 0


def _node_type(node: dict[str, Any]) -> str:
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


def _normalized_node_type(node: dict[str, Any]) -> str:
    return _normalize_key(_node_type(node))


def _display_entity_type(raw_type: str) -> str:
    normalized = _normalize_key(raw_type) or "unknown"
    return _DISPLAY_ENTITY_TYPE_OVERRIDES.get(normalized, normalized)


def _is_participant_blocked_node_type(raw_type: str) -> bool:
    normalized = _normalize_key(raw_type)
    return normalized in _RAW_EVENT_NODE_TYPES or normalized in _RAW_OTHER_NODE_TYPES


def _raw_node_kind(node: dict[str, Any]) -> str:
    normalized = _normalized_node_type(node)
    if normalized in _RAW_EVENT_NODE_TYPES:
        return DISPLAY_RAW_EVENT_KIND
    if normalized in _RAW_OTHER_NODE_TYPES:
        return DISPLAY_RAW_OTHER_KIND
    return DISPLAY_RAW_ENTITY_KIND


def _is_participant_mergeable_node(node: dict[str, Any]) -> bool:
    normalized = _normalized_node_type(node)
    if _is_participant_blocked_node_type(normalized):
        return False
    return normalized in _PARTICIPANT_LIKE_NODE_TYPES or normalized == "unknown"


def _mapping_legacy_key(mapping: dict[str, Any]) -> tuple[int, str] | None:
    try:
        conversation_id = int(mapping.get("conversation_id") or 0)
    except (TypeError, ValueError):
        conversation_id = 0
    raw_label = _normalize_key(mapping.get("raw_label"))
    if conversation_id <= 0 or not raw_label:
        return None
    return conversation_id, raw_label


def _build_confirmed_participant_lookup(
    confirmed_participants: list[dict[str, Any]] | None,
) -> dict[int, dict[str, Any]]:
    lookup: dict[int, dict[str, Any]] = {}
    for item in confirmed_participants or []:
        if not isinstance(item, dict):
            continue
        try:
            participant_id = int(item.get("id") or 0)
        except (TypeError, ValueError):
            participant_id = 0
        if participant_id <= 0:
            continue
        lookup[participant_id] = item
    return lookup


def _build_mapping_lookups(
    participant_mappings: list[dict[str, Any]] | None,
) -> tuple[dict[str, dict[str, Any]], dict[tuple[int, str], dict[str, Any]]]:
    graph_node_lookup: dict[str, dict[str, Any]] = {}
    legacy_lookup: dict[tuple[int, str], dict[str, Any]] = {}

    for mapping in participant_mappings or []:
        if not isinstance(mapping, dict):
            continue
        try:
            participant_id = int(mapping.get("participant_id") or 0)
        except (TypeError, ValueError):
            participant_id = 0
        if participant_id <= 0:
            continue

        graph_node_id = _normalize_text(mapping.get("graph_node_id"))
        if graph_node_id and graph_node_id not in graph_node_lookup:
            graph_node_lookup[graph_node_id] = mapping

        legacy_key = _mapping_legacy_key(mapping)
        if legacy_key is not None and legacy_key not in legacy_lookup:
            legacy_lookup[legacy_key] = mapping

    return graph_node_lookup, legacy_lookup


def _find_mapping_for_node(
    node: dict[str, Any],
    *,
    graph_node_lookup: dict[str, dict[str, Any]],
    legacy_lookup: dict[tuple[int, str], dict[str, Any]],
) -> dict[str, Any] | None:
    graph_node_id = _node_id(node)
    mapping = graph_node_lookup.get(graph_node_id)
    if mapping is not None:
        return mapping

    legacy_key = (_node_conversation_id(node), _normalize_key(_node_label(node)))
    if legacy_key[0] <= 0 or not legacy_key[1]:
        return None
    return legacy_lookup.get(legacy_key)


def _participant_snapshot(
    mapping: dict[str, Any],
    *,
    confirmed_participant_lookup: dict[int, dict[str, Any]],
    fallback_label: str,
    fallback_entity_type: str,
) -> dict[str, Any] | None:
    try:
        participant_id = int(mapping.get("participant_id") or 0)
    except (TypeError, ValueError):
        participant_id = 0
    if participant_id <= 0:
        return None

    participant = confirmed_participant_lookup.get(participant_id, {})
    label = _normalize_text(participant.get("label") or mapping.get("participant_label") or fallback_label)
    entity_type = _display_entity_type(
        participant.get("entity_type")
        or mapping.get("participant_entity_type")
        or fallback_entity_type
    )
    return {
        "participant_id": participant_id,
        "display_label": label or fallback_label or f"participant:{participant_id}",
        "entity_type": entity_type,
    }


def _sorted_unique_ints(values: list[int]) -> list[int]:
    return sorted({int(value) for value in values if int(value) > 0})


def _sorted_unique_strings(values: list[str]) -> list[str]:
    return sorted({value for value in values if _normalize_text(value)})


def _merge_participant_display_node(
    node_bucket: dict[str, Any],
    *,
    participant_snapshot: dict[str, Any],
    raw_node_id: str,
    raw_label: str,
    conversation_id: int,
) -> None:
    node_bucket["display_label"] = participant_snapshot["display_label"]
    node_bucket["entity_type"] = participant_snapshot["entity_type"]
    node_bucket["confirmed"] = True
    node_bucket["pending_confirmation"] = False
    node_bucket["participant_id"] = participant_snapshot["participant_id"]
    node_bucket["source_conversation_ids"] = _sorted_unique_ints(
        list(node_bucket.get("source_conversation_ids", [])) + [conversation_id]
    )
    node_bucket["raw_node_ids"] = _sorted_unique_strings(
        list(node_bucket.get("raw_node_ids", [])) + [raw_node_id]
    )
    node_bucket["raw_labels"] = _sorted_unique_strings(
        list(node_bucket.get("raw_labels", [])) + [raw_label]
    )


def _build_raw_display_node(
    *,
    raw_node_id: str,
    raw_label: str,
    raw_type: str,
    conversation_id: int,
) -> dict[str, Any]:
    kind = _raw_node_kind({"type": raw_type})
    return {
        "id": f"raw:{raw_node_id}",
        "kind": kind,
        "display_label": raw_label or raw_node_id,
        "entity_type": _display_entity_type(raw_type),
        "confirmed": False,
        "pending_confirmation": kind == DISPLAY_RAW_ENTITY_KIND,
        "source_conversation_ids": _sorted_unique_ints([conversation_id]),
        "raw_node_ids": [raw_node_id],
        "participant_id": None,
        "raw_labels": [raw_label] if raw_label else [],
    }


def build_dossier_display_graph(
    raw_graph: dict[str, Any] | None,
    confirmed_participants: list[dict[str, Any]] | None,
    participant_mappings: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    graph = raw_graph if isinstance(raw_graph, dict) else {}
    raw_nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    raw_edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []

    confirmed_participant_lookup = _build_confirmed_participant_lookup(confirmed_participants)
    graph_node_lookup, legacy_lookup = _build_mapping_lookups(participant_mappings)

    display_nodes: dict[str, dict[str, Any]] = {}
    raw_to_display_node_id: dict[str, str] = {}

    for node in raw_nodes:
        if not isinstance(node, dict):
            continue

        raw_node_id = _node_id(node)
        raw_label = _node_label(node)
        raw_type = _node_type(node)
        conversation_id = _node_conversation_id(node)
        mapping = _find_mapping_for_node(
            node,
            graph_node_lookup=graph_node_lookup,
            legacy_lookup=legacy_lookup,
        )

        participant_snapshot = None
        if mapping is not None and _is_participant_mergeable_node(node):
            participant_snapshot = _participant_snapshot(
                mapping,
                confirmed_participant_lookup=confirmed_participant_lookup,
                fallback_label=raw_label,
                fallback_entity_type=raw_type,
            )

        if participant_snapshot is not None:
            display_node_id = f"participant:{participant_snapshot['participant_id']}"
            node_bucket = display_nodes.setdefault(
                display_node_id,
                {
                    "id": display_node_id,
                    "kind": DISPLAY_PARTICIPANT_KIND,
                    "display_label": participant_snapshot["display_label"],
                    "entity_type": participant_snapshot["entity_type"],
                    "confirmed": True,
                    "pending_confirmation": False,
                    "source_conversation_ids": [],
                    "raw_node_ids": [],
                    "participant_id": participant_snapshot["participant_id"],
                    "raw_labels": [],
                },
            )
            _merge_participant_display_node(
                node_bucket,
                participant_snapshot=participant_snapshot,
                raw_node_id=raw_node_id,
                raw_label=raw_label,
                conversation_id=conversation_id,
            )
        else:
            display_node = _build_raw_display_node(
                raw_node_id=raw_node_id,
                raw_label=raw_label,
                raw_type=raw_type,
                conversation_id=conversation_id,
            )
            display_node_id = display_node["id"]
            display_nodes[display_node_id] = display_node

        raw_to_display_node_id[raw_node_id] = display_node_id

    normalized_display_nodes = sorted(
        display_nodes.values(),
        key=lambda item: (
            0 if item.get("kind") == DISPLAY_PARTICIPANT_KIND else 1,
            str(item.get("display_label") or ""),
            str(item.get("id") or ""),
        ),
    )
    display_node_lookup = {
        str(item.get("id") or "").strip(): item
        for item in normalized_display_nodes
        if str(item.get("id") or "").strip()
    }

    normalized_edges: list[dict[str, Any]] = []
    for edge in raw_edges:
        if not isinstance(edge, dict):
            continue

        source_raw_id = _normalize_text(edge.get("source"))
        target_raw_id = _normalize_text(edge.get("target"))
        if not source_raw_id or not target_raw_id:
            continue

        source_display_id = raw_to_display_node_id.get(source_raw_id, f"raw:{source_raw_id}")
        target_display_id = raw_to_display_node_id.get(target_raw_id, f"raw:{target_raw_id}")
        source_display_node = display_node_lookup.get(source_display_id, {})
        target_display_node = display_node_lookup.get(target_display_id, {})
        source_conversation_ids = (
            source_display_node.get("source_conversation_ids")
            if isinstance(source_display_node.get("source_conversation_ids"), list)
            else []
        )
        target_conversation_ids = (
            target_display_node.get("source_conversation_ids")
            if isinstance(target_display_node.get("source_conversation_ids"), list)
            else []
        )

        normalized_edges.append(
            {
                "id": _normalize_text(edge.get("id")) or f"edge:{source_raw_id}:{target_raw_id}",
                "source": source_display_id,
                "target": target_display_id,
                "label": _normalize_text(edge.get("label") or edge.get("type")) or "CASE_RELATION",
                "source_raw_id": source_raw_id,
                "target_raw_id": target_raw_id,
                "source_conversation_ids": _sorted_unique_ints(list(source_conversation_ids)),
                "target_conversation_ids": _sorted_unique_ints(list(target_conversation_ids)),
                "conversation_ids": _sorted_unique_ints(
                    list(source_conversation_ids) + list(target_conversation_ids)
                ),
            }
        )

    pending_confirmation_count = sum(
        1 for node in normalized_display_nodes if bool(node.get("pending_confirmation"))
    )
    merged_participant_count = sum(
        1 for node in normalized_display_nodes if node.get("kind") == DISPLAY_PARTICIPANT_KIND
    )

    return {
        "nodes": normalized_display_nodes,
        "edges": normalized_edges,
        "stats": {
            "raw_node_count": sum(1 for node in raw_nodes if isinstance(node, dict)),
            "raw_edge_count": sum(1 for edge in raw_edges if isinstance(edge, dict)),
            "display_node_count": len(normalized_display_nodes),
            "display_edge_count": len(normalized_edges),
            "merged_participant_count": merged_participant_count,
            "pending_confirmation_count": pending_confirmation_count,
        },
    }


def build_dossier_display_graph_from_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    normalized_payload = payload if isinstance(payload, dict) else {}
    return build_dossier_display_graph(
        raw_graph=normalized_payload.get("graph"),
        confirmed_participants=normalized_payload.get("confirmed_participants"),
        participant_mappings=normalized_payload.get("participant_mappings"),
    )

