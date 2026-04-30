from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any

from app.services.dossier_hard_guards import (
    can_create_identity_candidate,
    identity_merge_blockers,
)


SCHEMA_VERSION = "h9.dossier_entity_state.v1"
SOURCE = "dossier_cross_conversation_entity_agent"
PROVIDER = "rules"
MODEL = "dossier-entity-state-rules-v1"


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _normalize_key(value: Any) -> str:
    return _normalize_text(value).lower()


def _positive_int(value: Any) -> int:
    try:
        normalized = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return normalized if normalized > 0 else 0


def _sorted_unique_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = _normalize_text(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _sorted_unique_ints(values: list[Any]) -> list[int]:
    return sorted({_positive_int(value) for value in values if _positive_int(value) > 0})


def _stable_id(*parts: Any) -> str:
    raw = "|".join(_normalize_text(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _conversation_title_lookup(conversation_summaries: list[dict[str, Any]] | None) -> dict[int, str]:
    lookup: dict[int, str] = {}
    for item in conversation_summaries or []:
        if not isinstance(item, dict):
            continue
        conversation_id = _positive_int(item.get("id") or item.get("conversation_id"))
        if conversation_id <= 0:
            continue
        lookup[conversation_id] = _normalize_text(item.get("title")) or f"会话 {conversation_id}"
    return lookup


def _entity_identity(entity: dict[str, Any], conversation_id: int) -> str:
    entity_id = _normalize_text(entity.get("id"))
    if entity_id:
        return entity_id
    return f"conversation:{conversation_id}:entity:{_stable_id(conversation_id, entity.get('canonical_label'), entity.get('entity_type'))}"


def _mention_identity(mention: dict[str, Any], entity_id: str, conversation_id: int) -> str:
    mention_id = _normalize_text(mention.get("id"))
    if mention_id:
        return mention_id
    return f"conversation:{conversation_id}:mention:{_stable_id(entity_id, mention.get('raw_label'), mention.get('raw_node_id'))}"


def _entity_from_conversation_entity(
    entity: dict[str, Any],
    *,
    conversation_id: int,
    dossier_id: int | None,
) -> dict[str, Any] | None:
    label = _normalize_text(entity.get("canonical_label") or entity.get("label"))
    if not label:
        return None
    entity_type = _normalize_text(entity.get("entity_type")) or "unknown"
    role = _normalize_text(entity.get("role"))
    confidence = _normalize_text(entity.get("confidence")) or "medium"
    if role == "current_party" and confidence == "low" and label in {"当前会话当事人", "未解析当事人"}:
        return None
    source_entity_id = _entity_identity(entity, conversation_id)
    mentions: list[dict[str, Any]] = []
    for mention in entity.get("mentions") if isinstance(entity.get("mentions"), list) else []:
        if not isinstance(mention, dict):
            continue
        raw_label = _normalize_text(mention.get("raw_label") or label)
        mention_conversation_id = _positive_int(mention.get("conversation_id")) or conversation_id
        mentions.append(
            {
                "id": _mention_identity(mention, source_entity_id, mention_conversation_id),
                "conversation_id": mention_conversation_id,
                "raw_label": raw_label,
                "raw_node_id": _normalize_text(mention.get("raw_node_id")) or None,
                "source": _normalize_text(mention.get("source")) or "conversation_entity_state",
                "source_entity_id": source_entity_id,
            }
        )
    if not mentions:
        mentions.append(
            {
                "id": f"conversation:{conversation_id}:mention:{_stable_id(source_entity_id, label)}",
                "conversation_id": conversation_id,
                "raw_label": label,
                "raw_node_id": None,
                "source": "conversation_entity_state",
                "source_entity_id": source_entity_id,
            }
        )
    source_labels = _sorted_unique_strings(
        [label]
        + list(entity.get("aliases") or [])
        + [mention.get("raw_label") for mention in mentions]
    )
    return {
        "id": f"dossier:{dossier_id or 0}:entity:{_stable_id(source_entity_id)}",
        "source_entity_ids": [source_entity_id],
        "canonical_label": label,
        "label": label,
        "entity_type": entity_type,
        "role": role,
        "status": "canonical",
        "aliases": _sorted_unique_strings(list(entity.get("aliases") or [])),
        "source_labels": source_labels,
        "source_conversation_ids": [conversation_id],
        "mentions": mentions,
        "evidence": entity.get("evidence") if isinstance(entity.get("evidence"), list) else [],
        "confidence": confidence,
        "dossier_id": dossier_id,
    }


def _decision_key(decision: dict[str, Any]) -> str:
    return _normalize_text(
        decision.get("task_key")
        or decision.get("candidate_key")
        or decision.get("pair_key")
        or decision.get("source_graph_node_id")
    )


def _decision_type(decision: dict[str, Any]) -> str:
    return _normalize_key(decision.get("decision_type") or decision.get("action"))


def _task_key(left_id: str, right_id: str) -> str:
    ordered = sorted((_normalize_text(left_id), _normalize_text(right_id)))
    return f"cross-conversation-same:{ordered[0]}::{ordered[1]}"


def _singleton_canonical_id(entity: dict[str, Any], *, dossier_id: int | None) -> str:
    return f"dossier:{dossier_id or 0}:canonical:{_stable_id(entity.get('source_entity_ids') or [])}"


def _same_decision_index(resolution_decisions: list[dict[str, Any]] | None) -> dict[str, str]:
    index: dict[str, str] = {}
    for decision in resolution_decisions or []:
        if not isinstance(decision, dict):
            continue
        key = _decision_key(decision)
        if not key:
            continue
        decision_type = _decision_type(decision)
        if decision_type in {"same", "confirm", "confirmed"}:
            index[key] = "same"
        elif decision_type in {"not_same", "reject", "rejected", "skip", "not_actionable"}:
            index[key] = "not_same"
    return index


class _UnionFind:
    def __init__(self, values: list[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent.setdefault(value, value)
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def _merge_entity_group(
    entities: list[dict[str, Any]],
    *,
    dossier_id: int | None,
    title_lookup: dict[int, str],
    relations: list[dict[str, Any]],
) -> dict[str, Any]:
    label_counts: dict[str, int] = {}
    for entity in entities:
        label = _normalize_text(entity.get("canonical_label") or entity.get("label"))
        if label:
            label_counts[label] = label_counts.get(label, 0) + 1
    canonical_label = sorted(label_counts.items(), key=lambda item: (-item[1], len(item[0]), item[0]))[0][0]
    entity_type = next((_normalize_text(item.get("entity_type")) for item in entities if _normalize_text(item.get("entity_type"))), "unknown")
    role = next((_normalize_text(item.get("role")) for item in entities if _normalize_text(item.get("role"))), "")
    source_entity_ids = _sorted_unique_strings(
        [source_id for entity in entities for source_id in entity.get("source_entity_ids", [])]
    )
    mentions = [
        mention
        for entity in entities
        for mention in entity.get("mentions", [])
        if isinstance(mention, dict)
    ]
    source_conversation_ids = _sorted_unique_ints(
        [conversation_id for entity in entities for conversation_id in entity.get("source_conversation_ids", [])]
    )
    aliases = _sorted_unique_strings(
        [
            value
            for entity in entities
            for value in (
                list(entity.get("aliases") or [])
                + list(entity.get("source_labels") or [])
                + [_normalize_text(entity.get("canonical_label"))]
            )
            if _normalize_key(value) != _normalize_key(canonical_label)
        ]
    )
    source_labels = _sorted_unique_strings(
        [canonical_label]
        + aliases
        + [mention.get("raw_label") for mention in mentions]
    )
    source_entity_id_set = set(source_entity_ids)
    relation_counts: dict[int, int] = defaultdict(int)
    for relation in relations:
        relation_conversation_id = _positive_int(relation.get("conversation_id"))
        if relation_conversation_id <= 0:
            continue
        if relation.get("source_entity_id") in source_entity_id_set or relation.get("target_entity_id") in source_entity_id_set:
            relation_counts[relation_conversation_id] += 1
    fact_counts: dict[int, int] = defaultdict(int)
    labels_by_conversation: dict[int, list[str]] = defaultdict(list)
    raw_node_ids_by_conversation: dict[int, list[str]] = defaultdict(list)
    for mention in mentions:
        conversation_id = _positive_int(mention.get("conversation_id"))
        if conversation_id <= 0:
            continue
        labels_by_conversation[conversation_id].append(mention.get("raw_label"))
        raw_node_ids_by_conversation[conversation_id].append(mention.get("raw_node_id"))
        fact_counts[conversation_id] += 1

    best_conversation_id = 0
    if source_conversation_ids:
        best_conversation_id = sorted(
            source_conversation_ids,
            key=lambda cid: (-relation_counts.get(cid, 0), -fact_counts.get(cid, 0), cid),
        )[0]
    source_conversations = []
    for conversation_id in source_conversation_ids:
        source_conversations.append(
            {
                "conversation_id": conversation_id,
                "title": title_lookup.get(conversation_id) or f"会话 {conversation_id}",
                "source_labels": _sorted_unique_strings(labels_by_conversation.get(conversation_id, [])),
                "raw_node_ids": _sorted_unique_strings(raw_node_ids_by_conversation.get(conversation_id, [])),
                "fact_count": int(fact_counts.get(conversation_id, 0)),
                "relation_count": int(relation_counts.get(conversation_id, 0)),
                "is_best": conversation_id == best_conversation_id,
            }
        )
    if relation_counts.get(best_conversation_id, 0) > 0:
        reason = "该会话包含该实体最多的相关关系"
    elif fact_counts.get(best_conversation_id, 0) > 0:
        reason = "该会话包含该实体最多的相关事实"
    else:
        reason = "该会话是该实体最早出现的来源会话"

    return {
        "id": f"dossier:{dossier_id or 0}:canonical:{_stable_id(source_entity_ids)}",
        "canonical_label": canonical_label,
        "label": canonical_label,
        "entity_type": entity_type or "unknown",
        "role": role,
        "status": "canonical",
        "aliases": aliases,
        "source_labels": source_labels,
        "source_entity_ids": source_entity_ids,
        "source_conversation_ids": source_conversation_ids,
        "source_conversations": source_conversations,
        "best_conversation_id": best_conversation_id or None,
        "best_conversation_reason": reason if best_conversation_id else "",
        "mentions": mentions,
        "mention_ids": _sorted_unique_strings([mention.get("id") for mention in mentions]),
        "dossier_id": dossier_id,
    }


def _state_relations(
    conversation_entity_states: list[dict[str, Any]],
    source_entity_to_dossier_entity: dict[str, str],
) -> list[dict[str, Any]]:
    relations: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, int]] = set()
    for state in conversation_entity_states:
        if not isinstance(state, dict) or not state.get("should_build", True):
            continue
        conversation_id = _positive_int(state.get("conversation_id"))
        for relation in state.get("relations") if isinstance(state.get("relations"), list) else []:
            if not isinstance(relation, dict):
                continue
            source_entity_id = _normalize_text(relation.get("source"))
            target_entity_id = _normalize_text(relation.get("target"))
            source = source_entity_to_dossier_entity.get(source_entity_id)
            target = source_entity_to_dossier_entity.get(target_entity_id)
            if not source or not target or source == target:
                continue
            relation_type = _normalize_text(relation.get("relation_type")) or "case_relation"
            key = (source, target, relation_type, conversation_id)
            if key in seen:
                continue
            seen.add(key)
            relations.append(
                {
                    "id": f"dossier:relation:{_stable_id(source, target, relation_type, conversation_id)}",
                    "source": source,
                    "target": target,
                    "label": _normalize_text(relation.get("relation")) or relation_type,
                    "relation_type": relation_type,
                    "source_conversation_ids": _sorted_unique_ints([conversation_id]),
                    "confidence": _normalize_text(relation.get("confidence")) or "medium",
                    "evidence": relation.get("evidence") if isinstance(relation.get("evidence"), list) else [],
                    "conversation_id": conversation_id,
                }
            )
    relations.sort(key=lambda item: (str(item.get("relation_type")), str(item.get("source")), str(item.get("target"))))
    return relations


def _candidate_entities_are_same_label(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_label = _normalize_key(left.get("canonical_label") or left.get("label"))
    right_label = _normalize_key(right.get("canonical_label") or right.get("label"))
    if left_label and right_label and left_label == right_label:
        return True
    left_labels = {_normalize_key(value) for value in left.get("source_labels", []) if _normalize_key(value)}
    right_labels = {_normalize_key(value) for value in right.get("source_labels", []) if _normalize_key(value)}
    return bool(left_labels & right_labels)


def _build_confirmation_tasks(
    entities: list[dict[str, Any]],
    *,
    dossier_id: int | None,
    decision_index: dict[str, str],
) -> list[dict[str, Any]]:
    def task_entity_payload(entity: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": entity["id"],
            "label": entity["canonical_label"],
            "entity_type": entity["entity_type"],
            "role": _normalize_text(entity.get("role")),
            "aliases": _sorted_unique_strings(entity.get("aliases") or []),
            "source_entity_ids": _sorted_unique_strings(entity.get("source_entity_ids") or []),
            "source_conversation_ids": _sorted_unique_ints(entity.get("source_conversation_ids") or []),
            "dossier_id": dossier_id,
        }

    tasks: list[dict[str, Any]] = []
    for index, left in enumerate(entities):
        for right in entities[index + 1 :]:
            left_conversations = set(left.get("source_conversation_ids") or [])
            right_conversations = set(right.get("source_conversation_ids") or [])
            if left_conversations & right_conversations:
                continue
            if not _candidate_entities_are_same_label(left, right):
                continue
            if not can_create_identity_candidate(left, right):
                continue
            task_key = _task_key(left["id"], right["id"])
            if decision_index.get(task_key) in {"same", "not_same"}:
                continue
            tasks.append(
                {
                    "id": task_key,
                    "task_key": task_key,
                    "type": "cross_conversation_same_entity",
                    "task_type": "cross_conversation_same_entity",
                    "status": "pending",
                    "dossier_id": dossier_id,
                    "source_entity": task_entity_payload(left),
                    "target_entity": task_entity_payload(right),
                    "source_conversation_ids": _sorted_unique_ints(
                        list(left.get("source_conversation_ids") or [])
                        + list(right.get("source_conversation_ids") or [])
                    ),
                    "reason": "不同会话出现相同或重叠主体称呼，需要用户确认是否为同一案卷实体。",
                    "confidence": "medium",
                    "allowed_actions": ["confirm", "reject", "defer"],
                    "requires_user_confirmation": True,
                }
            )
    return tasks


def build_dossier_entity_state(
    *,
    conversation_entity_states: list[dict[str, Any]] | None,
    dossier_id: int | None = None,
    user_id: int | None = None,
    resolution_decisions: list[dict[str, Any]] | None = None,
    conversation_summaries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    states = [
        state
        for state in conversation_entity_states or []
        if isinstance(state, dict) and state.get("should_build", True)
    ]
    title_lookup = _conversation_title_lookup(conversation_summaries)
    base_entities: list[dict[str, Any]] = []
    raw_relations: list[dict[str, Any]] = []
    for state in states:
        conversation_id = _positive_int(state.get("conversation_id"))
        for entity in state.get("entities") if isinstance(state.get("entities"), list) else []:
            if not isinstance(entity, dict):
                continue
            normalized = _entity_from_conversation_entity(
                entity,
                conversation_id=conversation_id,
                dossier_id=dossier_id,
            )
            if normalized is not None:
                base_entities.append(normalized)
        for relation in state.get("relations") if isinstance(state.get("relations"), list) else []:
            if not isinstance(relation, dict):
                continue
            raw_relation = dict(relation)
            raw_relation["source_entity_id"] = _normalize_text(relation.get("source"))
            raw_relation["target_entity_id"] = _normalize_text(relation.get("target"))
            raw_relations.append(raw_relation)

    decision_index = _same_decision_index(resolution_decisions)
    union = _UnionFind([entity["id"] for entity in base_entities])
    for index, left in enumerate(base_entities):
        for right in base_entities[index + 1 :]:
            base_task_key = _task_key(left["id"], right["id"])
            canonical_task_key = _task_key(
                _singleton_canonical_id(left, dossier_id=dossier_id),
                _singleton_canonical_id(right, dossier_id=dossier_id),
            )
            if decision_index.get(base_task_key) != "same" and decision_index.get(canonical_task_key) != "same":
                continue
            blockers = identity_merge_blockers(
                left,
                right,
                requires_user_confirmation=True,
                user_confirmed=True,
                source_dossier_id=dossier_id,
                target_dossier_id=dossier_id,
            )
            if blockers:
                continue
            union.union(left["id"], right["id"])

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entity in base_entities:
        grouped[union.find(entity["id"])].append(entity)

    canonical_entities = [
        _merge_entity_group(
            group,
            dossier_id=dossier_id,
            title_lookup=title_lookup,
            relations=raw_relations,
        )
        for group in grouped.values()
    ]
    canonical_entities.sort(key=lambda item: (str(item.get("entity_type")), str(item.get("canonical_label")), str(item.get("id"))))

    source_entity_to_dossier_entity: dict[str, str] = {}
    for entity in canonical_entities:
        for source_entity_id in entity.get("source_entity_ids", []):
            source_entity_to_dossier_entity[_normalize_text(source_entity_id)] = entity["id"]
    relations = _state_relations(states, source_entity_to_dossier_entity)
    confirmation_tasks = _build_confirmation_tasks(
        canonical_entities,
        dossier_id=dossier_id,
        decision_index=decision_index,
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE,
        "provider": PROVIDER,
        "model": MODEL,
        "dossier_id": dossier_id,
        "user_id": user_id,
        "entities": canonical_entities,
        "relations": relations,
        "confirmation_tasks": confirmation_tasks,
        "stats": {
            "conversation_entity_states": len(states),
            "canonical_entities": len(canonical_entities),
            "cross_conversation_candidates": len(confirmation_tasks),
            "relations": len(relations),
        },
        "trace": {
            "mode": "agentic_dossier_entity_state",
            "provider": PROVIDER,
            "model": MODEL,
            "steps": [
                "read_conversation_entity_state",
                "build_canonical_entities",
                "apply_scoped_resolution_decisions",
                "plan_cross_conversation_confirmation_tasks",
                "build_dossier_entity_state",
            ],
            "warnings": [],
        },
    }


def coordination_graph_from_dossier_entity_state(
    dossier_entity_state: dict[str, Any] | None,
) -> dict[str, Any]:
    state = dossier_entity_state if isinstance(dossier_entity_state, dict) else {}
    entities = []
    for entity in state.get("entities") if isinstance(state.get("entities"), list) else []:
        if not isinstance(entity, dict):
            continue
        entities.append(
            {
                "id": _normalize_text(entity.get("id")),
                "label": _normalize_text(entity.get("canonical_label") or entity.get("label")),
                "display_label": _normalize_text(entity.get("canonical_label") or entity.get("label")),
                "entity_type": _normalize_text(entity.get("entity_type")) or "unknown",
                "role": _normalize_text(entity.get("role")),
                "status": _normalize_text(entity.get("status")) or "canonical",
                "aliases": _sorted_unique_strings(entity.get("aliases") or []),
                "source_labels": _sorted_unique_strings(entity.get("source_labels") or []),
                "source_conversation_ids": _sorted_unique_ints(entity.get("source_conversation_ids") or []),
                "source_conversations": entity.get("source_conversations") if isinstance(entity.get("source_conversations"), list) else [],
                "best_conversation_id": entity.get("best_conversation_id"),
                "best_conversation_reason": _normalize_text(entity.get("best_conversation_reason")),
                "raw_node_ids": _sorted_unique_strings(
                    [
                        mention.get("raw_node_id")
                        for mention in entity.get("mentions", [])
                        if isinstance(mention, dict)
                    ]
                ),
                "mention_ids": _sorted_unique_strings(entity.get("mention_ids") or []),
                "source_entity_ids": _sorted_unique_strings(entity.get("source_entity_ids") or []),
                "suggested_action": "view",
            }
        )

    edges = []
    for relation in state.get("relations") if isinstance(state.get("relations"), list) else []:
        if not isinstance(relation, dict):
            continue
        edges.append(
            {
                "id": _normalize_text(relation.get("id")) or f"edge:{len(edges)}",
                "source": _normalize_text(relation.get("source")),
                "target": _normalize_text(relation.get("target")),
                "label": _normalize_text(relation.get("label")) or "关联",
                "relation_type": _normalize_text(relation.get("relation_type")) or "case_relation",
                "source_conversation_ids": _sorted_unique_ints(relation.get("source_conversation_ids") or []),
                "evidence": relation.get("evidence") if isinstance(relation.get("evidence"), list) else [],
                "confidence": _normalize_text(relation.get("confidence")) or "medium",
            }
        )

    tasks = state.get("confirmation_tasks") if isinstance(state.get("confirmation_tasks"), list) else []
    stats = state.get("stats") if isinstance(state.get("stats"), dict) else {}
    trace = state.get("trace") if isinstance(state.get("trace"), dict) else {}
    return {
        "entities": entities,
        "edges": edges,
        "tasks": tasks,
        "stats": {
            "bound_conversations": stats.get("conversation_entity_states", 0),
            "connected_entities": len(entities),
            "pending_identity": len(tasks),
            "pending_naming": 0,
            "isolated_anchors": 0,
            "canonical_entities": len(entities),
            "relations": len(edges),
        },
        "trace": {
            "mode": trace.get("mode") or "agentic_dossier_entity_state",
            "provider": trace.get("provider") or PROVIDER,
            "model": trace.get("model") or MODEL,
            "fallback_reason": trace.get("fallback_reason"),
            "steps": trace.get("steps") if isinstance(trace.get("steps"), list) else [],
            "warnings": trace.get("warnings") if isinstance(trace.get("warnings"), list) else [],
        },
    }


__all__ = [
    "build_dossier_entity_state",
    "coordination_graph_from_dossier_entity_state",
]
