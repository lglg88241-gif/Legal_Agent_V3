from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Any

from app.services.dossier_hard_guards import identity_merge_blockers


SCHEMA_VERSION = "h9.dossier_entity_state.v1"
SOURCE = "dossier_cross_conversation_entity_agent"
PROVIDER = "rules"
MODEL = "dossier-entity-state-rules-v1"

_ORGANIZATION_SUFFIXES = (
    "有限责任公司",
    "股份有限公司",
    "集团有限公司",
    "集团公司",
    "有限公司",
    "公司",
    "集团",
    "企业",
)

_ROLE_TOKENS = {
    "经理",
    "hr",
    "人事",
    "主管",
    "主任",
    "代表",
    "法人",
    "负责人",
    "专员",
    "顾问",
    "律师",
    "老师",
}


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


def _normalized_name_variants(value: Any) -> set[str]:
    text = _normalize_text(value)
    if not text:
        return set()
    lowered = text.lower()
    collapsed = re.sub(r"[\s()（）\[\]【】,，.。:：;；\-_/]+", "", lowered)
    variants = {lowered, collapsed}
    for suffix in _ORGANIZATION_SUFFIXES:
        if collapsed.endswith(suffix) and len(collapsed) > len(suffix):
            variants.add(collapsed[: -len(suffix)])
    return {variant for variant in variants if variant}


def _strict_name_variants(value: Any) -> set[str]:
    text = _normalize_text(value)
    if not text:
        return set()
    lowered = text.lower()
    collapsed = re.sub(r"[\s()（）\[\]【】,，.。:：;；\-_/]+", "", lowered)
    return {variant for variant in {lowered, collapsed} if variant}


def _normalized_label_set(entity: dict[str, Any]) -> set[str]:
    values = [
        entity.get("canonical_label"),
        entity.get("label"),
        *(entity.get("aliases") or []),
        *(entity.get("source_labels") or []),
    ]
    normalized: set[str] = set()
    for value in values:
        normalized.update(_normalized_name_variants(value))
    return normalized


def _strict_label_set(entity: dict[str, Any]) -> set[str]:
    values = [
        entity.get("canonical_label"),
        entity.get("label"),
        *(entity.get("aliases") or []),
        *(entity.get("source_labels") or []),
    ]
    normalized: set[str] = set()
    for value in values:
        normalized.update(_strict_name_variants(value))
    return normalized


def _entity_type_family(entity_type: Any) -> str:
    normalized = _normalize_key(entity_type)
    if normalized in {"person", "natural_person", "human"}:
        return "person"
    if normalized in {"organization", "company", "employer", "unit", "department", "authority", "agency", "institution", "court", "arbitration_body"}:
        return "organization"
    if normalized in {"role", "representative", "hr", "manager", "manager_or_hr", "person_role", "person_or_role"}:
        return "role"
    return normalized or "unknown"


def _entity_role_tokens(entity: dict[str, Any]) -> set[str]:
    values = [entity.get("role"), *(entity.get("aliases") or []), *(entity.get("source_labels") or [])]
    tokens: set[str] = set()
    for value in values:
        normalized = _normalize_key(value)
        if not normalized:
            continue
        for token in _ROLE_TOKENS:
            if token in normalized:
                tokens.add(token)
    return tokens


def _label_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_labels = _normalized_label_set(left)
    right_labels = _normalized_label_set(right)
    if not left_labels or not right_labels:
        return 0.0
    if left_labels & right_labels:
        return 1.0
    best = 0.0
    for left_value in left_labels:
        for right_value in right_labels:
            best = max(best, SequenceMatcher(None, left_value, right_value).ratio())
    return best


