from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.services.conversation_store import (
    build_dossier_participant_candidates,
    build_dossier_participant_lookup,
    get_dossier_for_user,
    is_generic_participant_label,
    list_dossier_conversations,
    list_dossier_entity_resolution_decisions,
    list_dossier_participant_mentions,
    list_dossier_participants,
    serialize_dossier,
    serialize_dossier_entity_resolution_decision,
    serialize_dossier_participant,
    serialize_dossier_participant_mention,
)
from app.services.dossier_display_graph import build_dossier_display_graph
from app.services.dossier_graph import get_dossier_graph_payload
from app.services.dossier_intelligence import _build_party_naming_task

_TRACE_MODE = "rules"
_TRACE_PROVIDER = "rules"
_TRACE_MODEL = "dossier-coordination-rules-v1"
_TRACE_FALLBACK_REASON = "llm_not_enabled_in_responsible_entity_graph"
_TRACE_STEPS = [
    "load_raw_dossier_graph",
    "load_participant_mappings",
    "filter_responsible_entities",
    "derive_subject_relations",
    "attach_evidence",
    "build_confirmation_tasks",
]

_SELF_ANCHOR_LABELS = {
    "当事人",
    "我",
    "本人",
    "自己",
    "用户",
    "我们",
    "咱们",
}
_SELF_ANCHOR_LABELS_NORMALIZED = {label.lower() for label in _SELF_ANCHOR_LABELS}

_PERSON_TYPES = {
    "person",
    "natural_person",
    "person_or_role",
    "human",
    "自然人",
    "人员",
}
_ORGANIZATION_TYPES = {
    "organization",
    "company",
    "employer",
    "organization_entity",
    "unit",
    "institution",
    "组织",
    "公司",
    "单位",
    "用人单位",
}
_AUTHORITY_TYPES = {
    "authority",
    "agency",
    "institutional_authority",
    "机构",
    "机关",
    "部门",
}
_PARTY_TYPES = {
    "party",
    "subject",
    "claimant",
    "respondent",
    "申请人",
    "被申请人",
    "主体",
}
_ROLE_TYPES = {
    "role",
    "representative",
    "agent",
    "hr",
    "manager",
    "supervisor",
    "legal_representative",
    "actual_employer",
    "payroll_entity",
    "social_insurance_payer",
    "工资发放主体",
    "社保缴纳主体",
    "实际用工单位",
    "法定代表人",
    "人事",
    "经理",
    "角色",
    "代表",
    "代理人",
}
_RESPONSIBLE_ENTITY_TYPE_HINTS = {
    *{value.lower() for value in _PERSON_TYPES},
    *{value.lower() for value in _ORGANIZATION_TYPES},
    *{value.lower() for value in _AUTHORITY_TYPES},
    *{value.lower() for value in _PARTY_TYPES},
    *{value.lower() for value in _ROLE_TYPES},
    "unknown",
}

_FACT_NODE_TYPES = {
    "event",
    "fact",
    "evidence",
    "document",
    "contract",
    "amount",
    "money",
    "salary",
    "time",
    "date",
    "law",
    "statute",
    "claim",
    "procedure",
    "issue",
    "action",
    "benefit",
    "social_insurance",
    "notice",
    "relationship_event",
    "事件",
    "事实",
    "证据",
    "文书",
    "合同",
    "劳动合同",
    "金额",
    "工资",
    "月薪",
    "时间",
    "日期",
    "法条",
    "诉求",
    "程序",
    "行为",
    "问题",
    "社保",
    "确认书",
    "通知",
    "人事通知",
    "补缴社保",
    "未缴纳社保",
}
_FACT_NODE_TYPES_NORMALIZED = {value.lower() for value in _FACT_NODE_TYPES}

_FACT_LABEL_HINTS = {
    "劳动合同",
    "合同",
    "社保",
    "社会保险",
    "月薪",
    "工资",
    "薪资",
    "金额",
    "确认书",
    "通知",
    "人事通知",
    "补缴社保",
    "未缴纳社保",
    "证据",
    "证明",
    "日期",
    "时间",
}
_ROLE_LABEL_HINTS = {
    "hr",
    "人事",
    "经理",
    "法定代表人",
    "实际用工单位",
    "工资发放主体",
    "社保缴纳主体",
    "代表",
    "代理人",
}
_ORGANIZATION_LABEL_HINTS = {
    "公司",
    "单位",
    "集团",
    "有限",
    "中心",
    "学校",
    "医院",
    "银行",
    "事务所",
    "厂",
    "局",
    "院",
}
_AUTHORITY_LABEL_HINTS = {
    "仲裁委",
    "委员会",
    "法院",
    "人社局",
    "社保局",
    "劳动局",
}
_EMPLOYMENT_HINTS = {
    "劳动",
    "用工",
    "入职",
    "在职",
    "就职",
    "工作",
    "雇佣",
    "签约",
    "合同",
    "协议",
}
_SOCIAL_INSURANCE_HINTS = {
    "社保",
    "社会保险",
    "补缴",
    "未缴纳",
    "五险",
}
_WAGE_HINTS = {
    "工资",
    "薪资",
    "月薪",
    "薪酬",
    "发薪",
}
_NOTICE_HINTS = {
    "通知",
    "告知",
    "要求",
    "确认",
    "邮件",
    "微信",
}
_TERMINATION_HINTS = {
    "解除",
    "辞退",
    "开除",
    "离职",
}
_AFFILIATION_HINTS = {
    "人事",
    "hr",
    "经理",
    "法定代表人",
    "代表",
    "代理",
}
_PERSON_NAME_EXCLUDES = {
    "公司",
    "单位",
    "合同",
    "通知",
    "社保",
    "工资",
    "确认书",
}
_TYPE_DISPLAY_LABELS = {
    "person": "person",
    "organization": "organization",
    "role": "role",
    "authority": "authority",
    "party": "party",
    "unknown": "unknown",
}
_CONFIDENCE_PRIORITY = {"low": 1, "medium": 2, "high": 3}


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


