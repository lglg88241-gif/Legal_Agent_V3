from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.services.conversation_store import (
    build_dossier_participant_candidates,
    build_dossier_participant_lookup,
    get_dossier_for_user,
    is_role_person_label,
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
from app.services.message_understanding_agent import (
    _call_openai_json_model,
    _call_zhipu_json_model,
    _extract_json_object,
    _resolve_provider_name,
)

logger = logging.getLogger(__name__)

_STATUS_SUCCESS = "success"
_SOURCE_NAME = "dossier_intelligence"
_PROVIDER_NAME = "rules"
_MODEL_NAME = "dossier-intelligence-rules-v0"
_FALLBACK_REASON = "llm_not_enabled_in_skeleton"
_CLASSIFIER_RULE_PROVIDER = "rules"
_CLASSIFIER_RULE_MODEL = "dossier-intelligence-candidate-rules-v1"
_CLASSIFIER_FALLBACK_REASON = "llm_not_enabled_in_candidate_classifier"
_SUMMARY_FALLBACK_TEXT = "\u5f53\u524d\u6848\u5377\u6682\u65e0\u53ef\u7528\u6458\u8981\u3002"
_CLASSIFIER_SUPPORTED_TASK_TYPES = {
    "identity",
    "party_naming",
    "affiliation_relation",
    "relation_only",
    "not_actionable",
}
_CLASSIFIER_SUPPORTED_CONFIDENCE = {"high", "medium", "low"}
_CLASSIFIER_SHOW_TO_USER_DEFAULTS = {
    "identity": True,
    "party_naming": True,
    "affiliation_relation": True,
    "relation_only": False,
    "not_actionable": False,
}
_CLASSIFIER_EXCLUDED_NODE_TYPES: set[str] = set()
_CLASSIFIER_SYSTEM_PROMPT = """你是案卷主体候选分类智能体，不负责输出法律建议。
你的任务是根据给定的案卷候选证据，把每个 candidate 分类成最合适的任务类型，并只输出 JSON。

严格要求：
1. 只能输出一个 JSON object，不要输出 markdown，不要输出解释文本。
2. tasks[].task_type 只能是：
   - identity
   - party_naming
   - affiliation_relation
   - relation_only
   - not_actionable
3. confidence 只能是 high / medium / low。
4. should_show_to_user 必须是布尔值。
5. 禁止依赖关键词命中；必须基于语义、上下文、共现线索、既有映射和历史决策判断。
6. generic + participant mapping 不足以直接判定 identity，必须有显式同名、同组织或稳定主体线索。
7. 角色型 person（经理、HR、人事、主管等）不能成为 identity 的一端，应优先判定为 affiliation_relation 或 relation_only。
8. 自我指代（我 / 本人 / 当事人 / 劳动者本人）默认是 party_naming，不与其他 person 自动 identity merge。
9. 同名跨会话 organization 默认可判定为 identity high。
10. 事件 / 文书 / 金额 / 时间 / 法条 / 程序节点必须是 not_actionable，且 should_show_to_user=false。
11. 无稳定 affiliation evidence 的角色型 person 应判定为 relation_only，且 should_show_to_user=false。
12. 已有 not_same / not_actionable 决策的 pair，不得再输出 identity 或 affiliation_relation。
13. 已有 same 决策的 pair，不再输出 identity。

输出 JSON 结构：
{
  "tasks": [
    {
      "task_type": "identity",
      "confidence": "high",
      "should_show_to_user": true,
      "reason": "...",
      "subject": {},
      "object": {},
      "evidence_summary": "...",
      "candidate_key": "..."
    }
  ]
}
"""

_SELF_ANCHOR_LABEL_PRIORITY = (
    "\u5f53\u4e8b\u4eba",
    "\u6211",
    "\u672c\u4eba",
    "\u81ea\u5df1",
    "\u7528\u6237",
    "\u6211\u4eec",
    "\u54b1\u4eec",
)
_SELF_ANCHOR_LABELS = set(_SELF_ANCHOR_LABEL_PRIORITY)
_SELF_NAMING_EXPLICIT_LABELS = {
    "\u6211",
    "\u672c\u4eba",
    "\u81ea\u5df1",
    "\u6211\u4eec",
    "\u54b1\u4eec",
    "\u52b3\u52a8\u8005\u672c\u4eba",
    "\u6211\u65b9\u5f53\u4e8b\u4eba",
    "\u7533\u8bf7\u4eba",
}
_SELF_NAMING_SUGGESTED_LABELS = [
    "\u52b3\u52a8\u8005\u672c\u4eba",
    "\u6211\u65b9\u5f53\u4e8b\u4eba",
    "\u7533\u8bf7\u4eba",
]
_SELF_NAMING_REASON = (
    "\u6848\u5377\u4e2d\u5b58\u5728\u7b2c\u4e00\u4eba\u79f0\u6216\u5f53\u4e8b\u4eba\u4e3b\u4f53\uff0c"
    "\u9700\u8981\u786e\u8ba4\u5bf9\u5916\u5c55\u793a\u540d\u79f0\u3002"
)
_SELF_PARTY_SIDES = {"claimant", "self", "primary"}
_NON_PERSON_ENTITY_TYPES = {"organization", "company", "employer", "authority"}
_SELF_IDENTITY_TITLE = "\u786e\u8ba4\u5f53\u4e8b\u4eba\u662f\u5426\u4e3a\u540c\u4e00\u4eba"
_SELF_IDENTITY_REASON_TEMPLATE = (
    "\u8be5\u6848\u5377\u5df2\u6709\u5df2\u547d\u540d\u5f53\u4e8b\u4eba\u201c{display_label}\u201d\uff0c"
    "\u65b0\u4f1a\u8bdd\u4e2d\u4e5f\u51fa\u73b0\u7b2c\u4e00\u4eba\u79f0\u6216\u5f53\u4e8b\u4eba\u4e3b\u4f53\uff0c"
    "\u9700\u8981\u786e\u8ba4\u662f\u5426\u4e3a\u540c\u4e00\u4eba\u3002"
)
_SELF_IDENTITY_SOURCE = "dossier_intelligence_rules"
_SELF_IDENTITY_CONFIDENCE = "high"
_SELF_IDENTITY_SUGGESTED_ACTION = "confirm_same_self"
_SELF_IDENTITY_EXCLUDED_NODE_TYPES = {
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
    "\u4e8b\u4ef6",
    "\u4e8b\u5b9e",
    "\u8bc1\u636e",
    "\u6587\u4e66",
    "\u6587\u4ef6",
    "\u91d1\u989d",
    "\u91d1\u94b1",
    "\u65f6\u95f4",
    "\u65e5\u671f",
    "\u6cd5\u5f8b",
    "\u6cd5\u6761",
    "\u8bc9\u6c42",
    "\u7a0b\u5e8f",
}
_CLASSIFIER_EXCLUDED_NODE_TYPES = _SELF_IDENTITY_EXCLUDED_NODE_TYPES | {
    "action",
    "issue",
    "benefit",
    "social_insurance",
    "notice",
    "relationship_event",
    "\u884c\u4e3a",
    "\u95ee\u9898",
    "\u793e\u4fdd",
    "\u901a\u77e5",
    "\u4eba\u4e8b\u901a\u77e5",
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


def _unique_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize_text(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _sorted_unique_ints(values: list[int]) -> list[int]:
    return sorted({value for value in values if isinstance(value, int) and value > 0})


def _sorted_unique_strings(values: list[str]) -> list[str]:
    return sorted({value for value in values if _normalize_text(value)})


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
    return default


def _extract_user_id(current_user: User) -> int:
    raw_user_id = getattr(current_user, "id", None)
    if raw_user_id is None:
        raw_user_id = getattr(current_user, "user_id", None)
    try:
        user_id = int(raw_user_id or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("Current user is required") from exc
    if user_id <= 0:
        raise ValueError("Current user is required")
    return user_id


def _isoformat(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _step(step: str, status: str, **extra: Any) -> dict[str, Any]:
    payload = {"step": step, "status": status}
    payload.update(extra)
    return payload


def _build_summary_payload(dossier: Any) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []

    auto_summary = _normalize_text(getattr(dossier, "auto_summary", ""))
    if auto_summary:
        return (
            {
                "text": auto_summary,
                "source": "auto_summary",
                "updated_at": _isoformat(
                    getattr(dossier, "auto_summary_updated_at", None)
                    or getattr(dossier, "updated_at", None)
                ),
            },
            warnings,
        )

    manual_summary = _normalize_text(getattr(dossier, "summary", ""))
    if manual_summary:
        return (
            {
                "text": manual_summary,
                "source": "manual_summary",
                "updated_at": _isoformat(getattr(dossier, "updated_at", None)),
            },
            warnings,
        )

    warnings.append("No dossier summary available")
    return (
        {
            "text": _SUMMARY_FALLBACK_TEXT,
            "source": "rule_fallback",
            "updated_at": _isoformat(getattr(dossier, "updated_at", None)),
        },
        warnings,
    )


def _derive_conversation_count(
    display_graph: dict[str, Any] | None,
    raw_graph_payload: dict[str, Any] | None,
) -> int:
    raw_graph = raw_graph_payload.get("graph") if isinstance(raw_graph_payload, dict) else {}
    raw_stats = (
        raw_graph.get("stats")
        if isinstance(raw_graph, dict) and isinstance(raw_graph.get("stats"), dict)
        else {}
    )
    conversation_count = _positive_int(raw_stats.get("conversation_count"))
    if conversation_count > 0:
        return conversation_count

    nodes = display_graph.get("nodes") if isinstance(display_graph, dict) else []
    conversation_ids: set[int] = set()
    for node in nodes if isinstance(nodes, list) else []:
        if not isinstance(node, dict):
            continue
        source_ids = node.get("source_conversation_ids")
        for source_id in source_ids if isinstance(source_ids, list) else []:
            normalized_id = _positive_int(source_id)
            if normalized_id > 0:
                conversation_ids.add(normalized_id)
    return len(conversation_ids)


def _build_display_graph_notes(
    display_graph: dict[str, Any] | None,
    raw_graph_payload: dict[str, Any] | None,
) -> list[str]:
    stats = display_graph.get("stats") if isinstance(display_graph, dict) else {}
    merged_participant_count = _positive_int(stats.get("merged_participant_count"))
    pending_confirmation_count = _positive_int(stats.get("pending_confirmation_count"))
    conversation_count = _derive_conversation_count(display_graph, raw_graph_payload)

    return [
        f"\u5df2\u786e\u8ba4\u4e3b\u4f53\u6570\u91cf\uff1a{merged_participant_count}\u3002",
        f"\u5f85\u786e\u8ba4\u539f\u59cb\u5b9e\u4f53\u6570\u91cf\uff1a{pending_confirmation_count}\u3002",
        f"\u6d89\u53ca\u4f1a\u8bdd\u6570\u91cf\uff1a{conversation_count}\u3002",
    ]


def _extract_node_label(node: dict[str, Any]) -> str:
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


def _extract_node_conversation_ids(node: dict[str, Any]) -> list[int]:
    values: list[int] = []
    source_ids = node.get("source_conversation_ids")
    if isinstance(source_ids, list):
        for source_id in source_ids:
            normalized_id = _positive_int(source_id)
            if normalized_id > 0:
                values.append(normalized_id)

    normalized_direct_id = _positive_int(node.get("conversation_id"))
    if normalized_direct_id > 0:
        values.append(normalized_direct_id)

    properties = node.get("properties")
    if isinstance(properties, dict):
        normalized_property_id = _positive_int(properties.get("conversation_id"))
        if normalized_property_id > 0:
            values.append(normalized_property_id)

    return _sorted_unique_ints(values)


def _extract_node_raw_ids(node: dict[str, Any]) -> list[str]:
    raw_node_ids = node.get("raw_node_ids")
    if isinstance(raw_node_ids, list):
        return _sorted_unique_strings(_unique_strings([str(item) for item in raw_node_ids]))

    values: list[str] = []
    for candidate in (node.get("id"), node.get("graph_node_id")):
        normalized = _normalize_text(candidate)
        if normalized:
            values.append(normalized)

    return _sorted_unique_strings(values)


def _extract_node_type(node: dict[str, Any]) -> str:
    candidates = [
        node.get("graph_node_type"),
        node.get("entity_type"),
        node.get("type"),
        node.get("kind"),
    ]
    properties = node.get("properties")
    if isinstance(properties, dict):
        candidates.extend(
            [
                properties.get("graph_node_type"),
                properties.get("entity_type"),
                properties.get("type"),
                properties.get("kind"),
            ]
        )

    for candidate in candidates:
        normalized = _normalize_key(candidate)
        if normalized:
            return normalized
    return ""


def _extract_self_anchor_labels_from_node(node: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    raw_labels = node.get("raw_labels")
    if isinstance(raw_labels, list):
        candidates.extend(_unique_strings([str(item) for item in raw_labels]))

    direct_label = _extract_node_label(node)
    if direct_label:
        candidates.append(direct_label)

    return [
        label
        for label in _unique_strings(candidates)
        if _normalize_text(label) in _SELF_ANCHOR_LABELS
    ]


def _is_self_identity_eligible_node(node: dict[str, Any]) -> bool:
    if not isinstance(node, dict):
        return False
    if not _extract_self_anchor_labels_from_node(node):
        return False
    node_type = _extract_node_type(node)
    if node_type and node_type in _SELF_IDENTITY_EXCLUDED_NODE_TYPES:
        return False
    return True


def _is_explicit_self_label(value: Any) -> bool:
    normalized = _normalize_text(value)
    if not normalized:
        return False
    return normalized in _SELF_NAMING_EXPLICIT_LABELS


def _has_explicit_self_alias(participant_aliases: dict[str, Any] | None) -> bool:
    aliases = participant_aliases if isinstance(participant_aliases, dict) else {}
    primary_alias = aliases.get("primary_party")
    if not isinstance(primary_alias, dict):
        return False

    label = _normalize_text(primary_alias.get("label"))
    role = _normalize_text(primary_alias.get("role"))
    if _is_explicit_self_label(label) or _is_explicit_self_label(role):
        return True
    return bool(label and label not in {"\u5f53\u4e8b\u4eba", "\u7528\u6237"})


def _participant_has_explicit_self_identity(participant: dict[str, Any]) -> bool:
    if not isinstance(participant, dict):
        return False

    label = _normalize_text(participant.get("label"))
    role = _normalize_text(participant.get("role"))
    party_side = _normalize_key(participant.get("party_side"))
    entity_type = _normalize_key(participant.get("entity_type"))

    if _is_explicit_self_label(label) or _is_explicit_self_label(role):
        return True

    if (
        party_side in _SELF_PARTY_SIDES
        and label
        and label not in {"\u5f53\u4e8b\u4eba", "\u7528\u6237"}
        and entity_type not in _NON_PERSON_ENTITY_TYPES
    ):
        return True

    return False


def _participant_self_identity_rank(participant: dict[str, Any]) -> tuple[int, int] | None:
    if not isinstance(participant, dict):
        return None

    label = _normalize_text(participant.get("label"))
    role = _normalize_text(participant.get("role"))
    party_side = _normalize_key(participant.get("party_side"))
    entity_type = _normalize_key(participant.get("entity_type"))

    if _is_explicit_self_label(label) or _is_explicit_self_label(role):
        return (0, 0)

    if (
        party_side in _SELF_PARTY_SIDES
        and label
        and label not in {"\u5f53\u4e8b\u4eba", "\u7528\u6237"}
        and entity_type not in _NON_PERSON_ENTITY_TYPES
    ):
        return (1, 0 if entity_type == "person" else 1)

    if party_side in _SELF_PARTY_SIDES and _is_explicit_self_label(role):
        return (2, 0)

    return None


def _select_confirmed_self_participant(
    raw_graph_payload: dict[str, Any] | None,
) -> dict[str, Any] | None:
    payload = raw_graph_payload if isinstance(raw_graph_payload, dict) else {}
    confirmed_participants = payload.get("confirmed_participants")
    if not isinstance(confirmed_participants, list):
        return None

    ranked_matches: list[tuple[tuple[int, int], int, dict[str, Any]]] = []
    for index, participant in enumerate(confirmed_participants):
        if not isinstance(participant, dict):
            continue
        rank = _participant_self_identity_rank(participant)
        if rank is None:
            continue
        ranked_matches.append((rank, index, participant))

    if not ranked_matches:
        return None

    ranked_matches.sort(key=lambda item: (item[0], item[1]))
    return ranked_matches[0][2]


def _has_confirmed_self_naming(raw_graph_payload: dict[str, Any] | None) -> bool:
    payload = raw_graph_payload if isinstance(raw_graph_payload, dict) else {}

    if _has_explicit_self_alias(payload.get("participant_aliases")):
        return True

    return _select_confirmed_self_participant(payload) is not None


def _collect_self_anchor_evidence(
    raw_graph_payload: dict[str, Any] | None,
    display_graph: dict[str, Any] | None,
) -> dict[str, Any] | None:
    raw_labels: list[str] = []
    source_conversation_ids: list[int] = []
    raw_node_ids: list[str] = []

    raw_graph = raw_graph_payload.get("graph") if isinstance(raw_graph_payload, dict) else {}
    raw_nodes = raw_graph.get("nodes") if isinstance(raw_graph, dict) else []
    for node in raw_nodes if isinstance(raw_nodes, list) else []:
        if not isinstance(node, dict):
            continue
        labels = _extract_self_anchor_labels_from_node(node)
        if not labels:
            continue
        raw_labels.extend(labels)
        source_conversation_ids.extend(_extract_node_conversation_ids(node))
        raw_node_ids.extend(_extract_node_raw_ids(node))

    display_nodes = display_graph.get("nodes") if isinstance(display_graph, dict) else []
    for node in display_nodes if isinstance(display_nodes, list) else []:
        if not isinstance(node, dict):
            continue
        labels = _extract_self_anchor_labels_from_node(node)
        if not labels:
            continue
        raw_labels.extend(labels)
        source_conversation_ids.extend(_extract_node_conversation_ids(node))
        raw_node_ids.extend(_extract_node_raw_ids(node))

    unique_labels = _unique_strings(raw_labels)
    if not unique_labels:
        return None

    ordered_labels: list[str] = []
    for preferred_label in _SELF_ANCHOR_LABEL_PRIORITY:
        if preferred_label in unique_labels:
            ordered_labels.append(preferred_label)
    for label in sorted(unique_labels):
        if label not in ordered_labels:
            ordered_labels.append(label)

    return {
        "raw_labels": ordered_labels,
        "source_conversation_ids": _sorted_unique_ints(source_conversation_ids),
        "raw_node_ids": _sorted_unique_strings(raw_node_ids),
    }


def _build_party_naming_task(
    raw_graph_payload: dict[str, Any] | None,
    display_graph: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if _has_confirmed_self_naming(raw_graph_payload):
        return None

    evidence = _collect_self_anchor_evidence(raw_graph_payload, display_graph)
    if evidence is None:
        return None

    current_label = evidence["raw_labels"][0]
    return {
        "task_type": "party_naming",
        "subject": "self_anchor",
        "current_label": current_label,
        "suggested_labels": list(_SELF_NAMING_SUGGESTED_LABELS),
        "reason": _SELF_NAMING_REASON,
        "requires_user_confirmation": True,
        "source": "dossier_intelligence_rules",
        "evidence": evidence,
    }


def _build_participant_mapping_indexes(
    raw_graph_payload: dict[str, Any] | None,
) -> tuple[set[str], set[tuple[int, str]], dict[int, list[int]]]:
    payload = raw_graph_payload if isinstance(raw_graph_payload, dict) else {}
    participant_mappings = payload.get("participant_mappings")
    if not isinstance(participant_mappings, list):
        return set(), set(), {}

    mapped_graph_node_ids: set[str] = set()
    mapped_legacy_keys: set[tuple[int, str]] = set()
    participant_conversation_ids: dict[int, set[int]] = {}

    for mapping in participant_mappings:
        if not isinstance(mapping, dict):
            continue

        participant_id = _positive_int(mapping.get("participant_id"))
        conversation_id = _positive_int(mapping.get("conversation_id"))
        raw_label = _normalize_text(mapping.get("raw_label"))
        graph_node_id = _normalize_text(mapping.get("graph_node_id"))

        if graph_node_id:
            mapped_graph_node_ids.add(graph_node_id)
        if conversation_id > 0 and raw_label:
            mapped_legacy_keys.add((conversation_id, _normalize_key(raw_label)))
        if participant_id > 0 and conversation_id > 0:
            participant_conversation_ids.setdefault(participant_id, set()).add(conversation_id)

    return (
        mapped_graph_node_ids,
        mapped_legacy_keys,
        {
            participant_id: sorted(conversation_ids)
            for participant_id, conversation_ids in participant_conversation_ids.items()
        },
    )


def _build_self_identity_not_same_index(
    raw_graph_payload: dict[str, Any] | None,
) -> set[tuple[str, int]]:
    payload = raw_graph_payload if isinstance(raw_graph_payload, dict) else {}
    decisions = payload.get("entity_resolution_decisions")
    if not isinstance(decisions, list):
        return set()

    index: set[tuple[str, int]] = set()
    for decision in decisions:
        if not isinstance(decision, dict):
            continue
        if _normalize_key(decision.get("subject")) != "self_anchor":
            continue
        if _normalize_key(decision.get("decision_type")) != "not_same":
            continue

        graph_node_id = _normalize_text(decision.get("source_graph_node_id"))
        target_participant_id = _positive_int(decision.get("target_participant_id"))
        if graph_node_id and target_participant_id > 0:
            index.add((graph_node_id, target_participant_id))
    return index


def _build_self_identity_tasks(
    raw_graph_payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if _derive_conversation_count(None, raw_graph_payload) <= 1:
        return []

    self_participant = _select_confirmed_self_participant(raw_graph_payload)
    participant_id = _positive_int(self_participant.get("id")) if isinstance(self_participant, dict) else 0
    if participant_id <= 0:
        return []

    participant_display_label = _normalize_text(self_participant.get("label"))
    if not participant_display_label:
        return []

    mapped_graph_node_ids, mapped_legacy_keys, participant_conversation_ids = (
        _build_participant_mapping_indexes(raw_graph_payload)
    )
    not_same_pairs = _build_self_identity_not_same_index(raw_graph_payload)
    target_conversation_ids = participant_conversation_ids.get(participant_id, [])
    target_conversation_id_set = set(target_conversation_ids)

    raw_graph = raw_graph_payload.get("graph") if isinstance(raw_graph_payload, dict) else {}
    raw_nodes = raw_graph.get("nodes") if isinstance(raw_graph, dict) else []
    tasks: list[dict[str, Any]] = []
    seen_graph_node_ids: set[str] = set()

    for node in raw_nodes if isinstance(raw_nodes, list) else []:
        if not _is_self_identity_eligible_node(node):
            continue

        raw_node_ids = _extract_node_raw_ids(node)
        graph_node_id = raw_node_ids[0] if raw_node_ids else ""
        if not graph_node_id or graph_node_id in seen_graph_node_ids:
            continue
        seen_graph_node_ids.add(graph_node_id)

        conversation_ids = _extract_node_conversation_ids(node)
        conversation_id = conversation_ids[0] if conversation_ids else 0
        raw_label = _extract_node_label(node)
        if conversation_id <= 0 or not raw_label:
            continue
        if conversation_id in target_conversation_id_set:
            continue
        if graph_node_id in mapped_graph_node_ids:
            continue
        if (conversation_id, _normalize_key(raw_label)) in mapped_legacy_keys:
            continue
        if (graph_node_id, participant_id) in not_same_pairs:
            continue

        graph_node_type = _extract_node_type(node) or "person"
        tasks.append(
            {
                "task_type": "entity_identity",
                "subject": "self_anchor",
                "title": _SELF_IDENTITY_TITLE,
                "source_node": {
                    "conversation_id": conversation_id,
                    "raw_label": raw_label,
                    "graph_node_id": graph_node_id,
                    "graph_node_label": _normalize_text(
                        node.get("graph_node_label") or raw_label
                    )
                    or raw_label,
                    "graph_node_type": graph_node_type,
                },
                "target_participant": {
                    "participant_id": participant_id,
                    "display_label": participant_display_label,
                    "source_conversation_ids": list(target_conversation_ids),
                },
                "reason": _SELF_IDENTITY_REASON_TEMPLATE.format(
                    display_label=participant_display_label
                ),
                "confidence": _SELF_IDENTITY_CONFIDENCE,
                "requires_user_confirmation": True,
                "suggested_action": _SELF_IDENTITY_SUGGESTED_ACTION,
                "source": _SELF_IDENTITY_SOURCE,
            }
        )

    return tasks


def _is_self_anchor_candidate_label(value: Any) -> bool:
    normalized = _normalize_text(value)
    if not normalized:
        return False
    return normalized in _SELF_ANCHOR_LABELS or normalized in _SELF_NAMING_EXPLICIT_LABELS


def _is_organization_like_label(value: Any) -> bool:
    label = _normalize_text(value)
    if not label:
        return False
    return any(token in label for token in ("\u516c\u53f8", "\u5355\u4f4d", "\u96c6\u56e2", "\u673a\u6784", "\u533b\u9662", "\u5b66\u6821", "\u94f6\u884c", "\u5382"))


def _normalize_candidate_task_type(value: Any) -> str:
    normalized = _normalize_key(value)
    if normalized == "cross_conversation_entity_resolution":
        return "identity"
    return normalized


def _node_payload_from_candidate_node(
    node_payload: dict[str, Any] | None,
    *,
    fallback_label: str = "",
    fallback_conversation_id: int = 0,
) -> dict[str, Any] | None:
    node = node_payload if isinstance(node_payload, dict) else {}
    raw_label = _normalize_text(node.get("raw_label") or node.get("label") or fallback_label)
    graph_node_label = _normalize_text(node.get("graph_node_label") or node.get("label") or raw_label)
    if not raw_label and not graph_node_label and fallback_conversation_id <= 0:
        return None

    graph_node_type = _normalize_key(
        node.get("graph_node_type")
        or node.get("entity_type")
        or node.get("type")
        or ""
    )
    conversation_id = _positive_int(node.get("conversation_id")) or fallback_conversation_id
    graph_node_id = _normalize_text(node.get("graph_node_id") or node.get("id"))
    label_for_flags = graph_node_label or raw_label

    return {
        "conversation_id": conversation_id,
        "graph_node_id": graph_node_id or None,
        "raw_label": raw_label or graph_node_label,
        "graph_node_label": graph_node_label or raw_label,
        "graph_node_type": graph_node_type or "unknown",
        "is_role_person": is_role_person_label(label_for_flags),
        "is_self_anchor": _is_self_anchor_candidate_label(label_for_flags),
        "is_organization": graph_node_type in {"organization", "company", "employer"} or _is_organization_like_label(label_for_flags),
    }


def _build_raw_graph_indexes(
    raw_graph_payload: dict[str, Any] | None,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    raw_graph = raw_graph_payload.get("graph") if isinstance(raw_graph_payload, dict) else {}
    nodes = raw_graph.get("nodes") if isinstance(raw_graph, dict) else []
    edges = raw_graph.get("edges") if isinstance(raw_graph, dict) else []

    node_index: dict[str, dict[str, Any]] = {}
    for node in nodes if isinstance(nodes, list) else []:
        if not isinstance(node, dict):
            continue
        node_id = _normalize_text(node.get("id") or node.get("graph_node_id"))
        if not node_id:
            continue
        node_index[node_id] = node

    return node_index, [edge for edge in edges if isinstance(edge, dict)] if isinstance(edges, list) else []


def _find_node_by_label_and_conversation(
    *,
    raw_graph_payload: dict[str, Any] | None,
    conversation_id: int,
    label: str,
) -> dict[str, Any] | None:
    raw_graph = raw_graph_payload.get("graph") if isinstance(raw_graph_payload, dict) else {}
    nodes = raw_graph.get("nodes") if isinstance(raw_graph, dict) else []
    normalized_label = _normalize_key(label)
    if conversation_id <= 0 or not normalized_label or not isinstance(nodes, list):
        return None

    for node in nodes:
        if not isinstance(node, dict):
            continue
        if _positive_int(node.get("conversation_id") or node.get("properties", {}).get("conversation_id")) != conversation_id:
            continue
        candidate_label = _normalize_key(_extract_node_label(node))
        if candidate_label == normalized_label:
            return node
    return None


def _candidate_subject_and_object(
    candidate: dict[str, Any],
    *,
    raw_graph_payload: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    task_type = _normalize_candidate_task_type(candidate.get("task_type"))
    if task_type == "identity":
        return (
            _node_payload_from_candidate_node(candidate.get("source_node")),
            _node_payload_from_candidate_node(candidate.get("target_node")),
        )

    mentions = candidate.get("mentions") if isinstance(candidate.get("mentions"), list) else []
    subject_node = _node_payload_from_candidate_node(
        mentions[0] if mentions else None,
        fallback_label=_normalize_text(candidate.get("raw_label") or candidate.get("suggested_label")),
    )
    object_node: dict[str, Any] | None = None
    related_organization_labels = [
        _normalize_text(label)
        for label in (candidate.get("related_organization_labels") or [])
        if _normalize_text(label)
    ]
    if related_organization_labels and subject_node is not None:
        organization_label = related_organization_labels[0]
        matched_node = _find_node_by_label_and_conversation(
            raw_graph_payload=raw_graph_payload,
            conversation_id=_positive_int(subject_node.get("conversation_id")),
            label=organization_label,
        )
        if matched_node is not None:
            object_node = _node_payload_from_candidate_node(
                {
                    "conversation_id": _positive_int(
                        matched_node.get("conversation_id")
                        or matched_node.get("properties", {}).get("conversation_id")
                    ),
                    "graph_node_id": _normalize_text(matched_node.get("id")),
                    "raw_label": _extract_node_label(matched_node),
                    "graph_node_label": _extract_node_label(matched_node),
                    "graph_node_type": _extract_node_type(matched_node) or "organization",
                }
            )
        else:
            object_node = _node_payload_from_candidate_node(
                {
                    "conversation_id": subject_node.get("conversation_id"),
                    "raw_label": organization_label,
                    "graph_node_label": organization_label,
                    "graph_node_type": "organization",
                }
            )

    return subject_node, object_node


def _decision_pair_key_for_candidate(
    subject_node: dict[str, Any] | None,
    object_node: dict[str, Any] | None,
) -> str:
    if not isinstance(subject_node, dict) or not isinstance(object_node, dict):
        return ""
    left = _normalize_text(subject_node.get("graph_node_id"))
    right = _normalize_text(object_node.get("graph_node_id"))
    if not left or not right:
        return ""
    ordered = sorted((left, right))
    return f"{ordered[0]}::{ordered[1]}"


def _edge_summary(
    edge: dict[str, Any],
    *,
    node_index: dict[str, dict[str, Any]],
) -> str:
    source_id = _normalize_text(edge.get("source"))
    target_id = _normalize_text(edge.get("target"))
    relation = _normalize_text(edge.get("label") or edge.get("type") or edge.get("properties", {}).get("label")) or "\u5173\u8054"
    source_label = _extract_node_label(node_index.get(source_id, {})) or source_id or "\u6e90\u8282\u70b9"
    target_label = _extract_node_label(node_index.get(target_id, {})) or target_id or "\u76ee\u6807\u8282\u70b9"
    return f"{source_label} --{relation}--> {target_label}"


def _relevant_relation_summaries(
    *,
    subject_node: dict[str, Any] | None,
    object_node: dict[str, Any] | None,
    node_index: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
) -> list[str]:
    subject_graph_node_id = _normalize_text(subject_node.get("graph_node_id")) if isinstance(subject_node, dict) else ""
    object_graph_node_id = _normalize_text(object_node.get("graph_node_id")) if isinstance(object_node, dict) else ""
    if not subject_graph_node_id:
        return []

    summaries: list[str] = []
    for edge in edges:
        source_id = _normalize_text(edge.get("source"))
        target_id = _normalize_text(edge.get("target"))
        if object_graph_node_id:
            if {source_id, target_id} != {subject_graph_node_id, object_graph_node_id}:
                continue
            summaries.append(_edge_summary(edge, node_index=node_index))
            continue
        if subject_graph_node_id not in {source_id, target_id}:
            continue
        summaries.append(_edge_summary(edge, node_index=node_index))

    return _unique_strings(summaries)[:6]


def _relevant_participant_mappings(
    candidate: dict[str, Any],
    *,
    subject_node: dict[str, Any] | None,
    object_node: dict[str, Any] | None,
    raw_graph_payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    payload = raw_graph_payload if isinstance(raw_graph_payload, dict) else {}
    participant_mappings = payload.get("participant_mappings")
    if not isinstance(participant_mappings, list):
        return []

    subject_graph_node_id = _normalize_text(subject_node.get("graph_node_id")) if isinstance(subject_node, dict) else ""
    object_graph_node_id = _normalize_text(object_node.get("graph_node_id")) if isinstance(object_node, dict) else ""
    subject_conversation_id = _positive_int(subject_node.get("conversation_id")) if isinstance(subject_node, dict) else 0
    object_conversation_id = _positive_int(object_node.get("conversation_id")) if isinstance(object_node, dict) else 0
    subject_raw_label = _normalize_key(subject_node.get("raw_label")) if isinstance(subject_node, dict) else ""
    object_raw_label = _normalize_key(object_node.get("raw_label")) if isinstance(object_node, dict) else ""
    recommended_participant_id = _positive_int(
        candidate.get("recommended_participant_id") or candidate.get("existing_participant_id")
    )

    matches: list[dict[str, Any]] = []
    for mapping in participant_mappings:
        if not isinstance(mapping, dict):
            continue
        mapping_graph_node_id = _normalize_text(mapping.get("graph_node_id"))
        mapping_conversation_id = _positive_int(mapping.get("conversation_id"))
        mapping_raw_label = _normalize_key(mapping.get("raw_label"))
        mapping_participant_id = _positive_int(mapping.get("participant_id"))
        if recommended_participant_id > 0 and mapping_participant_id == recommended_participant_id:
            matches.append(mapping)
            continue
        if subject_graph_node_id and mapping_graph_node_id == subject_graph_node_id:
            matches.append(mapping)
            continue
        if object_graph_node_id and mapping_graph_node_id == object_graph_node_id:
            matches.append(mapping)
            continue
        if subject_conversation_id > 0 and mapping_conversation_id == subject_conversation_id and mapping_raw_label == subject_raw_label:
            matches.append(mapping)
            continue
        if object_conversation_id > 0 and mapping_conversation_id == object_conversation_id and mapping_raw_label == object_raw_label:
            matches.append(mapping)

    return matches[:6]


def _relevant_prior_decisions(
    *,
    subject_node: dict[str, Any] | None,
    object_node: dict[str, Any] | None,
    raw_graph_payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    payload = raw_graph_payload if isinstance(raw_graph_payload, dict) else {}
    decisions = payload.get("entity_resolution_decisions")
    if not isinstance(decisions, list):
        return []

    pair_key = _decision_pair_key_for_candidate(subject_node, object_node)
    subject_graph_node_id = _normalize_text(subject_node.get("graph_node_id")) if isinstance(subject_node, dict) else ""
    results: list[dict[str, Any]] = []

    for decision in decisions:
        if not isinstance(decision, dict):
            continue
        source_graph_node_id = _normalize_text(decision.get("source_graph_node_id"))
        if pair_key and source_graph_node_id == pair_key:
            results.append(decision)
            continue
        if subject_graph_node_id and source_graph_node_id == subject_graph_node_id:
            results.append(decision)

    return results[:4]


def _build_recent_facts(
    raw_graph_payload: dict[str, Any] | None,
    *,
    summary_text: str,
) -> list[str]:
    facts: list[str] = []
    if summary_text and summary_text != _SUMMARY_FALLBACK_TEXT:
        facts.append(summary_text)

    raw_graph = raw_graph_payload.get("graph") if isinstance(raw_graph_payload, dict) else {}
    raw_nodes = raw_graph.get("nodes") if isinstance(raw_graph, dict) else []
    for node in raw_nodes if isinstance(raw_nodes, list) else []:
        if not isinstance(node, dict):
            continue
        node_type = _extract_node_type(node)
        if node_type not in _CLASSIFIER_EXCLUDED_NODE_TYPES:
            continue
        label = _extract_node_label(node)
        if label:
            facts.append(label)

    return _unique_strings(facts)[:8]


def _build_candidate_evidence(
    raw_graph_payload: dict[str, Any] | None,
    display_graph: dict[str, Any] | None,
    *,
    dossier_id: int,
    summary_text: str = "",
) -> dict[str, Any]:
    payload = raw_graph_payload if isinstance(raw_graph_payload, dict) else {}
    participant_candidates = payload.get("participant_candidates")
    confirmed_participants = payload.get("confirmed_participants")
    node_index, edges = _build_raw_graph_indexes(raw_graph_payload)

    evidence_candidates: list[dict[str, Any]] = []
    for candidate in participant_candidates if isinstance(participant_candidates, list) else []:
        if not isinstance(candidate, dict):
            continue
        candidate_key = _normalize_text(candidate.get("candidate_key") or candidate.get("task_key"))
        if not candidate_key:
            continue

        subject_node, object_node = _candidate_subject_and_object(
            candidate,
            raw_graph_payload=raw_graph_payload,
        )
        candidate_conversation_ids = _sorted_unique_ints(
            [
                _positive_int(subject_node.get("conversation_id")) if isinstance(subject_node, dict) else 0,
                _positive_int(object_node.get("conversation_id")) if isinstance(object_node, dict) else 0,
            ]
        )
        evidence_candidates.append(
            {
                "candidate_key": candidate_key,
                "task_type_rule_hint": _normalize_candidate_task_type(candidate.get("task_type")),
                "subject": subject_node,
                "object": object_node,
                "evidence": {
                    "co_occurrence_relations": _relevant_relation_summaries(
                        subject_node=subject_node,
                        object_node=object_node,
                        node_index=node_index,
                        edges=edges,
                    ),
                    "shared_conversation_ids": candidate_conversation_ids,
                    "participant_mappings": _relevant_participant_mappings(
                        candidate,
                        subject_node=subject_node,
                        object_node=object_node,
                        raw_graph_payload=raw_graph_payload,
                    ),
                    "prior_decisions": _relevant_prior_decisions(
                        subject_node=subject_node,
                        object_node=object_node,
                        raw_graph_payload=raw_graph_payload,
                    ),
                },
            }
        )

    return {
        "dossier_id": dossier_id,
        "candidates": evidence_candidates,
        "confirmed_participants": confirmed_participants if isinstance(confirmed_participants, list) else [],
        "recent_facts": _build_recent_facts(raw_graph_payload, summary_text=summary_text),
    }


def _candidate_prior_decision_types(candidate: dict[str, Any]) -> set[str]:
    evidence = candidate.get("evidence") if isinstance(candidate.get("evidence"), dict) else {}
    prior_decisions = evidence.get("prior_decisions") if isinstance(evidence.get("prior_decisions"), list) else []
    return {
        _normalize_key(decision.get("decision_type"))
        for decision in prior_decisions
        if isinstance(decision, dict) and _normalize_key(decision.get("decision_type"))
    }


def _default_classifier_evidence_summary(candidate: dict[str, Any]) -> str:
    subject = candidate.get("subject") if isinstance(candidate.get("subject"), dict) else {}
    object_node = candidate.get("object") if isinstance(candidate.get("object"), dict) else {}
    subject_label = _normalize_text(subject.get("graph_node_label") or subject.get("raw_label"))
    object_label = _normalize_text(object_node.get("graph_node_label") or object_node.get("raw_label"))
    rule_hint = _normalize_candidate_task_type(candidate.get("task_type_rule_hint"))
    if subject_label and object_label:
        return f"{subject_label} -> {object_label} ({rule_hint or 'candidate'})"
    return f"{subject_label or object_label or candidate.get('candidate_key') or 'candidate'} ({rule_hint or 'candidate'})"


def _normalize_classifier_node(
    value: Any,
    *,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    node = value if isinstance(value, dict) else {}
    fallback_node = fallback if isinstance(fallback, dict) else {}
    payload = _node_payload_from_candidate_node(
        {
            "conversation_id": node.get("conversation_id") or fallback_node.get("conversation_id"),
            "graph_node_id": node.get("graph_node_id") or fallback_node.get("graph_node_id"),
            "raw_label": node.get("raw_label") or fallback_node.get("raw_label"),
            "graph_node_label": node.get("graph_node_label") or fallback_node.get("graph_node_label"),
            "graph_node_type": node.get("graph_node_type") or fallback_node.get("graph_node_type"),
            "entity_type": node.get("entity_type") or fallback_node.get("graph_node_type"),
        },
        fallback_label=_normalize_text(fallback_node.get("raw_label") or fallback_node.get("graph_node_label")),
        fallback_conversation_id=_positive_int(fallback_node.get("conversation_id")),
    )
    return payload


def _apply_classifier_task_guards(
    task: dict[str, Any],
    *,
    candidate: dict[str, Any],
) -> dict[str, Any] | None:
    task_type = _normalize_candidate_task_type(task.get("task_type"))
    subject = task.get("subject") if isinstance(task.get("subject"), dict) else {}
    object_node = task.get("object") if isinstance(task.get("object"), dict) else {}
    prior_decision_types = _candidate_prior_decision_types(candidate)
    subject_type = _normalize_key(subject.get("graph_node_type"))
    object_type = _normalize_key(object_node.get("graph_node_type"))

    if subject_type in _CLASSIFIER_EXCLUDED_NODE_TYPES or object_type in _CLASSIFIER_EXCLUDED_NODE_TYPES:
        task["task_type"] = "not_actionable"
        task["should_show_to_user"] = False
        return task

    if task_type == "identity" and (subject.get("is_role_person") or object_node.get("is_role_person")):
        if subject.get("is_organization") or object_node.get("is_organization"):
            task["task_type"] = "affiliation_relation"
            task["should_show_to_user"] = True
        else:
            task["task_type"] = "relation_only"
            task["should_show_to_user"] = False
        return task

    if task_type == "identity" and (subject.get("is_self_anchor") or object_node.get("is_self_anchor")):
        task["task_type"] = "party_naming"
        task["should_show_to_user"] = True
        return task

    if task_type == "identity" and prior_decision_types & {"same", "not_same", "not_actionable"}:
        return None

    if task_type == "affiliation_relation" and prior_decision_types & {"not_same", "not_actionable"}:
        return None

    if _normalize_candidate_task_type(task.get("task_type")) == "not_actionable":
        task["should_show_to_user"] = False
    if _normalize_candidate_task_type(task.get("task_type")) == "relation_only":
        task["should_show_to_user"] = False

    return task


def _normalize_classifier_tasks(
    parsed: dict[str, Any],
    *,
    evidence: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence_lookup = {
        _normalize_text(candidate.get("candidate_key")): candidate
        for candidate in evidence.get("candidates", [])
        if isinstance(candidate, dict) and _normalize_text(candidate.get("candidate_key"))
    }
    normalized_tasks: list[dict[str, Any]] = []
    seen_candidate_keys: set[str] = set()

    raw_tasks = parsed.get("tasks") if isinstance(parsed.get("tasks"), list) else []
    for raw_task in raw_tasks:
        if not isinstance(raw_task, dict):
            continue
        candidate_key = _normalize_text(raw_task.get("candidate_key"))
        if not candidate_key or candidate_key in seen_candidate_keys:
            continue
        candidate = evidence_lookup.get(candidate_key)
        if candidate is None:
            continue

        task_type = _normalize_candidate_task_type(raw_task.get("task_type"))
        if task_type not in _CLASSIFIER_SUPPORTED_TASK_TYPES:
            continue

        confidence = _normalize_key(raw_task.get("confidence")) or "low"
        if confidence not in _CLASSIFIER_SUPPORTED_CONFIDENCE:
            confidence = "low"

        normalized_task = {
            "task_type": task_type,
            "confidence": confidence,
            "should_show_to_user": _coerce_bool(
                raw_task.get("should_show_to_user"),
                _CLASSIFIER_SHOW_TO_USER_DEFAULTS.get(task_type, False),
            ),
            "reason": _normalize_text(raw_task.get("reason")) or _default_classifier_evidence_summary(candidate),
            "subject": _normalize_classifier_node(
                raw_task.get("subject"),
                fallback=candidate.get("subject"),
            ),
            "object": _normalize_classifier_node(
                raw_task.get("object"),
                fallback=candidate.get("object"),
            ),
            "evidence_summary": _normalize_text(raw_task.get("evidence_summary")) or _default_classifier_evidence_summary(candidate),
            "candidate_key": candidate_key,
        }
        guarded_task = _apply_classifier_task_guards(
            normalized_task,
            candidate=candidate,
        )
        if guarded_task is None:
            continue
        seen_candidate_keys.add(candidate_key)
        normalized_tasks.append(guarded_task)

    return normalized_tasks


def _build_rule_fallback_task(candidate: dict[str, Any]) -> dict[str, Any] | None:
    subject = candidate.get("subject") if isinstance(candidate.get("subject"), dict) else {}
    object_node = candidate.get("object") if isinstance(candidate.get("object"), dict) else {}
    candidate_key = _normalize_text(candidate.get("candidate_key"))
    if not candidate_key or not subject:
        return None

    rule_hint = _normalize_candidate_task_type(candidate.get("task_type_rule_hint"))
    prior_decision_types = _candidate_prior_decision_types(candidate)
    task_type = rule_hint

    if subject.get("is_self_anchor"):
        task_type = "party_naming"
    elif rule_hint == "raw_label_resolution":
        return None

    if task_type == "identity" and prior_decision_types & {"same", "not_same", "not_actionable"}:
        return None
    if task_type == "affiliation_relation" and prior_decision_types & {"not_same", "not_actionable"}:
        return None
    if task_type == "identity" and (subject.get("is_role_person") or object_node.get("is_role_person")):
        task_type = "affiliation_relation" if subject.get("is_organization") or object_node.get("is_organization") else "relation_only"

    if task_type not in _CLASSIFIER_SUPPORTED_TASK_TYPES:
        return None

    confidence = _normalize_key(candidate.get("confidence")) or (
        "high" if task_type in {"identity", "party_naming"} else "medium"
    )
    if confidence not in _CLASSIFIER_SUPPORTED_CONFIDENCE:
        confidence = "low"

    task = {
        "task_type": task_type,
        "confidence": confidence,
        "should_show_to_user": _CLASSIFIER_SHOW_TO_USER_DEFAULTS.get(task_type, False),
        "reason": _normalize_text(candidate.get("reason")) or _default_classifier_evidence_summary(candidate),
        "subject": subject,
        "object": object_node or None,
        "evidence_summary": _default_classifier_evidence_summary(candidate),
        "candidate_key": candidate_key,
    }
    return _apply_classifier_task_guards(task, candidate=candidate)


def _build_rule_fallback_classifier_result(
    evidence: dict[str, Any],
    *,
    fallback_reason: str,
) -> dict[str, Any]:
    tasks: list[dict[str, Any]] = []
    for candidate in evidence.get("candidates", []) if isinstance(evidence.get("candidates"), list) else []:
        if not isinstance(candidate, dict):
            continue
        task = _build_rule_fallback_task(candidate)
        if task is not None:
            tasks.append(task)

    return {
        "tasks": tasks,
        "provider": _CLASSIFIER_RULE_PROVIDER,
        "model": _CLASSIFIER_RULE_MODEL,
        "fallback_used": True,
        "fallback_reason": fallback_reason,
    }


def _build_classifier_prompt_messages(evidence: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": _CLASSIFIER_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(evidence, ensure_ascii=False, indent=2)},
    ]


async def _call_dossier_intelligence_classifier_llm(
    *,
    messages: list[dict[str, str]],
    provider_hint: str | None = None,
) -> dict[str, str]:
    provider = _resolve_provider_name(provider_hint)
    if provider == "openai":
        return await _call_openai_json_model(messages)
    if provider == "zhipu":
        return await _call_zhipu_json_model(messages)
    raise RuntimeError("No LLM provider configured for dossier intelligence candidate classifier.")


async def _classify_candidates_with_agent(
    evidence: dict[str, Any],
    *,
    use_llm: bool = True,
    provider_hint: str | None = None,
) -> dict[str, Any]:
    candidates = evidence.get("candidates") if isinstance(evidence, dict) else []
    if not isinstance(candidates, list) or not candidates:
        return {
            "tasks": [],
            "provider": _CLASSIFIER_RULE_PROVIDER,
            "model": _CLASSIFIER_RULE_MODEL,
            "fallback_used": False,
            "fallback_reason": None,
        }

    if not use_llm:
        return _build_rule_fallback_classifier_result(
            evidence,
            fallback_reason="llm_disabled",
        )

    messages = _build_classifier_prompt_messages(evidence)
    try:
        llm_response = await _call_dossier_intelligence_classifier_llm(
            messages=messages,
            provider_hint=provider_hint,
        )
    except Exception as exc:
        logger.warning("Dossier intelligence candidate classifier LLM call failed: %s", exc)
        return _build_rule_fallback_classifier_result(
            evidence,
            fallback_reason="llm_call_failed",
        )

    parsed = _extract_json_object(llm_response.get("content", ""))
    if parsed is None:
        logger.warning("Dossier intelligence candidate classifier returned invalid JSON payload")
        return _build_rule_fallback_classifier_result(
            evidence,
            fallback_reason="invalid_llm_json",
        )

    return {
        "tasks": _normalize_classifier_tasks(parsed, evidence=evidence),
        "provider": llm_response.get("provider", _CLASSIFIER_RULE_PROVIDER),
        "model": llm_response.get("model", _CLASSIFIER_RULE_MODEL),
        "fallback_used": False,
        "fallback_reason": None,
    }


def _confirmation_task_dedupe_key(task: dict[str, Any]) -> tuple[str, str]:
    task_type = _normalize_candidate_task_type(task.get("task_type"))
    if task_type == "party_naming":
        return task_type, _normalize_text(task.get("current_label"))
    candidate_key = _normalize_text(task.get("candidate_key"))
    if candidate_key:
        return task_type, candidate_key
    if task_type == "entity_identity":
        source_node = task.get("source_node") if isinstance(task.get("source_node"), dict) else {}
        target_participant = task.get("target_participant") if isinstance(task.get("target_participant"), dict) else {}
        return (
            task_type,
            f"{_normalize_text(source_node.get('graph_node_id'))}:{_positive_int(target_participant.get('participant_id'))}",
        )
    subject_node = task.get("subject_node") if isinstance(task.get("subject_node"), dict) else {}
    object_node = task.get("object_node") if isinstance(task.get("object_node"), dict) else {}
    return (
        task_type,
        f"{_normalize_text(subject_node.get('graph_node_id') or subject_node.get('raw_label'))}:{_normalize_text(object_node.get('graph_node_id') or object_node.get('raw_label'))}",
    )


def _classifier_task_to_confirmation_task(
    task: dict[str, Any],
    *,
    origin: str,
) -> dict[str, Any]:
    task_type = _normalize_candidate_task_type(task.get("task_type"))
    subject = task.get("subject") if isinstance(task.get("subject"), dict) else {}
    object_node = task.get("object") if isinstance(task.get("object"), dict) else {}
    reason = _normalize_text(task.get("reason"))
    evidence_summary = _normalize_text(task.get("evidence_summary"))
    confidence = _normalize_key(task.get("confidence")) or "low"
    should_show = _coerce_bool(
        task.get("should_show_to_user"),
        _CLASSIFIER_SHOW_TO_USER_DEFAULTS.get(task_type, False),
    )

    if task_type == "party_naming":
        current_label = _normalize_text(subject.get("graph_node_label") or subject.get("raw_label"))
        return {
            "task_type": "party_naming",
            "subject": "candidate_classifier",
            "current_label": current_label,
            "suggested_labels": list(_SELF_NAMING_SUGGESTED_LABELS),
            "reason": reason,
            "requires_user_confirmation": True,
            "source": "dossier_intelligence_classifier",
            "origin": origin,
            "confidence": confidence,
            "should_show_to_user": should_show,
            "candidate_key": _normalize_text(task.get("candidate_key")),
            "evidence": {
                "raw_labels": _unique_strings([current_label]),
                "source_conversation_ids": _sorted_unique_ints([_positive_int(subject.get("conversation_id"))]),
                "raw_node_ids": _sorted_unique_strings([_normalize_text(subject.get("graph_node_id"))]),
                "summary": evidence_summary,
            },
        }

    return {
        "task_type": task_type,
        "candidate_key": _normalize_text(task.get("candidate_key")),
        "reason": reason,
        "confidence": confidence,
        "should_show_to_user": should_show,
        "origin": origin,
        "source": "dossier_intelligence_classifier",
        "requires_user_confirmation": task_type in {"identity", "affiliation_relation", "party_naming"},
        "evidence_summary": evidence_summary,
        "subject_node": subject,
        "object_node": object_node or None,
    }


def _dedupe_confirmation_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for task in tasks:
        if not isinstance(task, dict):
            continue
        key = _confirmation_task_dedupe_key(task)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(task)
    return deduped


async def _load_display_graph_context(
    db: AsyncSession,
    *,
    user_id: int,
    dossier: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    conversations = await list_dossier_conversations(db, user_id, dossier.id)
    conversation_ids = [
        conversation.id
        for conversation in conversations
        if isinstance(getattr(conversation, "id", None), int) and conversation.id > 0
    ]
    participant_aliases = serialize_dossier(dossier).get("participant_aliases", {})
    participants = await list_dossier_participants(db, user_id, dossier.id)
    mentions = await list_dossier_participant_mentions(db, user_id, dossier.id)
    decisions = await list_dossier_entity_resolution_decisions(db, user_id, dossier.id)
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
    raw_graph_payload["entity_resolution_decisions"] = [
        serialize_dossier_entity_resolution_decision(decision)
        for decision in decisions
    ]
    raw_graph_payload["participant_candidates"] = build_dossier_participant_candidates(
        graph_payload=raw_graph_payload,
        participants=participants,
        confirmed_mentions=mentions,
        participant_aliases=participant_aliases,
        resolution_decisions=decisions,
    )
    display_graph = build_dossier_display_graph(
        raw_graph=raw_graph_payload.get("graph"),
        confirmed_participants=raw_graph_payload.get("confirmed_participants"),
        participant_mappings=raw_graph_payload.get("participant_mappings"),
    )
    return raw_graph_payload, display_graph


async def build_dossier_intelligence(
    db: AsyncSession,
    *,
    current_user: User,
    dossier_id: int,
    conversation_id: int | None = None,
    include_display_graph: bool = True,
    use_llm: bool = True,
    provider_hint: str | None = None,
) -> dict[str, Any]:
    user_id = _extract_user_id(current_user)
    dossier = await get_dossier_for_user(db, user_id, dossier_id)
    if dossier is None:
        raise ValueError("Dossier not found")

    steps: list[dict[str, Any]] = [_step("load_dossier", "success", dossier_id=dossier.id)]
    warnings: list[str] = []
    display_graph_notes: list[str] = []
    raw_graph_payload: dict[str, Any] | None = None
    display_graph: dict[str, Any] | None = None

    if include_display_graph:
        try:
            raw_graph_payload, display_graph = await _load_display_graph_context(
                db,
                user_id=user_id,
                dossier=dossier,
            )
            display_graph_notes = _build_display_graph_notes(display_graph, raw_graph_payload)
            payload_warning = _normalize_text(raw_graph_payload.get("warning"))
            if payload_warning:
                warnings.append(payload_warning)
            display_stats = display_graph.get("stats") if isinstance(display_graph, dict) else {}
            steps.append(
                _step(
                    "load_display_graph",
                    "success",
                    display_node_count=int(display_stats.get("display_node_count") or 0),
                    merged_participant_count=int(
                        display_stats.get("merged_participant_count") or 0
                    ),
                )
            )
        except Exception as exc:
            warnings.append(f"display_graph_unavailable: {exc}")
            steps.append(
                _step(
                    "load_display_graph",
                    "warning",
                    reason=str(exc),
                )
            )
    else:
        steps.append(
            _step(
                "skip_display_graph",
                "skipped",
                reason="include_display_graph_false",
            )
        )

    if include_display_graph and (raw_graph_payload is None or display_graph is None):
        try:
            raw_graph_payload, display_graph = await _load_display_graph_context(
                db,
                user_id=user_id,
                dossier=dossier,
            )
        except Exception as exc:
            warnings.append(f"party_naming_context_unavailable: {exc}")
            raw_graph_payload = {}
            display_graph = {}

    summary_payload, summary_warnings = _build_summary_payload(dossier)
    warnings.extend(summary_warnings)
    steps.append(_step("build_summary", "success", source=summary_payload["source"]))

    confirmation_tasks: list[dict[str, Any]] = []
    party_naming_task = _build_party_naming_task(raw_graph_payload, display_graph)
    if party_naming_task is not None:
        confirmation_tasks.append(party_naming_task)
    steps.append(
        _step(
            "build_party_naming_tasks",
            "success",
            task_count=1 if party_naming_task is not None else 0,
        )
    )
    self_identity_tasks = _build_self_identity_tasks(raw_graph_payload)
    for task in self_identity_tasks:
        task.setdefault("origin", "self_anchor_rule")
        task.setdefault("should_show_to_user", True)
    confirmation_tasks.extend(self_identity_tasks)
    steps.append(
        _step(
            "build_self_identity_tasks",
            "success",
            task_count=len(self_identity_tasks),
        )
    )

    classifier_result = {
        "tasks": [],
        "provider": _CLASSIFIER_RULE_PROVIDER,
        "model": _CLASSIFIER_RULE_MODEL,
        "fallback_used": False,
        "fallback_reason": None,
    }
    if include_display_graph and raw_graph_payload is not None:
        candidate_evidence = _build_candidate_evidence(
            raw_graph_payload,
            display_graph,
            dossier_id=dossier.id,
            summary_text=summary_payload["text"],
        )
        steps.append(
            _step(
                "build_candidate_evidence",
                "success",
                candidate_count=len(candidate_evidence.get("candidates") or []),
            )
        )
        classifier_result = await _classify_candidates_with_agent(
            candidate_evidence,
            use_llm=use_llm,
            provider_hint=provider_hint,
        )
        classifier_origin = (
            "rule_fallback" if classifier_result.get("fallback_used") else "intelligence_classifier"
        )
        confirmation_tasks.extend(
            _classifier_task_to_confirmation_task(task, origin=classifier_origin)
            for task in classifier_result.get("tasks", [])
            if isinstance(task, dict)
        )
        steps.append(
            _step(
                "classify_candidates_with_agent",
                "warning" if classifier_result.get("fallback_used") else "success",
                task_count=len(classifier_result.get("tasks") or []),
                provider=classifier_result.get("provider"),
                model=classifier_result.get("model"),
                fallback_used=bool(classifier_result.get("fallback_used")),
                fallback_reason=classifier_result.get("fallback_reason"),
            )
        )
    else:
        steps.append(
            _step(
                "build_candidate_evidence",
                "skipped",
                reason="display_graph_unavailable",
            )
        )
        steps.append(
            _step(
                "classify_candidates_with_agent",
                "skipped",
                reason="display_graph_unavailable",
            )
        )

    if party_naming_task is not None:
        party_naming_task.setdefault("origin", "self_anchor_rule")
        party_naming_task.setdefault("confidence", "high")
        party_naming_task.setdefault("should_show_to_user", True)
    confirmation_tasks = _dedupe_confirmation_tasks(confirmation_tasks)
    steps.append(
        _step(
            "build_confirmation_tasks",
            "success",
            task_count=len(confirmation_tasks),
        )
    )

    return {
        "status": _STATUS_SUCCESS,
        "source": _SOURCE_NAME,
        "scope": {
            "user_id": user_id,
            "dossier_id": dossier.id,
            "conversation_id": conversation_id,
        },
        "summary": summary_payload,
        "confirmation_tasks": confirmation_tasks,
        "display_graph_notes": display_graph_notes,
        "candidate_classifier": {
            "provider": classifier_result.get("provider"),
            "model": classifier_result.get("model"),
            "fallback_used": bool(classifier_result.get("fallback_used")),
            "fallback_reason": classifier_result.get("fallback_reason"),
            "task_count": len(classifier_result.get("tasks") or []),
        },
        "trace": {
            "steps": steps,
            "warnings": warnings,
        },
        "provider": _PROVIDER_NAME,
        "model": _MODEL_NAME,
        "fallback_reason": _FALLBACK_REASON,
    }
