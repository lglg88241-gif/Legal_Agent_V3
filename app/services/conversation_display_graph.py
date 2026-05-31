from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services.conversation_entity_state import SELF_ANCHOR_LABELS


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _normalize_key(value: Any) -> str:
    return _normalize_text(value).lower()


def _node_id(node: dict[str, Any]) -> str:
    return _normalize_text(node.get("id"))


def _node_label(node: dict[str, Any]) -> str:
    candidates = [node.get("label"), node.get("raw_label")]
    properties = node.get("properties")
    if isinstance(properties, dict):
        candidates.extend([properties.get("name"), properties.get("label")])
    for candidate in candidates:
        normalized = _normalize_text(candidate)
        if normalized:
            return normalized
    return ""


def _node_type(node: dict[str, Any]) -> str:
    candidates = [node.get("type"), node.get("entity_type")]
    properties = node.get("properties")
    if isinstance(properties, dict):
        candidates.extend([properties.get("entity_type"), properties.get("category")])
    for candidate in candidates:
        normalized = _normalize_text(candidate)
        if normalized:
            return normalized
    return "unknown"


def _positive_int(value: Any) -> int:
    try:
        normalized = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return normalized if normalized > 0 else 0


def _self_anchor_keys() -> set[str]:
    return {_normalize_key(label) for label in SELF_ANCHOR_LABELS}


def _build_current_party_merge(raw_graph: dict[str, Any], entity_state: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(entity_state, dict):
        return {}

    entities = entity_state.get("entities") if isinstance(entity_state.get("entities"), list) else []
    raw_nodes = raw_graph.get("nodes") if isinstance(raw_graph.get("nodes"), list) else []
    raw_node_lookup = {
        _node_id(node): node
        for node in raw_nodes
        if isinstance(node, dict) and _node_id(node)
    }
    self_anchor_keys = _self_anchor_keys()

    for entity in entities:
        if not isinstance(entity, dict):
            continue
        if _normalize_key(entity.get("role")) != "current_party":
            continue
        if _normalize_key(entity.get("entity_type")) != "person":
            continue

        canonical_label = _normalize_text(entity.get("canonical_label"))
        if not canonical_label or _normalize_key(canonical_label) in self_anchor_keys:
            continue

        mentions = entity.get("mentions") if isinstance(entity.get("mentions"), list) else []
        raw_node_ids: list[str] = []
        raw_labels: list[str] = []
        has_self_anchor = False
        canonical_raw_node_id = ""

        for mention in mentions:
            if not isinstance(mention, dict):
                continue
            raw_label = _normalize_text(mention.get("raw_label") or mention.get("label"))
            raw_node_id = _normalize_text(mention.get("raw_node_id"))
            if raw_label:
                raw_labels.append(raw_label)
            if _normalize_key(raw_label) in self_anchor_keys:
                has_self_anchor = True
            if raw_node_id and raw_node_id in raw_node_lookup:
                raw_node_ids.append(raw_node_id)
                node_label = _node_label(raw_node_lookup[raw_node_id])
                if _normalize_key(node_label) in self_anchor_keys:
                    has_self_anchor = True
                if node_label == canonical_label and not canonical_raw_node_id:
                    canonical_raw_node_id = raw_node_id

        alias_values = entity.get("aliases") if isinstance(entity.get("aliases"), list) else []
        if any(_normalize_key(alias) in self_anchor_keys for alias in alias_values):
            has_self_anchor = True

        unique_raw_node_ids = list(dict.fromkeys(raw_node_ids))
        if not has_self_anchor or len(unique_raw_node_ids) < 2:
            continue

        if not canonical_raw_node_id:
            for raw_node_id in unique_raw_node_ids:
                node_label = _node_label(raw_node_lookup.get(raw_node_id, {}))
                if node_label == canonical_label:
                    canonical_raw_node_id = raw_node_id
                    break
        if not canonical_raw_node_id:
            canonical_raw_node_id = unique_raw_node_ids[0]

        return {
            "display_node_id": canonical_raw_node_id,
            "canonical_label": canonical_label,
            "entity_type": _normalize_text(entity.get("entity_type")) or "person",
            "raw_node_ids": unique_raw_node_ids,
            "raw_labels": list(dict.fromkeys(label for label in raw_labels if label)),
        }

    return {}


def build_conversation_display_graph(
    raw_graph: dict[str, Any] | None,
    entity_state: dict[str, Any] | None,
) -> dict[str, Any]:
    graph = deepcopy(raw_graph) if isinstance(raw_graph, dict) else {}
    raw_nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    raw_edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []

    merge = _build_current_party_merge(graph, entity_state)
    if not merge:
        return graph

    merged_raw_node_ids = set(merge.get("raw_node_ids") or [])
    display_node_id = _normalize_text(merge.get("display_node_id"))
    canonical_label = _normalize_text(merge.get("canonical_label"))
    entity_type = _normalize_text(merge.get("entity_type")) or "person"
    raw_labels = list(dict.fromkeys(merge.get("raw_labels") or []))

    node_id_map: dict[str, str] = {}
    display_nodes: list[dict[str, Any]] = []
    canonical_node_added = False

    for node in raw_nodes:
        if not isinstance(node, dict):
            continue
        raw_node_id = _node_id(node)
        if not raw_node_id:
            continue

        if raw_node_id in merged_raw_node_ids:
            node_id_map[raw_node_id] = display_node_id
            if canonical_node_added:
                continue
            canonical_node_added = True
            merged_node = deepcopy(node)
            merged_node["id"] = display_node_id
            merged_node["label"] = canonical_label
            merged_node["type"] = entity_type
            merged_node["entity_type"] = entity_type
            merged_node["raw_node_ids"] = list(dict.fromkeys(merge.get("raw_node_ids") or []))
            merged_node["raw_labels"] = raw_labels
            properties = deepcopy(merged_node.get("properties")) if isinstance(merged_node.get("properties"), dict) else {}
            properties["name"] = canonical_label
            properties["raw_labels"] = raw_labels
            properties["raw_node_ids"] = list(dict.fromkeys(merge.get("raw_node_ids") or []))
            properties.setdefault("conversation_id", _positive_int(merged_node.get("conversation_id")))
            merged_node["properties"] = properties
            display_nodes.append(merged_node)
            continue

        node_id_map[raw_node_id] = raw_node_id
        display_nodes.append(deepcopy(node))

    normalized_edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for edge in raw_edges:
        if not isinstance(edge, dict):
            continue
        source_id = _normalize_text(edge.get("source"))
        target_id = _normalize_text(edge.get("target"))
        if not source_id or not target_id:
            continue
        mapped_source = node_id_map.get(source_id, source_id)
        mapped_target = node_id_map.get(target_id, target_id)
        if mapped_source == mapped_target:
            continue
        label = _normalize_text(edge.get("label") or edge.get("type")) or "CASE_RELATION"
        dedupe_key = (mapped_source, mapped_target, label)
        if dedupe_key in seen_edges:
            continue
        seen_edges.add(dedupe_key)
        normalized_edge = deepcopy(edge)
        normalized_edge["source"] = mapped_source
        normalized_edge["target"] = mapped_target
        normalized_edges.append(normalized_edge)

    graph["nodes"] = display_nodes
    graph["edges"] = normalized_edges
    return graph


__all__ = ["build_conversation_display_graph"]