def _contains_any(text: str, hints: set[str]) -> bool:
    normalized = _normalize_key(text)
    return any(hint.lower() in normalized for hint in hints)


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
        node.get("name"),
        node.get("title"),
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


def _raw_edge_id(edge: dict[str, Any]) -> str:
    return _normalize_text(edge.get("id")) or _normalize_text(
        f"{edge.get('source')}->{edge.get('target')}:{edge.get('label') or edge.get('type')}"
    )


def _raw_edge_label(edge: dict[str, Any]) -> str:
    label = _normalize_text(edge.get("label") or edge.get("type"))
    if label:
        return label
    properties = edge.get("properties")
    if isinstance(properties, dict):
        return _normalize_text(properties.get("label") or properties.get("relation"))
    return ""


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


def _looks_like_role_label(label: str) -> bool:
    return _contains_any(label, _ROLE_LABEL_HINTS)


def _looks_like_organization_label(label: str) -> bool:
    return _contains_any(label, _ORGANIZATION_LABEL_HINTS)


def _looks_like_authority_label(label: str) -> bool:
    return _contains_any(label, _AUTHORITY_LABEL_HINTS)


def _looks_like_person_name(label: str) -> bool:
    normalized = _normalize_text(label)
    if not normalized or any(token in normalized for token in _PERSON_NAME_EXCLUDES):
        return False
    if len(normalized) in {2, 3, 4} and all("\u4e00" <= char <= "\u9fff" for char in normalized):
        return True
    return False


def _looks_like_self_anchor(label: str) -> bool:
    return _normalize_key(label) in _SELF_ANCHOR_LABELS_NORMALIZED


def _is_fact_object(label: str, raw_type: str) -> bool:
    normalized_type = _normalize_key(raw_type)
    if normalized_type in _FACT_NODE_TYPES_NORMALIZED:
        return True
    return _contains_any(label, _FACT_LABEL_HINTS)


def _coordination_entity_type(label: str, raw_type: str) -> str:
    normalized_label = _normalize_text(label)
    normalized_type = _normalize_key(raw_type)

    if _looks_like_self_anchor(normalized_label) or normalized_type in {value.lower() for value in _PARTY_TYPES}:
        return "party"
    if normalized_type in {value.lower() for value in _ROLE_TYPES} or _looks_like_role_label(normalized_label):
        return "role"
    if normalized_type in {value.lower() for value in _ORGANIZATION_TYPES} or _looks_like_organization_label(normalized_label):
        return "organization"
    if normalized_type in {value.lower() for value in _AUTHORITY_TYPES} or _looks_like_authority_label(normalized_label):
        return "authority"
    if normalized_type in {value.lower() for value in _PERSON_TYPES} or _looks_like_person_name(normalized_label):
        return "person"
    if normalized_type in {value.lower() for value in _PARTY_TYPES}:
        return "party"
    return "unknown"


def _is_responsible_entity_candidate(label: str, raw_type: str) -> bool:
    normalized_label = _normalize_text(label)
    normalized_type = _normalize_key(raw_type)
    if not normalized_label:
        return False
    if _is_fact_object(normalized_label, normalized_type):
        return False
    if normalized_type in _RESPONSIBLE_ENTITY_TYPE_HINTS and normalized_type != "unknown":
        return True
    entity_type = _coordination_entity_type(normalized_label, normalized_type)
    return entity_type in {"person", "organization", "role", "authority", "party"}