def _role_compatibility(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_role = _normalize_key(left.get("role"))
    right_role = _normalize_key(right.get("role"))
    if left_role and right_role and left_role == right_role:
        return 1.0
    left_tokens = _entity_role_tokens(left)
    right_tokens = _entity_role_tokens(right)
    if left_tokens and right_tokens and left_tokens & right_tokens:
        return 0.8
    if not left_role or not right_role:
        return 0.4
    return 0.0


def _has_strict_label_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return bool(_strict_label_set(left) & _strict_label_set(right))


def _is_auto_merge_candidate(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    candidate_score: dict[str, Any],
) -> bool:
    if candidate_score.get("confidence") != "high":
        return False
    if candidate_score.get("type_family_left") != candidate_score.get("type_family_right"):
        return False
    if not _has_strict_label_overlap(left, right):
        return False
    if float(candidate_score.get("label_similarity") or 0) < 0.999:
        return False
    if float(candidate_score.get("role_score") or 0) <= 0:
        return False
    return True


def _score_same_entity_candidate(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_family = _entity_type_family(left.get("entity_type"))
    right_family = _entity_type_family(right.get("entity_type"))
    label_score = _label_similarity(left, right)
    role_score = _role_compatibility(left, right)
    source_overlap = set(_normalize_key(value) for value in left.get("source_labels", []) if _normalize_key(value)) & set(
        _normalize_key(value) for value in right.get("source_labels", []) if _normalize_key(value)
    )

    reasons: list[str] = []
    score = 0.0

    if left_family == right_family and left_family != "unknown":
        score += 0.25
        reasons.append(f"entity_type_family:{left_family}")
    elif left_family != right_family:
        reasons.append(f"entity_type_mismatch:{left_family}->{right_family}")

    if label_score >= 0.999:
        score += 0.5
        reasons.append("shared_normalized_label")
    elif label_score >= 0.9:
        score += 0.4
        reasons.append("very_high_label_similarity")
    elif label_score >= 0.75:
        score += 0.25
        reasons.append("high_label_similarity")
    elif label_score >= 0.6:
        score += 0.12
        reasons.append("moderate_label_similarity")

    if source_overlap:
        score += 0.15
        reasons.append("overlapping_source_labels")

    if role_score >= 0.8:
        score += 0.1
        reasons.append("role_alignment")
    elif role_score > 0:
        score += 0.05
        reasons.append("partial_role_alignment")

    left_mentions = len(left.get("mentions") or [])
    right_mentions = len(right.get("mentions") or [])
    if left_mentions > 0 and right_mentions > 0:
        score += 0.05
        reasons.append("both_have_mentions")

    score = round(min(score, 1.0), 4)
    high_confidence = (
        score >= 0.92
        and left_family == right_family
        and _has_strict_label_overlap(left, right)
    )
    confidence = "low"
    if high_confidence:
        confidence = "high"
    elif score >= 0.55:
        confidence = "medium"

    auto_merge_recommended = _is_auto_merge_candidate(
        left,
        right,
        candidate_score={
            "confidence": confidence,
            "label_similarity": label_score,
            "role_score": role_score,
            "type_family_left": left_family,
            "type_family_right": right_family,
        },
    )

    if auto_merge_recommended:
        reasons.append("strict_auto_merge_gate")

    return {
        "score": score,
        "confidence": confidence,
        "reasons": reasons,
        "label_similarity": round(label_score, 4),
        "role_score": round(role_score, 4),
        "type_family_left": left_family,
        "type_family_right": right_family,
        "auto_merge_recommended": auto_merge_recommended,
    }


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


_RESPONSIBLE_ENTITY_TYPES = {
    "person",
    "natural_person",
    "human",
    "organization",
    "company",
    "employer",
    "unit",
    "department",
    "authority",
    "agency",
    "institution",
    "arbitration_body",
    "court",
    "role",
    "representative",
    "hr",
    "manager",
    "manager_or_hr",
    "person_role",
    "person_or_role",
}

_FACT_ENTITY_TYPES = {
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
    "notice",
    "result",
    "consequence",
    "social_insurance",
    "economic_loss",
    "compensation",
    "damage",
}

_FACT_LABEL_HINTS = {
    "合同",
    "劳动合同",
    "社保",
    "社会保险",
    "工资",
    "月薪",
    "薪资",
    "薪酬",
    "通知",
    "确认书",
    "证据",
    "证明",
    "时间",
    "日期",
    "赔偿",
    "赔偿金",
    "补偿",
    "后果",
    "结果",
    "请求",
    "申请书",
    "仲裁申请书",
    "经济损失",
    "损失",
    "违约金",
    "咨询主题",
    "案由",
    "纠纷",
    "劳动纠纷",
    "争议",
    "争议类型",
    "案件类型",
}


_FACT_LABEL_HINTS = _FACT_LABEL_HINTS | {
    "\u6253\u5361",
    "\u8003\u52e4",
    "\u51fa\u52e4",
    "\u7b7e\u5230",
    "\u7b7e\u9000",
    "\u8865\u7b7e",
    "\u4e0a\u73ed",
    "\u52b3\u52a8\u5173\u7cfb",
    "\u52b3\u52a1\u5173\u7cfb",
    "\u7528\u5de5\u5173\u7cfb",
    "\u4e8b\u5b9e\u52b3\u52a8\u5173\u7cfb",
    "\u878d\u8d44\u5230\u8d26",
    "\u5230\u8d26",
    "\u5173\u7cfb",
    "\u534f\u8bae",
    "\u627f\u8bfa",
    "\u53d1\u653e",
    "\u62d6\u6b20",
    "\u8f9e\u9000",
    "\u89e3\u9664",
    "\u79bb\u804c",
    "\u5165\u804c",
    "\u7b7e\u7ea6",
    "\u7f34\u7eb3",
    "\u6761\u4ef6",
    "\u539f\u56e0",
    "\u4e8b\u5b9e",
    "\u60c5\u51b5",
    "\u95ee\u9898",
    "\u91d1\u989d",
}

_UNRESOLVED_PARTY_LABELS = {
    "我",
    "本人",
    "自己",
    "用户",
    "当事人",
    "劳动者",
    "劳动者本人",
    "申请人",
    "我方",
    "我方当事人",
    "当前会话当事人",
    "未解析当事人",
}


def _looks_like_responsible_label(label: str) -> bool:
    normalized = _normalize_text(label)
    if not normalized or _looks_like_duration_or_time_quantity(normalized) or any(hint in normalized for hint in _FACT_LABEL_HINTS):
        return False
    if any(normalized.endswith(suffix) or suffix in normalized for suffix in _ORGANIZATION_SUFFIXES):
        return True
    if any(token in _normalize_key(normalized) for token in _ROLE_TOKENS):
        return True
    if len(normalized) in {2, 3} and all("\u4e00" <= char <= "\u9fff" for char in normalized):
        return True
    return len(normalized) <= 8 and any(char.isalpha() for char in normalized) and any("\u4e00" <= char <= "\u9fff" for char in normalized)


def _is_responsible_entity(label: str, entity_type: str) -> bool:
    if _normalize_text(label) in _UNRESOLVED_PARTY_LABELS:
        return False
    normalized_type = _normalize_key(entity_type)
    if normalized_type in {"客体", "事件", "后果"}:
        return False
    if normalized_type == "主体":
        normalized_type = "unknown"
    elif normalized_type == "dynamiccaseentity":
        return False
    if normalized_type in _FACT_ENTITY_TYPES:
        return False
    if _looks_like_duration_or_time_quantity(label) or any(hint in label for hint in _FACT_LABEL_HINTS):
        return False
    if normalized_type in _RESPONSIBLE_ENTITY_TYPES:
        return True
    return normalized_type == "unknown" and _looks_like_responsible_label(label)


def _looks_like_duration_or_time_quantity(label: str) -> bool:
    normalized = _normalize_text(label)
    if not normalized:
        return False
    if any(char.isdigit() for char in normalized) and any(unit in normalized for unit in ("年", "月", "日", "天", "周", "星期", "个月")):
        return True
    return bool(
        re.fullmatch(r"(?:好几|好些|多个|数个|几个|若干|很多|许多|多|数|半|一|两|三|四|五|六|七|八|九|十)[年月日天周星期个]*", normalized)
        or re.fullmatch(r".*(?:几个月|数月|多月|好几个月|好些个月|若干个月|很久|长期|一段时间|一阵子|一段日子).*", normalized)
    )


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
    source = _normalize_text(entity.get("source")) or "conversation_entity_state"
    if source == "fallback_rules":
        return None
    if role == "current_party" and confidence == "low" and label in {"当前会话当事人", "未解析当事人"}:
        return None
    if not _is_responsible_entity(label, entity_type):
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
        "source": source,
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
    score = _score_same_entity_candidate(left, right)
    return score["label_similarity"] >= 0.75 or bool(
        set(_normalize_key(value) for value in left.get("source_labels", []) if _normalize_key(value))
        & set(_normalize_key(value) for value in right.get("source_labels", []) if _normalize_key(value))
    )


def _should_auto_merge_pair(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    decision_index: dict[str, str],
    dossier_id: int | None,
) -> bool:
    base_task_key = _task_key(left["id"], right["id"])
    canonical_task_key = _task_key(
        _singleton_canonical_id(left, dossier_id=dossier_id),
        _singleton_canonical_id(right, dossier_id=dossier_id),
    )
    if decision_index.get(base_task_key) == "not_same" or decision_index.get(canonical_task_key) == "not_same":
        return False
    candidate_score = _score_same_entity_candidate(left, right)
    if not candidate_score["auto_merge_recommended"]:
        return False
    blockers = identity_merge_blockers(
        left,
        right,
        requires_user_confirmation=False,
        user_confirmed=False,
        source_dossier_id=dossier_id,
        target_dossier_id=dossier_id,
    )
    return not blockers


def _can_surface_identity_candidate(left: dict[str, Any], right: dict[str, Any]) -> bool:
    blockers = identity_merge_blockers(
        left,
        right,
        requires_user_confirmation=False,
        user_confirmed=False,
    )
    hard_blockers = set(blockers) - {"role_person_not_auto_identity_merge"}
    return not hard_blockers


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
            candidate_score = _score_same_entity_candidate(left, right)
            if candidate_score["score"] < 0.55:
                continue
            if candidate_score["confidence"] not in {"high", "medium"}:
                continue
            if not _can_surface_identity_candidate(left, right):
                continue
            task_key = _task_key(left["id"], right["id"])
            if decision_index.get(task_key) in {"same", "not_same"}:
                continue
            is_high_priority = candidate_score["confidence"] == "high" or bool(
                candidate_score["auto_merge_recommended"]
            )
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
                    "reason": (
                        "系统高度怀疑不同会话中的主体为同一实体，但仍需要用户确认后归并。"
                        if is_high_priority
                        else "不同会话中的主体存在较高同一实体相似度，需要用户确认是否归并。"
                    ),
                    "confidence": candidate_score["confidence"],
                    "score": candidate_score["score"],
                    "scoring_reasons": candidate_score["reasons"],
                    "label_similarity": candidate_score["label_similarity"],
                    "role_score": candidate_score["role_score"],
                    "auto_merge_recommended": candidate_score["auto_merge_recommended"],
                    "priority": "high" if is_high_priority else "normal",
                    "high_priority": is_high_priority,
                    "allowed_actions": ["confirm", "reject", "defer"],
                    "requires_user_confirmation": True,
                }
            )
    tasks.sort(
        key=lambda item: (
            0 if item.get("high_priority") else 1,
            -float(item.get("score") or 0),
            str(item.get("task_key")),
        )
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
            user_confirmed_same = decision_index.get(base_task_key) == "same" or decision_index.get(canonical_task_key) == "same"
            auto_merge_same = False
            if not user_confirmed_same:
                auto_merge_same = _should_auto_merge_pair(
                    left,
                    right,
                    decision_index=decision_index,
                    dossier_id=dossier_id,
                )
            if not user_confirmed_same and not auto_merge_same:
                continue
            blockers = identity_merge_blockers(
                left,
                right,
                requires_user_confirmation=not (user_confirmed_same or auto_merge_same),
                user_confirmed=user_confirmed_same,
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