def _graph_participant_indexes(raw_graph_payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = raw_graph_payload if isinstance(raw_graph_payload, dict) else {}
    participants = payload.get("confirmed_participants")
    participant_lookup = {
        _positive_int(participant.get("id")): participant
        for participant in participants if isinstance(participant, dict)
    } if isinstance(participants, list) else {}

    mappings = payload.get("participant_mappings")
    raw_node_mapping_lookup: dict[str, dict[str, Any]] = {}
    if isinstance(mappings, list):
        for mapping in mappings:
            if not isinstance(mapping, dict):
                continue
            raw_node_id = _normalize_text(mapping.get("graph_node_id") or mapping.get("id"))
            participant_id = _positive_int(mapping.get("participant_id"))
            if not raw_node_id or participant_id <= 0:
                continue
            participant = participant_lookup.get(participant_id, {})
            raw_node_mapping_lookup[raw_node_id] = {
                "participant_id": participant_id,
                "display_label": _normalize_text(
                    mapping.get("participant_label")
                    or participant.get("label")
                    or participant.get("display_label")
                ),
                "entity_type": _normalize_text(
                    participant.get("entity_type") or mapping.get("participant_entity_type")
                ) or "unknown",
            }

    return {
        "participant_lookup": participant_lookup,
        "raw_node_mapping_lookup": raw_node_mapping_lookup,
    }


def _raw_indexes(raw_graph_payload: dict[str, Any] | None) -> dict[str, Any]:
    nodes = _graph_nodes(raw_graph_payload)
    edges = _graph_edges(raw_graph_payload)
    node_lookup: dict[str, dict[str, Any]] = {}
    node_conversation_lookup: dict[str, int] = {}
    incident_edges_by_node: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for node in nodes:
        raw_node_id = _raw_node_id(node)
        if not raw_node_id:
            continue
        node_lookup[raw_node_id] = node
        node_conversation_lookup[raw_node_id] = _raw_node_conversation_id(node)

    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source_raw_id = _normalize_text(edge.get("source"))
        target_raw_id = _normalize_text(edge.get("target"))
        if source_raw_id:
            incident_edges_by_node[source_raw_id].append(edge)
        if target_raw_id and target_raw_id != source_raw_id:
            incident_edges_by_node[target_raw_id].append(edge)

    return {
        "nodes": nodes,
        "edges": edges,
        "node_lookup": node_lookup,
        "node_conversation_lookup": node_conversation_lookup,
        "incident_edges_by_node": incident_edges_by_node,
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
    source_label = _candidate_node_label(source_node)
    target_label = _candidate_node_label(target_node)
    source_type = _coordination_entity_type(
        source_label,
        str(source_node.get("graph_node_type") or source_node.get("entity_type") or ""),
    )
    target_type = _coordination_entity_type(
        target_label,
        str(target_node.get("graph_node_type") or target_node.get("entity_type") or ""),
    )
    if source_type == target_type:
        return source_type
    if source_type != "unknown":
        return source_type
    if target_type != "unknown":
        return target_type
    return "unknown"


def _entity_display_label(label: str) -> str:
    return _normalize_text(label)


def _entity_payload(
    *,
    entity_id: str,
    label: str,
    status: str,
    entity_type: str,
    source_conversation_ids: list[int],
    raw_node_ids: list[str],
    raw_labels: list[str],
    participant_id: int | None,
    best_conversation_id: int | None,
    best_reason: str,
    edge_count_map: dict[str, int],
    suggested_action: str,
) -> dict[str, Any]:
    display_label = _entity_display_label(label)
    normalized_entity_type = entity_type if entity_type in _TYPE_DISPLAY_LABELS else "unknown"
    normalized_labels = _sorted_unique_strings(raw_labels)
    return {
        "id": entity_id,
        "label": display_label,
        "display_label": display_label,
        "status": status,
        "entity_type": normalized_entity_type,
        "source_conversation_ids": _sorted_unique_ints(source_conversation_ids),
        "raw_node_ids": _sorted_unique_strings(raw_node_ids),
        "raw_labels": normalized_labels,
        "source_labels": normalized_labels,
        "participant_id": participant_id,
        "best_conversation_id": best_conversation_id,
        "best_conversation_reason": best_reason,
        "edge_count_by_conversation": edge_count_map,
        "suggested_action": suggested_action,
    }


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
        raw_labels = _raw_labels_from_node(node)
        label = _normalize_text(node.get("display_label")) or (raw_labels[0] if raw_labels else "")
        entity_type = _coordination_entity_type(label, str(node.get("entity_type") or ""))
        if not _is_responsible_entity_candidate(label, entity_type):
            continue

        best_conversation_id, best_reason, edge_count_map = _best_conversation_for_entity(
            raw_node_ids=raw_node_ids,
            source_conversation_ids=source_conversation_ids,
            raw_indexes=raw_indexes,
        )
        participant_id = _positive_int(node.get("participant_id")) or None
        entity_id = _normalize_text(node.get("id")) or f"participant:{participant_id or len(entities) + 1}"

        entities.append(
            _entity_payload(
                entity_id=entity_id,
                label=label,
                status="confirmed",
                entity_type=entity_type,
                source_conversation_ids=source_conversation_ids,
                raw_node_ids=raw_node_ids,
                raw_labels=raw_labels,
                participant_id=participant_id,
                best_conversation_id=best_conversation_id,
                best_reason=best_reason,
                edge_count_map=edge_count_map,
                suggested_action="view",
            )
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
        candidate_task_type = _normalize_text(candidate.get("task_type"))
        if candidate_task_type not in {"identity", "cross_conversation_entity_resolution"}:
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
        raw_labels = _sorted_unique_strings(
            list(candidate.get("raw_labels") or [])
            + [
                _candidate_node_label(source_node),
                _candidate_node_label(target_node),
            ]
        )
        label = _normalize_text(candidate.get("suggested_label")) or (
            raw_labels[0] if raw_labels else f"pending-identity-{index}"
        )
        entity_type = _pending_identity_entity_type(source_node, target_node)
        if not _is_responsible_entity_candidate(label, entity_type):
            continue

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
            _entity_payload(
                entity_id=entity_id,
                label=label,
                status="pending_identity",
                entity_type=entity_type,
                source_conversation_ids=source_conversation_ids,
                raw_node_ids=raw_node_ids,
                raw_labels=raw_labels,
                participant_id=participant_id,
                best_conversation_id=best_conversation_id,
                best_reason=best_reason,
                edge_count_map=edge_count_map,
                suggested_action="confirm_identity",
            )
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
        raw_labels = _sorted_unique_strings(
            list(evidence.get("raw_labels") or []) + [_normalize_text(task.get("current_label"))]
        )
        label = _normalize_text(task.get("current_label")) or (raw_labels[0] if raw_labels else "当事人")
        best_conversation_id, best_reason, edge_count_map = _best_conversation_for_entity(
            raw_node_ids=raw_node_ids,
            source_conversation_ids=source_conversation_ids,
            raw_indexes=raw_indexes,
        )
        raw_node_suffix = ":".join(raw_node_ids) if raw_node_ids else str(index)
        task_id = f"party-naming:{raw_node_suffix}"
        entity_id = f"pending-naming:{raw_node_suffix}"

        entities.append(
            _entity_payload(
                entity_id=entity_id,
                label=label,
                status="pending_naming",
                entity_type="party",
                source_conversation_ids=source_conversation_ids,
                raw_node_ids=raw_node_ids,
                raw_labels=raw_labels,
                participant_id=None,
                best_conversation_id=best_conversation_id,
                best_reason=best_reason,
                edge_count_map=edge_count_map,
                suggested_action="name_party",
            )
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

    if _looks_like_self_anchor(label):
        rank = 0
    elif entity_type == "organization":
        rank = 1
    elif entity_type == "person":
        rank = 2
    elif entity_type == "role":
        rank = 3
    elif entity_type == "party":
        rank = 4
    elif entity_type == "authority":
        rank = 5
    else:
        rank = 6

    generic_penalty = 1 if is_generic_participant_label(label) else 0
    return (rank, generic_penalty, -len(_normalize_text(label)), raw_node_id)


def _isolated_anchor_group_key(
    *,
    node: dict[str, Any],
    mapping: dict[str, Any] | None,
    entity_type: str,
) -> str:
    participant_id = _positive_int(mapping.get("participant_id")) if isinstance(mapping, dict) else 0
    if participant_id > 0:
        return f"participant:{participant_id}"

    conversation_id = _raw_node_conversation_id(node)
    label = _normalize_key(
        (_normalize_text(mapping.get("display_label")) if isinstance(mapping, dict) else "")
        or _raw_node_label(node)
    )
    return f"isolated-anchor:{conversation_id}:{entity_type}:{label or _raw_node_id(node)}"


def _build_isolated_anchor_entities(
    *,
    raw_graph_payload: dict[str, Any] | None,
    bound_conversation_ids: list[int] | None,
    raw_indexes: dict[str, Any],
    participant_indexes: dict[str, Any],
    covered_conversation_ids: set[int],
    covered_raw_node_ids: set[str],
) -> list[dict[str, Any]]:
    nodes = _graph_nodes(raw_graph_payload)
    mapping_lookup = participant_indexes.get("raw_node_mapping_lookup", {})
    relevant_conversation_ids = set(_sorted_unique_ints(bound_conversation_ids or []))
    if not relevant_conversation_ids:
        relevant_conversation_ids.update(
            _sorted_unique_ints([_raw_node_conversation_id(node) for node in nodes])
        )

    grouped_anchors: dict[str, dict[str, Any]] = {}
    for node in nodes:
        conversation_id = _raw_node_conversation_id(node)
        raw_node_id = _raw_node_id(node)
        raw_label = _raw_node_label(node)
        raw_type = _raw_node_type(node)
        if (
            conversation_id <= 0
            or relevant_conversation_ids
            and conversation_id not in relevant_conversation_ids
            or not raw_node_id
            or raw_node_id in covered_raw_node_ids
            or not _is_responsible_entity_candidate(raw_label, raw_type)
        ):
            continue

        mapping = mapping_lookup.get(raw_node_id) if isinstance(mapping_lookup, dict) else None
        label = _normalize_text(mapping.get("display_label")) if isinstance(mapping, dict) else ""
        label = label or raw_label or raw_node_id
        entity_type = (
            _coordination_entity_type(label, str(mapping.get("entity_type")))
            if isinstance(mapping, dict) and mapping.get("entity_type")
            else _coordination_entity_type(label, raw_type)
        )
        group_key = _isolated_anchor_group_key(
            node=node,
            mapping=mapping if isinstance(mapping, dict) else None,
            entity_type=entity_type,
        )
        existing = grouped_anchors.get(group_key)
        candidate_payload = {
            "entity_id": group_key,
            "label": label,
            "entity_type": entity_type,
            "participant_id": _positive_int(mapping.get("participant_id")) or None if isinstance(mapping, dict) else None,
            "source_conversation_ids": {conversation_id},
            "raw_node_ids": {raw_node_id},
            "raw_labels": {raw_label or label},
            "priority": _anchor_priority(node),
        }
        if existing is None:
            grouped_anchors[group_key] = candidate_payload
            continue

        existing["source_conversation_ids"].add(conversation_id)
        existing["raw_node_ids"].add(raw_node_id)
        existing["raw_labels"].add(raw_label or label)
        if candidate_payload["priority"] < existing["priority"]:
            existing["priority"] = candidate_payload["priority"]
            existing["label"] = label
            existing["entity_type"] = entity_type
            existing["participant_id"] = candidate_payload["participant_id"]

    isolated_entities: list[dict[str, Any]] = []
    for group_key, anchor in grouped_anchors.items():
        source_conversation_ids = _sorted_unique_ints(list(anchor["source_conversation_ids"]))
        raw_node_ids = _sorted_unique_strings(list(anchor["raw_node_ids"]))
        raw_labels = _sorted_unique_strings(list(anchor["raw_labels"]))
        best_conversation_id, best_reason, edge_count_map = _best_conversation_for_entity(
            raw_node_ids=raw_node_ids,
            source_conversation_ids=source_conversation_ids,
            raw_indexes=raw_indexes,
        )
        if not edge_count_map and len(source_conversation_ids) == 1:
            best_reason = "当前仅来源于该会话，作为孤立主体锚点展示"
        elif not edge_count_map:
            best_reason = "当前仅作为案卷中的补充责任主体展示"

        isolated_entities.append(
            _entity_payload(
                entity_id=group_key,
                label=str(anchor["label"]),
                status="isolated_anchor",
                entity_type=str(anchor["entity_type"]),
                source_conversation_ids=source_conversation_ids,
                raw_node_ids=raw_node_ids,
                raw_labels=raw_labels,
                participant_id=anchor["participant_id"],
                best_conversation_id=best_conversation_id or (source_conversation_ids[0] if source_conversation_ids else None),
                best_reason=best_reason,
                edge_count_map=edge_count_map,
                suggested_action="review_anchor",
            )
        )

    isolated_entities.sort(
        key=lambda entity: (
            0 if entity.get("participant_id") else 1,
            _sorted_unique_ints(entity.get("source_conversation_ids") or [0])[0]
            if entity.get("source_conversation_ids")
            else 0,
            str(entity.get("label") or ""),
        )
    )
    return isolated_entities


def _build_responsible_entity_graph_agent_input(
    *,
    raw_graph_payload: dict[str, Any] | None,
    display_graph: dict[str, Any] | None,
    participant_candidates: list[dict[str, Any]] | None,
    intelligence_payload: dict[str, Any] | None,
    bound_conversation_ids: list[int] | None,
) -> dict[str, Any]:
    return {
        "raw_graph_payload": raw_graph_payload or {},
        "display_graph": display_graph or {},
        "participant_candidates": participant_candidates or [],
        "intelligence_payload": intelligence_payload or {},
        "bound_conversation_ids": _sorted_unique_ints(bound_conversation_ids or []),
        "agent_prompt": {
            "system": "构建案卷责任主体连接图，只保留责任主体节点，把事实对象降级为边证据。",
            "mode": _TRACE_MODE,
            "provider": _TRACE_PROVIDER,
            "model": _TRACE_MODEL,
        },
    }


def _entity_is_person_like(entity: dict[str, Any]) -> bool:
    return str(entity.get("entity_type") or "").strip() in {"person", "party"}


def _entity_is_role_like(entity: dict[str, Any]) -> bool:
    return str(entity.get("entity_type") or "").strip() == "role"


def _entity_is_org_like(entity: dict[str, Any]) -> bool:
    return str(entity.get("entity_type") or "").strip() in {"organization", "authority"}


def _relation_summary_text(
    *,
    source_label: str,
    target_label: str,
    relation_label: str,
    fact_label: str = "",
) -> str:
    if fact_label:
        return f"“{source_label}”与“{target_label}”共同关联“{fact_label}”，形成“{relation_label}”线索。"
    return f"“{source_label}”与“{target_label}”存在“{relation_label}”关系。"


def _classify_subject_relation(
    *,
    source_entity: dict[str, Any],
    target_entity: dict[str, Any],
    direct_relation_label: str = "",
    fact_label: str = "",
    supporting_text: str = "",
) -> tuple[str, str, str]:
    source_label = _normalize_text(source_entity.get("label"))
    target_label = _normalize_text(target_entity.get("label"))
    combined_text = " ".join(
        part for part in [source_label, target_label, direct_relation_label, fact_label, supporting_text] if part
    )

    if (
        (_entity_is_role_like(source_entity) and _entity_is_org_like(target_entity))
        or (_entity_is_org_like(source_entity) and _entity_is_role_like(target_entity))
        or (
            _contains_any(source_label, _AFFILIATION_HINTS)
            and target_label
            and target_label in source_label
        )
        or (
            _contains_any(target_label, _AFFILIATION_HINTS)
            and source_label
            and source_label in target_label
        )
    ):
        confidence = "high" if target_label and target_label in source_label or source_label and source_label in target_label else "medium"
        return ("代表/隶属", "affiliation", confidence)

    if (_entity_is_person_like(source_entity) and _entity_is_org_like(target_entity)) or (
        _entity_is_org_like(source_entity) and _entity_is_person_like(target_entity)
    ):
        if _contains_any(combined_text, _SOCIAL_INSURANCE_HINTS):
            return ("劳动关系/社保争议", "employment_dispute", "high" if fact_label else "medium")
        if _contains_any(combined_text, _WAGE_HINTS):
            return ("劳动关系/工资争议", "employment_dispute", "medium")
        if _contains_any(combined_text, _TERMINATION_HINTS):
            return ("劳动关系/解除争议", "employment_dispute", "medium")
        if _contains_any(combined_text, _EMPLOYMENT_HINTS):
            return ("劳动关系/签约关系", "employment_relation", "high" if fact_label else "medium")
        return ("劳动关系", "employment_relation", "medium")

    if (
        (_entity_is_role_like(source_entity) or _entity_is_org_like(source_entity))
        and _entity_is_person_like(target_entity)
        and _contains_any(combined_text, _NOTICE_HINTS | _TERMINATION_HINTS)
    ):
        return ("通知/管理关系", "management", "medium")

    if direct_relation_label:
        return (direct_relation_label, "direct", "high")

    return ("同案关联", "same_case", "low")


def _normalize_relation_orientation(
    *,
    source_entity: dict[str, Any],
    target_entity: dict[str, Any],
    relation_label: str,
    relation_type: str,
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    if relation_type in {"employment_relation", "employment_dispute"}:
        if _entity_is_org_like(source_entity) and _entity_is_person_like(target_entity):
            return target_entity, source_entity, relation_label, relation_type

    if relation_type == "affiliation":
        if _entity_is_org_like(source_entity) and _entity_is_role_like(target_entity):
            return target_entity, source_entity, relation_label, relation_type
        if _entity_is_authority_like(source_entity) and _entity_is_role_like(target_entity):
            return target_entity, source_entity, relation_label, relation_type

    if relation_type == "management":
        if _entity_is_person_like(source_entity) and (_entity_is_role_like(target_entity) or _entity_is_org_like(target_entity)):
            return target_entity, source_entity, relation_label, relation_type

    if relation_type == "same_case":
        source_id = _normalize_text(source_entity.get("id"))
        target_id = _normalize_text(target_entity.get("id"))
        if target_id and source_id and target_id < source_id:
            return target_entity, source_entity, relation_label, relation_type

    return source_entity, target_entity, relation_label, relation_type


def _entity_is_authority_like(entity: dict[str, Any]) -> bool:
    return str(entity.get("entity_type") or "").strip() == "authority"


def _empty_edge(
    *,
    source_entity: dict[str, Any],
    target_entity: dict[str, Any],
    relation_label: str,
    relation_type: str,
    confidence: str,
) -> dict[str, Any]:
    source_id = _normalize_text(source_entity.get("id"))
    target_id = _normalize_text(target_entity.get("id"))
    return {
        "id": f"{relation_type}:{source_id}:{target_id}:{_normalize_key(relation_label or relation_type)}",
        "source": source_id,
        "target": target_id,
        "label": relation_label or relation_type,
        "relation_type": relation_type,
        "source_conversation_ids": [],
        "evidence": [],
        "confidence": confidence if confidence in _CONFIDENCE_PRIORITY else "low",
    }


def _append_edge_evidence(edge: dict[str, Any], evidence: dict[str, Any]) -> None:
    conversation_id = _positive_int(evidence.get("conversation_id"))
    raw_node_ids = _sorted_unique_strings(evidence.get("raw_node_ids") or [])
    raw_edge_ids = _sorted_unique_strings(evidence.get("raw_edge_ids") or [])
    summary = _normalize_text(evidence.get("summary"))
    evidence_key = (
        conversation_id,
        tuple(raw_node_ids),
        tuple(raw_edge_ids),
        summary,
    )
    existing_keys = {
        (
            _positive_int(item.get("conversation_id")),
            tuple(_sorted_unique_strings(item.get("raw_node_ids") or [])),
            tuple(_sorted_unique_strings(item.get("raw_edge_ids") or [])),
            _normalize_text(item.get("summary")),
        )
        for item in edge.get("evidence", [])
        if isinstance(item, dict)
    }
    if evidence_key in existing_keys:
        return

    edge.setdefault("evidence", []).append(
        {
            "conversation_id": conversation_id or None,
            "summary": summary,
            "raw_node_ids": raw_node_ids,
            "raw_edge_ids": raw_edge_ids,
        }
    )
    edge["source_conversation_ids"] = _sorted_unique_ints(
        list(edge.get("source_conversation_ids") or []) + ([conversation_id] if conversation_id > 0 else [])
    )


def _merge_confidence(existing_confidence: str, new_confidence: str) -> str:
    if _CONFIDENCE_PRIORITY.get(new_confidence, 0) > _CONFIDENCE_PRIORITY.get(existing_confidence, 0):
        return new_confidence
    return existing_confidence


def _add_relation_edge(
    edge_registry: dict[tuple[str, str, str, str], dict[str, Any]],
    *,
    source_entity: dict[str, Any],
    target_entity: dict[str, Any],
    relation_label: str,
    relation_type: str,
    confidence: str,
    evidence: dict[str, Any],
) -> None:
    normalized_source, normalized_target, normalized_label, normalized_relation_type = _normalize_relation_orientation(
        source_entity=source_entity,
        target_entity=target_entity,
        relation_label=relation_label,
        relation_type=relation_type,
    )
    source_id = _normalize_text(normalized_source.get("id"))
    target_id = _normalize_text(normalized_target.get("id"))
    if not source_id or not target_id or source_id == target_id:
        return
    key = (source_id, target_id, normalized_relation_type, normalized_label or normalized_relation_type)
    edge = edge_registry.get(key)
    if edge is None:
        edge = _empty_edge(
            source_entity=normalized_source,
            target_entity=normalized_target,
            relation_label=normalized_label or normalized_relation_type,
            relation_type=normalized_relation_type,
            confidence=confidence,
        )
        edge_registry[key] = edge
    else:
        edge["confidence"] = _merge_confidence(str(edge.get("confidence") or "low"), confidence)

    _append_edge_evidence(edge, evidence)


def _build_direct_subject_edges(
    *,
    raw_indexes: dict[str, Any],
    entity_lookup: dict[str, dict[str, Any]],
    raw_node_to_entity_ids: dict[str, set[str]],
    edge_registry: dict[tuple[str, str, str, str], dict[str, Any]],
) -> None:
    node_lookup = raw_indexes.get("node_lookup", {})
    node_conversation_lookup = raw_indexes.get("node_conversation_lookup", {})

    for edge in raw_indexes.get("edges", []):
        if not isinstance(edge, dict):
            continue
        source_raw_id = _normalize_text(edge.get("source"))
        target_raw_id = _normalize_text(edge.get("target"))
        source_entity_ids = raw_node_to_entity_ids.get(source_raw_id, set())
        target_entity_ids = raw_node_to_entity_ids.get(target_raw_id, set())
        if not source_entity_ids or not target_entity_ids:
            continue

        relation_label = _raw_edge_label(edge) or "直接关系"
        source_node = node_lookup.get(source_raw_id, {})
        target_node = node_lookup.get(target_raw_id, {})
        for source_entity_id in source_entity_ids:
            for target_entity_id in target_entity_ids:
                if source_entity_id == target_entity_id:
                    continue
                source_entity = entity_lookup.get(source_entity_id)
                target_entity = entity_lookup.get(target_entity_id)
                if not source_entity or not target_entity:
                    continue

                edge_label, relation_type, confidence = _classify_subject_relation(
                    source_entity=source_entity,
                    target_entity=target_entity,
                    direct_relation_label=relation_label,
                )
                conversation_id = _positive_int(
                    edge.get("properties", {}).get("conversation_id")
                    if isinstance(edge.get("properties"), dict)
                    else 0
                ) or _positive_int(node_conversation_lookup.get(source_raw_id)) or _positive_int(
                    node_conversation_lookup.get(target_raw_id)
                )
                evidence = {
                    "conversation_id": conversation_id,
                    "summary": _relation_summary_text(
                        source_label=_normalize_text(source_entity.get("label")),
                        target_label=_normalize_text(target_entity.get("label")),
                        relation_label=edge_label,
                    ),
                    "raw_node_ids": [source_raw_id, target_raw_id],
                    "raw_edge_ids": [_raw_edge_id(edge)],
                }
                _add_relation_edge(
                    edge_registry,
                    source_entity=source_entity,
                    target_entity=target_entity,
                    relation_label=edge_label,
                    relation_type=relation_type,
                    confidence=confidence,
                    evidence=evidence,
                )


def _build_fact_mediated_edges(
    *,
    raw_indexes: dict[str, Any],
    entity_lookup: dict[str, dict[str, Any]],
    raw_node_to_entity_ids: dict[str, set[str]],
    edge_registry: dict[tuple[str, str, str, str], dict[str, Any]],
) -> None:
    node_lookup = raw_indexes.get("node_lookup", {})
    incident_edges_by_node = raw_indexes.get("incident_edges_by_node", {})

    for raw_node_id, fact_node in node_lookup.items():
        fact_label = _raw_node_label(fact_node)
        fact_type = _raw_node_type(fact_node)
        if not _is_fact_object(fact_label, fact_type):
            continue

        linked_entities: dict[str, dict[str, Any]] = {}
        for edge in incident_edges_by_node.get(raw_node_id, []):
            if not isinstance(edge, dict):
                continue
            source_raw_id = _normalize_text(edge.get("source"))
            target_raw_id = _normalize_text(edge.get("target"))
            other_raw_id = target_raw_id if source_raw_id == raw_node_id else source_raw_id
            for entity_id in raw_node_to_entity_ids.get(other_raw_id, set()):
                linked_entities.setdefault(
                    entity_id,
                    {
                        "raw_node_ids": set(),
                        "raw_edge_ids": set(),
                        "edge_labels": set(),
                        "conversation_ids": set(),
                    },
                )
                linked_entities[entity_id]["raw_node_ids"].add(other_raw_id)
                linked_entities[entity_id]["raw_edge_ids"].add(_raw_edge_id(edge))
                edge_label = _raw_edge_label(edge)
                if edge_label:
                    linked_entities[entity_id]["edge_labels"].add(edge_label)
                conversation_id = _positive_int(
                    edge.get("properties", {}).get("conversation_id")
                    if isinstance(edge.get("properties"), dict)
                    else 0
                ) or _raw_node_conversation_id(fact_node)
                if conversation_id > 0:
                    linked_entities[entity_id]["conversation_ids"].add(conversation_id)

        if len(linked_entities) < 2:
            continue

        for source_entity_id, target_entity_id in combinations(sorted(linked_entities), 2):
            source_entity = entity_lookup.get(source_entity_id)
            target_entity = entity_lookup.get(target_entity_id)
            if not source_entity or not target_entity:
                continue

            supporting_text = " ".join(
                sorted(
                    linked_entities[source_entity_id]["edge_labels"]
                    | linked_entities[target_entity_id]["edge_labels"]
                )
            )
            edge_label, relation_type, confidence = _classify_subject_relation(
                source_entity=source_entity,
                target_entity=target_entity,
                fact_label=fact_label,
                supporting_text=supporting_text,
            )
            conversation_ids = _sorted_unique_ints(
                list(linked_entities[source_entity_id]["conversation_ids"])
                + list(linked_entities[target_entity_id]["conversation_ids"])
                + [_raw_node_conversation_id(fact_node)]
            )
            evidence = {
                "conversation_id": conversation_ids[0] if conversation_ids else None,
                "summary": _relation_summary_text(
                    source_label=_normalize_text(source_entity.get("label")),
                    target_label=_normalize_text(target_entity.get("label")),
                    relation_label=edge_label,
                    fact_label=fact_label or _raw_node_type(fact_node),
                ),
                "raw_node_ids": _sorted_unique_strings(
                    [raw_node_id]
                    + list(linked_entities[source_entity_id]["raw_node_ids"])
                    + list(linked_entities[target_entity_id]["raw_node_ids"])
                ),
                "raw_edge_ids": _sorted_unique_strings(
                    list(linked_entities[source_entity_id]["raw_edge_ids"])
                    + list(linked_entities[target_entity_id]["raw_edge_ids"])
                ),
            }
            _add_relation_edge(
                edge_registry,
                source_entity=source_entity,
                target_entity=target_entity,
                relation_label=edge_label,
                relation_type=relation_type,
                confidence=confidence,
                evidence=evidence,
            )


def _build_affiliation_edges(
    *,
    entities: list[dict[str, Any]],
    edge_registry: dict[tuple[str, str, str, str], dict[str, Any]],
) -> None:
    conversation_entities: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for entity in entities:
        for conversation_id in _sorted_unique_ints(entity.get("source_conversation_ids") or []):
            conversation_entities[conversation_id].append(entity)

    for conversation_id, scoped_entities in conversation_entities.items():
        for source_entity in scoped_entities:
            if not _entity_is_role_like(source_entity):
                continue
            source_label = _normalize_text(source_entity.get("label"))
            if not _contains_any(source_label, _AFFILIATION_HINTS):
                continue
            for target_entity in scoped_entities:
                if source_entity.get("id") == target_entity.get("id") or not _entity_is_org_like(target_entity):
                    continue
                target_label = _normalize_text(target_entity.get("label"))
                if not target_label or target_label not in source_label:
                    continue
                evidence = {
                    "conversation_id": conversation_id,
                    "summary": f"会话 #{conversation_id} 中节点“{source_label}”名称包含“{target_label}”，推断为代表/隶属关系。",
                    "raw_node_ids": _sorted_unique_strings(
                        list(source_entity.get("raw_node_ids") or [])
                        + list(target_entity.get("raw_node_ids") or [])
                    ),
                    "raw_edge_ids": [],
                }
                _add_relation_edge(
                    edge_registry,
                    source_entity=source_entity,
                    target_entity=target_entity,
                    relation_label="代表/隶属",
                    relation_type="affiliation",
                    confidence="medium",
                    evidence=evidence,
                )


def _derive_subject_relation_edges(entities: list[dict[str, Any]], raw_indexes: dict[str, Any]) -> list[dict[str, Any]]:
    entity_lookup = {
        _normalize_text(entity.get("id")): entity
        for entity in entities
        if _normalize_text(entity.get("id"))
    }
    raw_node_to_entity_ids: dict[str, set[str]] = defaultdict(set)
    for entity in entities:
        entity_id = _normalize_text(entity.get("id"))
        if not entity_id:
            continue
        for raw_node_id in _sorted_unique_strings(entity.get("raw_node_ids") or []):
            raw_node_to_entity_ids[raw_node_id].add(entity_id)

    edge_registry: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    _build_direct_subject_edges(
        raw_indexes=raw_indexes,
        entity_lookup=entity_lookup,
        raw_node_to_entity_ids=raw_node_to_entity_ids,
        edge_registry=edge_registry,
    )
    _build_fact_mediated_edges(
        raw_indexes=raw_indexes,
        entity_lookup=entity_lookup,
        raw_node_to_entity_ids=raw_node_to_entity_ids,
        edge_registry=edge_registry,
    )
    _build_affiliation_edges(
        entities=entities,
        edge_registry=edge_registry,
    )

    edges = list(edge_registry.values())
    edges.sort(key=lambda edge: (str(edge.get("relation_type")), str(edge.get("source")), str(edge.get("target"))))
    return edges


def _validate_responsible_entity_graph_output(payload: dict[str, Any]) -> dict[str, Any]:
    graph = payload if isinstance(payload, dict) else {}
    entities = graph.get("entities")
    validated_entities: list[dict[str, Any]] = []
    entity_ids: set[str] = set()
    for entity in entities if isinstance(entities, list) else []:
        if not isinstance(entity, dict):
            continue
        entity_id = _normalize_text(entity.get("id"))
        label = _normalize_text(entity.get("label") or entity.get("display_label"))
        entity_type = _normalize_text(entity.get("entity_type"))
        if not entity_id or not label or not _is_responsible_entity_candidate(label, entity_type):
            continue
        validated_entity = dict(entity)
        validated_entity["id"] = entity_id
        validated_entity["label"] = label
        validated_entity["display_label"] = _normalize_text(entity.get("display_label")) or label
        validated_entity["entity_type"] = entity_type if entity_type in _TYPE_DISPLAY_LABELS else "unknown"
        validated_entity["source_conversation_ids"] = _sorted_unique_ints(entity.get("source_conversation_ids") or [])
        validated_entity["raw_node_ids"] = _sorted_unique_strings(entity.get("raw_node_ids") or [])
        validated_entity["raw_labels"] = _sorted_unique_strings(
            entity.get("raw_labels") or entity.get("source_labels") or []
        )
        validated_entity["source_labels"] = validated_entity["raw_labels"]
        entity_ids.add(entity_id)
        validated_entities.append(validated_entity)

    edges = graph.get("edges")
    validated_edges: list[dict[str, Any]] = []
    for edge in edges if isinstance(edges, list) else []:
        if not isinstance(edge, dict):
            continue
        source_id = _normalize_text(edge.get("source"))
        target_id = _normalize_text(edge.get("target"))
        if not source_id or not target_id or source_id not in entity_ids or target_id not in entity_ids:
            continue
        validated_evidence = []
        for evidence in edge.get("evidence") if isinstance(edge.get("evidence"), list) else []:
            if not isinstance(evidence, dict):
                continue
            validated_evidence.append(
                {
                    "conversation_id": _positive_int(evidence.get("conversation_id")) or None,
                    "summary": _normalize_text(evidence.get("summary")),
                    "raw_node_ids": _sorted_unique_strings(evidence.get("raw_node_ids") or []),
                    "raw_edge_ids": _sorted_unique_strings(evidence.get("raw_edge_ids") or []),
                }
            )
        validated_edges.append(
            {
                "id": _normalize_text(edge.get("id")) or f"{source_id}:{target_id}:{_normalize_key(edge.get('label') or edge.get('relation_type'))}",
                "source": source_id,
                "target": target_id,
                "label": _normalize_text(edge.get("label") or edge.get("relation_type")) or "关联",
                "relation_type": _normalize_text(edge.get("relation_type")) or "same_case",
                "source_conversation_ids": _sorted_unique_ints(edge.get("source_conversation_ids") or []),
                "evidence": validated_evidence,
                "confidence": str(edge.get("confidence") or "low").strip().lower() or "low",
            }
        )

    trace = graph.get("trace") if isinstance(graph.get("trace"), dict) else {}
    return {
        "entities": validated_entities,
        "edges": validated_edges,
        "tasks": graph.get("tasks") if isinstance(graph.get("tasks"), list) else [],
        "stats": graph.get("stats") if isinstance(graph.get("stats"), dict) else {},
        "trace": {
            "mode": trace.get("mode") or _TRACE_MODE,
            "provider": trace.get("provider") or _TRACE_PROVIDER,
            "model": trace.get("model") or _TRACE_MODEL,
            "fallback_reason": trace.get("fallback_reason") or _TRACE_FALLBACK_REASON,
            "steps": trace.get("steps") if isinstance(trace.get("steps"), list) else list(_TRACE_STEPS),
            "warnings": trace.get("warnings") if isinstance(trace.get("warnings"), list) else [],
        },
    }


def _build_responsible_entity_graph_rules(
    agent_input: dict[str, Any],
) -> dict[str, Any]:
    raw_graph_payload = agent_input.get("raw_graph_payload")
    display_graph = agent_input.get("display_graph")
    participant_candidates = agent_input.get("participant_candidates")
    intelligence_payload = agent_input.get("intelligence_payload")
    bound_conversation_ids = agent_input.get("bound_conversation_ids")

    raw_indexes = _raw_indexes(raw_graph_payload)
    participant_indexes = _graph_participant_indexes(raw_graph_payload)
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
        participant_indexes=participant_indexes,
        covered_conversation_ids=covered_conversation_ids,
        covered_raw_node_ids=covered_raw_node_ids,
    )

    entities = (
        confirmed_entities
        + pending_identity_entities
        + pending_naming_entities
        + isolated_anchor_entities
    )
    edges = _derive_subject_relation_edges(entities, raw_indexes)
    tasks = pending_identity_tasks + pending_naming_tasks
    warnings: list[str] = []
    if not entities:
        warnings.append("no_responsible_entities_detected")

    return {
        "entities": entities,
        "edges": edges,
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
            "steps": list(_TRACE_STEPS),
            "warnings": warnings,
        },
    }


def build_dossier_coordination_graph(
    *,
    raw_graph_payload: dict[str, Any] | None,
    display_graph: dict[str, Any] | None,
    participant_candidates: list[dict[str, Any]] | None,
    intelligence_payload: dict[str, Any] | None,
    bound_conversation_ids: list[int] | None = None,
) -> dict[str, Any]:
    agent_input = _build_responsible_entity_graph_agent_input(
        raw_graph_payload=raw_graph_payload,
        display_graph=display_graph,
        participant_candidates=participant_candidates,
        intelligence_payload=intelligence_payload,
        bound_conversation_ids=bound_conversation_ids,
    )
    rules_output = _build_responsible_entity_graph_rules(agent_input)
    return _validate_responsible_entity_graph_output(rules_output)


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
    decisions = await list_dossier_entity_resolution_decisions(db, user_id, dossier.id)

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
    raw_graph_payload["entity_resolution_decisions"] = [
        serialize_dossier_entity_resolution_decision(decision)
        for decision in decisions
    ]
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
        resolution_decisions=decisions,
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
