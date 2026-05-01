from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.core.llm_factory import create_cloud_llm


logger = logging.getLogger(__name__)

PENDING_CLARIFICATION_TYPE = "subject_reference_resolution"
FACT_MEMORY_QA_MODEL = "glm-4-flash"

_SCOPE_ALIASES = {
    "current_conversation_graph": "current_conversation_graph",
    "current_conversation_messages": "current_conversation_messages",
    "dossier_confirmed_entities": "dossier_confirmed_participants",
    "dossier_confirmed_participants": "dossier_confirmed_participants",
    "dossier_display_graph": "dossier_display_graph",
    "dossier_cross_conversation_graph": "dossier_cross_conversation_candidates",
    "dossier_cross_conversation_candidates": "dossier_cross_conversation_candidates",
}

_DEFAULT_SCOPE_ORDER = [
    "current_conversation_graph",
    "current_conversation_messages",
    "dossier_confirmed_participants",
    "dossier_display_graph",
    "dossier_cross_conversation_candidates",
]

_SELF_ANCHOR_LABELS = {
    "我",
    "本人",
    "我本人",
    "当事人",
    "劳动者本人",
    "自己",
    "申请人",
    "我方",
}
_GENERIC_ORGANIZATION_LABELS = {
    "公司",
    "单位",
    "那家公司",
    "这家公司",
    "该公司",
    "那个单位",
    "这个单位",
    "该单位",
}
_ORGANIZATION_HINT_TOKENS = (
    "公司",
    "单位",
    "集团",
    "企业",
    "科技",
    "厂",
    "医院",
    "学校",
    "银行",
)
_NON_RESPONSIBLE_ENTITY_TYPES = {
    "客体",
    "事件",
    "后果",
    "时间",
    "日期",
    "金额",
    "数额",
    "证据",
    "文书",
    "材料",
    "time",
    "date",
    "amount",
    "money",
    "document",
    "evidence",
}
_CLARIFICATION_PREFIX_RE = re.compile(r"^(?:是|就是|指的是|说的是|我说的是|应该是|是指)\s*")
_FACT_MEMORY_SYSTEM_PROMPT = """你是案卷事实记忆问答智能体，不提供法律建议。

你的任务是只根据输入的证据包回答用户关于已记录案情事实的追问。

严格要求：
1. 只能输出 JSON object，不要输出 markdown，不要输出解释文字。
2. 只能使用 evidence 中的 current_conversation_graph_facts、recent_messages、dossier_graph_facts。
3. 优先使用 current_conversation_graph_facts 和 recent_messages；只有当前会话证据不足时，才使用 dossier_graph_facts。
4. 不要把金额、日期、工资、拖欠工资、经济损失、证据、事件、后果当作责任主体候选。
5. 但这些非主体节点可以作为事实答案或证据使用，例如金额、时间、事件、证据名称。
6. 如果证据足以回答，decision=answer，并给出自然、简短的中文回答。
7. 如果只能从案卷其他会话回答，answer 中要提示“当前会话证据不足，结合案卷其他会话记录...”。
8. 如果证据冲突或无法确定，decision=clarify，answer 中说明还缺什么，不要编造。

JSON 格式：
{
  "decision": "answer" | "clarify" | "no_answer",
  "answer": "...",
  "confidence": 0.0,
  "used_evidence_ids": ["..."],
  "selected_scope": "current_conversation_graph" | "current_conversation_messages" | "dossier_display_graph" | "",
  "reason": "..."
}
"""


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _normalize_key(value: Any) -> str:
    return _normalize_text(value).lower()


def normalize_subject_reference_scope_order(raw_scope_order: list[Any] | None) -> list[str]:
    normalized: list[str] = []
    for item in raw_scope_order if isinstance(raw_scope_order, list) else []:
        scope_name = _SCOPE_ALIASES.get(_normalize_key(item))
        if scope_name and scope_name not in normalized:
            normalized.append(scope_name)
    if normalized:
        return normalized
    return list(_DEFAULT_SCOPE_ORDER)


def _target_type_matches(entity_type: Any, target_type: str) -> bool:
    normalized_entity_type = _normalize_key(entity_type)
    if target_type == "organization":
        return normalized_entity_type in {
            "organization",
            "company",
            "employer",
            "unit",
            "organization_entity",
        }
    if target_type == "person":
        return normalized_entity_type in {"person", "individual", "individual_person"}
    if target_type == "self":
        return normalized_entity_type in {"person", "individual", "party", "self"}
    if target_type == "role":
        return normalized_entity_type in {"role", "person", "organization"}
    return bool(normalized_entity_type)


def _mention_tokens(subject_reference: dict[str, Any]) -> list[str]:
    tokens = [
        _normalize_key(subject_reference.get("target_mention")),
        _normalize_key(subject_reference.get("mention")),
        _normalize_key(subject_reference.get("target_span")),
    ]
    return [token for token in tokens if token]


def _coerce_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _extract_node_conversation_id(node: dict[str, Any]) -> int:
    return _coerce_int(
        node.get("conversation_id")
        or node.get("properties", {}).get("conversation_id")
        or node.get("source_conversation_ids", [0])[0]
    )


def _scope_matches_conversation(
    scope_name: str,
    *,
    conversation_id: Any,
    current_conversation_id: int,
) -> bool:
    normalized_conversation_id = _coerce_int(conversation_id)
    if scope_name == "current_conversation_graph":
        return current_conversation_id > 0 and normalized_conversation_id == current_conversation_id
    if scope_name == "dossier_cross_conversation_candidates":
        return (
            current_conversation_id > 0
            and normalized_conversation_id > 0
            and normalized_conversation_id != current_conversation_id
        )
    return False


def _normalize_reference_type(value: Any) -> str:
    normalized = _normalize_key(value) or "unknown"
    if normalized in {"organization", "person", "self", "role"}:
        return normalized
    return "unknown"


def _normalize_pending_clarification(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if _normalize_key(value.get("type")) != PENDING_CLARIFICATION_TYPE:
        return None
    scope_attempted = normalize_subject_reference_scope_order(value.get("scope_attempted"))
    return {
        "type": PENDING_CLARIFICATION_TYPE,
        "target_mention": _normalize_text(value.get("target_mention")) or "这个主体",
        "reference_type": _normalize_reference_type(value.get("reference_type")),
        "previous_question": _normalize_text(value.get("previous_question")),
        "scope_attempted": scope_attempted,
        "expected_response": _normalize_text(value.get("expected_response")) or "entity_name_or_context",
        "pending_graph_update_input_text": _normalize_text(value.get("pending_graph_update_input_text")),
    }


def extract_pending_subject_reference_clarification(
    recent_messages: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    latest_assistant_message: dict[str, Any] | None = None
    for item in reversed(list(recent_messages or [])):
        if not isinstance(item, dict):
            continue
        if _normalize_key(item.get("role")) != "assistant":
            continue
        latest_assistant_message = item
        break
    if latest_assistant_message is None:
        return None
    metadata = (
        latest_assistant_message.get("metadata")
        if isinstance(latest_assistant_message.get("metadata"), dict)
        else {}
    )
    return _normalize_pending_clarification(metadata.get("pending_clarification"))


def looks_like_subject_reference_clarification_response(
    *,
    message: str,
    pending_clarification: dict[str, Any] | None,
) -> bool:
    if not isinstance(pending_clarification, dict):
        return False
    normalized = _normalize_text(message)
    if not normalized or len(normalized) > 24:
        return False
    if any(symbol in normalized for symbol in {"?", "？"}):
        return False
    if any(token in normalized for token in {"谁", "什么", "哪", "多少", "几", "怎么", "为什么", "吗", "嘛"}):
        return False
    return True


def _strip_clarification_prefix(text: str) -> str:
    normalized = _normalize_text(text)
    normalized = _CLARIFICATION_PREFIX_RE.sub("", normalized)
    return normalized.strip("，,。；;：: ")


def _is_self_anchor_label(value: str) -> bool:
    normalized = _normalize_key(value)
    if not normalized:
        return False
    compact = normalized.replace("，", "").replace(",", "")
    if compact in {_normalize_key(item).replace("，", "").replace(",", "") for item in _SELF_ANCHOR_LABELS}:
        return True
    segments = [
        item.strip()
        for item in re.split(r"[，,、/\s]+", normalized)
        if item.strip()
    ]
    return bool(segments) and all(segment in {_normalize_key(item) for item in _SELF_ANCHOR_LABELS} for segment in segments)


def _is_generic_organization_phrase(value: str) -> bool:
    normalized = _normalize_key(value)
    return normalized in {_normalize_key(item) for item in _GENERIC_ORGANIZATION_LABELS}


def _looks_like_organization_label(value: str) -> bool:
    normalized = _normalize_text(value)
    if not normalized:
        return False
    if _is_self_anchor_label(normalized):
        return False
    return any(token in normalized for token in _ORGANIZATION_HINT_TOKENS)


def _node_label(node: dict[str, Any]) -> str:
    if not isinstance(node, dict):
        return ""
    properties = node.get("properties") if isinstance(node.get("properties"), dict) else {}
    return _normalize_text(
        node.get("display_label")
        or node.get("participant_label")
        or node.get("raw_label")
        or node.get("label")
        or node.get("name")
        or properties.get("name")
        or properties.get("label")
    )


def _node_entity_type(node: dict[str, Any]) -> str:
    if not isinstance(node, dict):
        return ""
    properties = node.get("properties") if isinstance(node.get("properties"), dict) else {}
    return _normalize_text(
        node.get("entity_type")
        or node.get("type")
        or node.get("category")
        or node.get("graph_node_type")
        or node.get("participant_entity_type")
        or properties.get("entity_type")
        or properties.get("type")
        or properties.get("category")
        or properties.get("node_type")
    )


def _is_responsible_subject_node(node: dict[str, Any]) -> bool:
    entity_type = _normalize_key(_node_entity_type(node))
    if not entity_type:
        return False
    return entity_type not in {_normalize_key(item) for item in _NON_RESPONSIBLE_ENTITY_TYPES}


def _node_matches_reference_type(node: dict[str, Any], target_type: str) -> bool:
    label = _node_label(node)
    entity_type = _node_entity_type(node)
    if not label or not _is_responsible_subject_node(node):
        return False
    if target_type == "organization":
        return _target_type_matches(entity_type, target_type) or _looks_like_organization_label(label)
    if target_type in {"person", "self"}:
        if _target_type_matches(entity_type, target_type):
            return True
        return bool(label) and not _looks_like_organization_label(label) and len(label) in {2, 3, 4}
    if target_type == "role":
        return True
    return True


def _infer_clarification_entity_kind(message: str) -> tuple[str, str | None]:
    cleaned = _strip_clarification_prefix(message)
    if not cleaned:
        return "unknown", None
    if _is_self_anchor_label(cleaned):
        return "self", None
    if _is_generic_organization_phrase(cleaned):
        return "organization", None
    if _looks_like_organization_label(cleaned):
        return "organization", cleaned
    return "unknown", None


def _register_candidate(
    bucket: dict[str, dict[str, Any]],
    label: str,
    *,
    entity_type: str,
    score: int,
    reasons: list[str],
    scope_name: str,
    evidence_items: list[dict[str, Any]] | None = None,
) -> None:
    cleaned_label = _normalize_text(label)
    if not cleaned_label:
        return
    key = cleaned_label.lower()
    candidate = bucket.setdefault(
        key,
        {
            "label": cleaned_label,
            "entity_type": entity_type or "unknown",
            "score": 0,
            "reasons": [],
            "scope": scope_name,
            "evidence": [],
        },
    )
    candidate["score"] += max(0, score)
    for reason in reasons:
        if reason and reason not in candidate["reasons"]:
            candidate["reasons"].append(reason)
    for item in evidence_items or []:
        if item not in candidate["evidence"]:
            candidate["evidence"].append(item)
    if candidate["entity_type"] == "unknown" and entity_type:
        candidate["entity_type"] = entity_type


def _graph_nodes_from_context(dossier_graph_context: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(dossier_graph_context, dict):
        return []
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    for collection_name in ("graph_nodes", "matched_nodes"):
        items = dossier_graph_context.get(collection_name)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            key = (
                _normalize_key(item.get("id") or item.get("graph_node_id") or item.get("display_label") or item.get("label")),
                _extract_node_conversation_id(item),
                _normalize_key(item.get("raw_label") or item.get("label") or item.get("display_label")),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged


def _graph_nodes_from_current_conversation_graph(
    current_conversation_graph: dict[str, Any] | None,
    *,
    current_conversation_id: int,
) -> list[dict[str, Any]]:
    if not isinstance(current_conversation_graph, dict):
        return []
    nodes = current_conversation_graph.get("nodes")
    if not isinstance(nodes, list):
        return []

    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    for item in nodes:
        if not isinstance(item, dict):
            continue
        normalized = dict(item)
        if not normalized.get("conversation_id") and current_conversation_id > 0:
            normalized["conversation_id"] = current_conversation_id
        key = (
            _normalize_key(
                normalized.get("id")
                or normalized.get("graph_node_id")
                or normalized.get("display_label")
                or normalized.get("label")
            ),
            _extract_node_conversation_id(normalized),
            _normalize_key(
                normalized.get("raw_label")
                or normalized.get("label")
                or normalized.get("display_label")
            ),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(normalized)
    return merged


def _candidate_pool_for_scope(
    *,
    scope_name: str,
    subject_reference: dict[str, Any],
    dossier_context: dict[str, Any] | None,
    dossier_graph_context: dict[str, Any] | None,
    current_conversation_graph: dict[str, Any] | None,
    recent_messages: list[dict[str, Any]] | None,
    current_conversation_id: int,
) -> list[dict[str, Any]]:
    mention_tokens = _mention_tokens(subject_reference)
    target_type = _normalize_reference_type(
        subject_reference.get("reference_type") or subject_reference.get("target_type")
    )
    candidates: dict[str, dict[str, Any]] = {}
    recent_text = " ".join(
        _normalize_key(item.get("content"))
        for item in recent_messages or []
        if isinstance(item, dict) and _normalize_key(item.get("role")) == "user"
    )

    dossier_mentions: list[dict[str, Any]] = []
    if isinstance(dossier_context, dict):
        dossier_mentions.extend(
            item
            for item in dossier_context.get("participant_mentions", [])
            if isinstance(item, dict)
        )
    if isinstance(dossier_graph_context, dict):
        dossier_mentions.extend(
            item
            for item in dossier_graph_context.get("participant_mentions", [])
            if isinstance(item, dict)
        )

    for mention_item in dossier_mentions:
        participant_label = _normalize_text(mention_item.get("participant_label"))
        if not participant_label:
            continue
        if scope_name not in {
            "current_conversation_graph",
            "current_conversation_messages",
            "dossier_display_graph",
            "dossier_cross_conversation_candidates",
        }:
            continue
        if scope_name in {"current_conversation_graph", "dossier_cross_conversation_candidates"}:
            if not _scope_matches_conversation(
                scope_name,
                conversation_id=mention_item.get("conversation_id"),
                current_conversation_id=current_conversation_id,
            ):
                continue
        raw_label = _normalize_key(
            mention_item.get("raw_label") or mention_item.get("graph_node_label")
        )
        participant_label_key = _normalize_key(participant_label)
        entity_type = _normalize_text(
            mention_item.get("participant_entity_type") or mention_item.get("graph_node_type")
        )
        if scope_name == "current_conversation_messages":
            if not recent_text:
                continue
            if not any(
                token in recent_text
                for token in [*mention_tokens, raw_label, participant_label_key]
                if token
            ):
                continue
        reasons: list[str] = []
        score = 0
        if mention_tokens and any(token in raw_label for token in mention_tokens):
            score += 6
            reasons.append("mention_mapping")
        elif target_type != "unknown" and _target_type_matches(entity_type, target_type):
            score += 2
            reasons.append("mention_type_match")
        if score <= 0:
            continue
        _register_candidate(
            candidates,
            participant_label,
            entity_type=entity_type,
            score=score,
            reasons=reasons,
            scope_name=scope_name,
            evidence_items=[
                {
                    "source": "participant_mention",
                    "conversation_id": _coerce_int(mention_item.get("conversation_id")),
                    "graph_node_id": _normalize_text(mention_item.get("graph_node_id")) or None,
                    "graph_node_label": _normalize_text(mention_item.get("graph_node_label")) or None,
                    "raw_label": _normalize_text(mention_item.get("raw_label")) or None,
                }
            ],
        )

    confirmed_participants: list[dict[str, Any]] = []
    if isinstance(dossier_context, dict):
        confirmed_participants.extend(
            item
            for item in dossier_context.get("confirmed_participants", [])
            if isinstance(item, dict)
        )
    if isinstance(dossier_graph_context, dict):
        confirmed_participants.extend(
            item
            for item in dossier_graph_context.get("participants", [])
            if isinstance(item, dict)
        )

    for participant in confirmed_participants:
        if scope_name not in {
            "current_conversation_messages",
            "dossier_confirmed_participants",
            "dossier_display_graph",
        }:
            continue
        if scope_name == "current_conversation_messages" and not recent_text:
            continue
        label = _normalize_text(participant.get("label") or participant.get("display_label"))
        if not label:
            continue
        entity_type = _normalize_text(participant.get("entity_type"))
        normalized_label = _normalize_key(label)
        role = _normalize_key(participant.get("role"))
        description = _normalize_key(participant.get("description"))
        score = 0
        reasons: list[str] = []
        if target_type != "unknown" and _target_type_matches(entity_type, target_type):
            score += 2
            reasons.append("participant_type_match")
        if mention_tokens and any(
            token in normalized_label or token in role or token in description
            for token in mention_tokens
        ):
            score += 1
            reasons.append("participant_text_match")
        if scope_name == "current_conversation_messages" and recent_text:
            if normalized_label and normalized_label in recent_text:
                score += 2
                reasons.append("message_context_match")
            elif mention_tokens and any(token in recent_text for token in mention_tokens):
                score += 1
                reasons.append("message_mention_match")
        if score <= 0:
            continue
        _register_candidate(
            candidates,
            label,
            entity_type=entity_type,
            score=score,
            reasons=reasons,
            scope_name=scope_name,
            evidence_items=[
                {
                    "source": "confirmed_participant",
                    "participant_label": label,
                    "role": _normalize_text(participant.get("role")) or None,
                }
            ],
        )

    graph_nodes: list[tuple[dict[str, Any], str]] = []
    if scope_name == "current_conversation_graph":
        graph_nodes.extend(
            (
                node,
                "current_conversation_graph_node",
            )
            for node in _graph_nodes_from_current_conversation_graph(
                current_conversation_graph,
                current_conversation_id=current_conversation_id,
            )
        )
    elif scope_name in {
        "current_conversation_messages",
        "dossier_display_graph",
        "dossier_cross_conversation_candidates",
    }:
        graph_nodes.extend(
            (node, "graph_node")
            for node in _graph_nodes_from_context(dossier_graph_context)
        )

    for node, evidence_source in graph_nodes:
        if not isinstance(node, dict):
            continue
        node_conversation_id = _extract_node_conversation_id(node)
        if scope_name == "current_conversation_graph":
            if not _scope_matches_conversation(
                scope_name,
                conversation_id=node_conversation_id,
                current_conversation_id=current_conversation_id,
            ):
                continue
        elif scope_name == "dossier_cross_conversation_candidates":
            if not _scope_matches_conversation(
                scope_name,
                conversation_id=node_conversation_id,
                current_conversation_id=current_conversation_id,
            ):
                continue
        elif scope_name not in {"current_conversation_messages", "dossier_display_graph"}:
            continue

        label = _node_label(node)
        if not label:
            continue
        entity_type = _node_entity_type(node)
        if not _node_matches_reference_type(node, target_type):
            continue
        normalized_label = _normalize_key(label)
        raw_label = _normalize_key(node.get("raw_label") or node.get("label"))
        score = 0
        reasons: list[str] = []
        if target_type != "unknown" and _target_type_matches(entity_type, target_type):
            score += 5 if scope_name == "current_conversation_graph" else 2
            reasons.append("graph_node_type_match")
        if mention_tokens and any(token in normalized_label or token in raw_label for token in mention_tokens):
            score += 3
            reasons.append("graph_node_text_match")
        if target_type == "organization" and _looks_like_organization_label(label):
            score += 1 if scope_name == "current_conversation_graph" else 0
            reasons.append("organization_shape_match")
        if scope_name == "current_conversation_messages":
            if not recent_text:
                continue
            if normalized_label and normalized_label in recent_text:
                score += 2
                reasons.append("message_label_match")
            elif mention_tokens and any(token in recent_text for token in mention_tokens):
                score += 1
                reasons.append("message_mention_match")
        if score <= 0:
            continue
        _register_candidate(
            candidates,
            label,
            entity_type=entity_type,
            score=score,
            reasons=reasons,
            scope_name=scope_name,
            evidence_items=[
                {
                    "source": evidence_source,
                    "conversation_id": node_conversation_id,
                    "graph_node_id": _normalize_text(node.get("id") or node.get("graph_node_id")) or None,
                    "graph_node_label": _normalize_text(node.get("display_label") or node.get("label")) or None,
                    "raw_label": _normalize_text(node.get("raw_label")) or None,
                }
            ],
        )

    return sorted(
        candidates.values(),
        key=lambda item: (-int(item.get("score") or 0), str(item.get("label") or "")),
    )


def _build_memory_resolution_trace(
    *,
    scope_order: list[str],
    selected_scope: str,
    candidate_count_by_scope: dict[str, int],
    used_evidence: list[dict[str, Any]],
    ambiguity: bool,
    escalated_to_dossier: bool,
    unresolved_reason: str | None,
    pending_clarification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    trace = {
        "scope_order": list(scope_order),
        "selected_scope": selected_scope,
        "candidate_count_by_scope": dict(candidate_count_by_scope),
        "used_evidence": list(used_evidence),
        "ambiguity": bool(ambiguity),
        "escalated_to_dossier": bool(escalated_to_dossier),
        "whether_escalated_to_dossier": bool(escalated_to_dossier),
        "unresolved_reason": _normalize_text(unresolved_reason) or None,
    }
    if isinstance(pending_clarification, dict):
        trace["pending_clarification"] = pending_clarification
    return trace


def _build_pending_clarification_payload(
    *,
    mention: str,
    reference_type: str,
    previous_question: str,
    scope_attempted: list[str],
) -> dict[str, Any]:
    return {
        "type": PENDING_CLARIFICATION_TYPE,
        "target_mention": mention,
        "reference_type": reference_type,
        "previous_question": previous_question,
        "scope_attempted": list(scope_attempted),
        "expected_response": "entity_name_or_context",
    }


def _build_resolution_success_text(mention: str, label: str, selected_scope: str) -> tuple[str, float]:
    if selected_scope == "current_conversation_graph":
        return (
            f"在当前会话里，这里的“{mention}”指 {label}。",
            0.92,
        )
    if selected_scope == "current_conversation_messages":
        return (
            f"结合当前会话上下文，这里的“{mention}”更可能指 {label}。",
            0.76,
        )
    return (
        f"当前会话里还没有直接确认“{mention}”对应的主体。从案卷其他会话看，它更可能指 {label}，但仍需要你确认是否为同一主体。",
        0.6,
    )


def _edge_relation(edge: dict[str, Any]) -> str:
    if not isinstance(edge, dict):
        return ""
    properties = edge.get("properties") if isinstance(edge.get("properties"), dict) else {}
    return _normalize_text(
        edge.get("label")
        or edge.get("relation")
        or edge.get("relation_type")
        or edge.get("type")
        or properties.get("relation")
        or properties.get("relation_type")
    )


def _edge_endpoint(edge: dict[str, Any], *keys: str) -> str:
    if not isinstance(edge, dict):
        return ""
    for key in keys:
        value = _normalize_text(edge.get(key))
        if value:
            return value
    return ""


def _graph_lookup_tables(
    current_conversation_graph: dict[str, Any] | None,
    *,
    current_conversation_id: int,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(current_conversation_graph, dict):
        return {}, {}, []
    raw_nodes = current_conversation_graph.get("nodes")
    raw_edges = current_conversation_graph.get("edges")
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        return {}, {}, []
    node_by_id: dict[str, dict[str, Any]] = {}
    node_by_label: dict[str, dict[str, Any]] = {}
    for raw_node in raw_nodes:
        if not isinstance(raw_node, dict):
            continue
        node = dict(raw_node)
        if not node.get("conversation_id") and current_conversation_id > 0:
            node["conversation_id"] = current_conversation_id
        label = _node_label(node)
        node_id = _normalize_text(node.get("id") or node.get("graph_node_id"))
        if node_id:
            node_by_id[node_id] = node
        if label:
            node_by_label[_normalize_key(label)] = node
    return node_by_id, node_by_label, [edge for edge in raw_edges if isinstance(edge, dict)]


def _edge_nodes(
    edge: dict[str, Any],
    *,
    node_by_id: dict[str, dict[str, Any]],
    node_by_label: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    source_ref = _edge_endpoint(edge, "source", "from", "source_id", "source_name")
    target_ref = _edge_endpoint(edge, "target", "to", "target_id", "target_name")
    source_node = node_by_id.get(source_ref) or node_by_label.get(_normalize_key(source_ref))
    target_node = node_by_id.get(target_ref) or node_by_label.get(_normalize_key(target_ref))
    return (
        source_node if isinstance(source_node, dict) else None,
        target_node if isinstance(target_node, dict) else None,
    )


def _question_mentions_node(question: str, node: dict[str, Any]) -> bool:
    label = _node_label(node)
    return bool(label and label in question)


def _node_fact_text(node: dict[str, Any]) -> str:
    label = _node_label(node)
    node_type = _node_entity_type(node)
    return f"{label}（{node_type}）" if node_type else label


def _message_content(message: dict[str, Any]) -> str:
    return _normalize_text(message.get("content") if isinstance(message, dict) else "")


def _question_tokens(question: str) -> list[str]:
    normalized = _normalize_text(question)
    tokens: list[str] = []
    for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]+", normalized):
        if len(token) >= 2:
            tokens.append(token)
        if len(token) >= 4 and re.fullmatch(r"[\u4e00-\u9fff]+", token):
            for size in (2, 3, 4):
                for index in range(0, len(token) - size + 1):
                    tokens.append(token[index : index + size])
    for chunk in re.split(r"[，,。！？?\s]+", normalized):
        chunk = chunk.strip()
        if len(chunk) >= 2:
            tokens.append(chunk)
    seen: set[str] = set()
    result: list[str] = []
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


def _text_overlap_score(question: str, *values: str) -> int:
    normalized_question = _normalize_text(question)
    score = 0
    for value in values:
        normalized_value = _normalize_text(value)
        if not normalized_value:
            continue
        if normalized_value in normalized_question:
            score += max(4, min(len(normalized_value), 12))
        if normalized_question and normalized_question in normalized_value:
            score += 2
    for token in _question_tokens(normalized_question):
        if any(token in _normalize_text(value) for value in values):
            score += min(len(token), 8)
    return score


def _edge_fact_record(
    edge: dict[str, Any],
    *,
    node_by_id: dict[str, dict[str, Any]],
    node_by_label: dict[str, dict[str, Any]],
    scope: str,
    conversation_id: int,
    evidence_id: str,
) -> dict[str, Any] | None:
    relation = _edge_relation(edge)
    if not relation:
        return None
    source_node, target_node = _edge_nodes(edge, node_by_id=node_by_id, node_by_label=node_by_label)
    if not isinstance(source_node, dict) or not isinstance(target_node, dict):
        return None
    source_label = _node_label(source_node)
    target_label = _node_label(target_node)
    if not source_label or not target_label:
        return None
    return {
        "id": evidence_id,
        "scope": scope,
        "conversation_id": conversation_id,
        "source_label": source_label,
        "source_type": _node_entity_type(source_node),
        "relation": relation,
        "target_label": target_label,
        "target_type": _node_entity_type(target_node),
        "text": f"{_node_fact_text(source_node)} - {relation} - {_node_fact_text(target_node)}",
        "source_node": source_node,
        "target_node": target_node,
    }


def _graph_fact_records(
    *,
    graph_payload: dict[str, Any] | None,
    scope: str,
    current_conversation_id: int,
) -> list[dict[str, Any]]:
    node_by_id, node_by_label, edges = _graph_lookup_tables(
        graph_payload,
        current_conversation_id=current_conversation_id,
    )
    facts: list[dict[str, Any]] = []
    for index, edge in enumerate(edges):
        fact = _edge_fact_record(
            edge,
            node_by_id=node_by_id,
            node_by_label=node_by_label,
            scope=scope,
            conversation_id=current_conversation_id,
            evidence_id=f"{scope}:edge:{index}",
        )
        if fact is not None:
            facts.append(fact)
    return facts


def _dossier_graph_payload_from_context(
    dossier_graph_context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(dossier_graph_context, dict):
        return None
    raw_payload = dossier_graph_context.get("graph_payload")
    if isinstance(raw_payload, dict):
        graph = raw_payload.get("graph")
        if isinstance(graph, dict):
            return graph
        if isinstance(raw_payload.get("nodes"), list) and isinstance(raw_payload.get("edges"), list):
            return raw_payload
    graph = dossier_graph_context.get("graph")
    if isinstance(graph, dict):
        return graph
    nodes = dossier_graph_context.get("graph_nodes")
    if not isinstance(nodes, list):
        nodes = dossier_graph_context.get("matched_nodes")
    edges = dossier_graph_context.get("matched_edges")
    if isinstance(nodes, list) and isinstance(edges, list):
        return {"nodes": nodes, "edges": edges}
    return None


def _message_evidence_score(question: str, content: str) -> int:
    score = _text_overlap_score(question, content)
    normalized_question = _normalize_text(question)
    normalized_content = _normalize_text(content)
    for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]+", normalized_question):
        if len(token) < 2:
            continue
        if token in normalized_content or normalized_content.find(token[:2]) >= 0:
            score += min(len(token), 8)
            continue
        for index in range(0, max(len(token) - 1, 0), 2):
            piece = token[index : index + 2]
            if len(piece) >= 2 and piece in normalized_content:
                score += 2
    return score


def _recent_message_records(recent_messages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, message in enumerate(list(recent_messages or [])[-12:]):
        if not isinstance(message, dict):
            continue
        content = _message_content(message)
        if not content:
            continue
        records.append(
            {
                "id": f"current_conversation_messages:message:{index}",
                "scope": "current_conversation_messages",
                "message_id": _coerce_int(message.get("message_id") or message.get("id")),
                "role": _normalize_text(message.get("role")) or "user",
                "content": content,
                "text": content,
            }
        )
    return records


def _rank_fact_records(
    question: str,
    facts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        if _normalize_text(fact.get("scope")) == "current_conversation_messages":
            score = _message_evidence_score(question, _normalize_text(fact.get("content") or fact.get("text")))
        else:
            score = _text_overlap_score(
                question,
                _normalize_text(fact.get("text")),
                _normalize_text(fact.get("source_label")),
                _normalize_text(fact.get("relation")),
                _normalize_text(fact.get("target_label")),
            )
        if score <= 0:
            continue
        ranked.append({**fact, "score": score})
    return sorted(ranked, key=lambda item: (-int(item.get("score") or 0), str(item.get("id") or "")))


def _fact_labels(fact: dict[str, Any]) -> set[str]:
    labels = {
        _normalize_text(fact.get("source_label")),
        _normalize_text(fact.get("target_label")),
    }
    return {label for label in labels if label}


def _expand_related_graph_facts(
    all_facts: list[dict[str, Any]],
    seed_facts: list[dict[str, Any]],
    *,
    max_depth: int = 2,
) -> list[dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {
        _normalize_text(fact.get("id")): fact
        for fact in seed_facts
        if _normalize_text(fact.get("id"))
    }
    frontier: set[str] = set()
    for fact in seed_facts:
        frontier.update(_fact_labels(fact))

    for _depth in range(max_depth):
        if not frontier:
            break
        next_frontier: set[str] = set()
        for fact in all_facts:
            fact_id = _normalize_text(fact.get("id"))
            if not fact_id or fact_id in selected:
                continue
            labels = _fact_labels(fact)
            if not (labels & frontier):
                continue
            selected[fact_id] = {**fact, "score": int(fact.get("score") or 0)}
            next_frontier.update(labels - frontier)
        frontier = next_frontier

    return sorted(
        selected.values(),
        key=lambda item: (-int(item.get("score") or 0), str(item.get("id") or "")),
    )


def _compact_fact_for_prompt(fact: dict[str, Any]) -> dict[str, Any]:
    result = {
        "id": _normalize_text(fact.get("id")),
        "scope": _normalize_text(fact.get("scope")),
        "text": _normalize_text(fact.get("text") or fact.get("content")),
    }
    if fact.get("conversation_id") is not None:
        result["conversation_id"] = _coerce_int(fact.get("conversation_id"))
    if fact.get("message_id") is not None:
        result["message_id"] = _coerce_int(fact.get("message_id"))
    return result


def _evidence_item_from_fact(fact: dict[str, Any]) -> dict[str, Any]:
    if _normalize_text(fact.get("scope")) == "current_conversation_messages":
        return {
            "source": "current_conversation_message",
            "message_id": _coerce_int(fact.get("message_id")),
            "content": _normalize_text(fact.get("content") or fact.get("text")),
        }
    return {
        "source": f"{_normalize_text(fact.get('scope'))}_relation",
        "conversation_id": _coerce_int(fact.get("conversation_id")),
        "source_label": _normalize_text(fact.get("source_label")),
        "relation": _normalize_text(fact.get("relation")),
        "target_label": _normalize_text(fact.get("target_label")),
    }


def _evidence_items_by_ids(
    facts: list[dict[str, Any]],
    used_evidence_ids: list[Any],
) -> list[dict[str, Any]]:
    ids = {_normalize_text(item) for item in used_evidence_ids if _normalize_text(item)}
    if not ids:
        return []
    items: list[dict[str, Any]] = []
    for fact in facts:
        if _normalize_text(fact.get("id")) in ids:
            items.append(_evidence_item_from_fact(fact))
    return items


def _extract_json_object(raw_content: str) -> dict[str, Any] | None:
    text = _normalize_text(raw_content)
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _normalize_fact_memory_model_result(value: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    decision = _normalize_key(value.get("decision"))
    if decision not in {"answer", "clarify", "no_answer"}:
        return None
    answer = _normalize_text(value.get("answer"))
    try:
        confidence = float(value.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(confidence, 1.0))
    used_evidence_ids = [
        _normalize_text(item)
        for item in value.get("used_evidence_ids", [])
        if _normalize_text(item)
    ]
    selected_scope = _normalize_text(value.get("selected_scope"))
    if selected_scope not in {
        "current_conversation_graph",
        "current_conversation_messages",
        "dossier_display_graph",
    }:
        selected_scope = ""
    return {
        "decision": decision,
        "answer": answer,
        "confidence": confidence,
        "used_evidence_ids": used_evidence_ids,
        "selected_scope": selected_scope,
        "reason": _normalize_text(value.get("reason")),
    }


def _scope_count(ranked: list[dict[str, Any]], scope: str) -> int:
    return sum(1 for item in ranked if _normalize_text(item.get("scope")) == scope)


def _build_fact_memory_evidence_pack(
    *,
    question: str,
    current_conversation_graph: dict[str, Any] | None,
    dossier_graph_context: dict[str, Any] | None,
    recent_messages: list[dict[str, Any]] | None,
    current_conversation_id: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, int]]:
    current_graph_facts = _graph_fact_records(
        graph_payload=current_conversation_graph,
        scope="current_conversation_graph",
        current_conversation_id=current_conversation_id,
    )
    message_facts = _recent_message_records(recent_messages)
    dossier_graph_payload = _dossier_graph_payload_from_context(dossier_graph_context)
    dossier_facts = _graph_fact_records(
        graph_payload=dossier_graph_payload,
        scope="dossier_display_graph",
        current_conversation_id=current_conversation_id,
    )

    current_ranked = _rank_fact_records(question, current_graph_facts)
    message_ranked = _rank_fact_records(question, message_facts)
    dossier_ranked = _rank_fact_records(question, dossier_facts)
    current_expanded = _expand_related_graph_facts(
        current_graph_facts,
        current_ranked[:8],
        max_depth=3,
    )
    dossier_expanded = _expand_related_graph_facts(
        dossier_facts,
        dossier_ranked[:8],
        max_depth=3,
    )
    all_ranked = [*current_expanded[:20], *message_ranked[:8], *dossier_expanded[:20]]

    pack = {
        "question": _normalize_text(question),
        "current_conversation_graph_facts": [
            _compact_fact_for_prompt(item) for item in current_expanded[:20]
        ],
        "recent_messages": [
            _compact_fact_for_prompt(item) for item in message_ranked[:8]
        ],
        "dossier_graph_facts": [
            _compact_fact_for_prompt(item) for item in dossier_expanded[:20]
        ],
        "responsible_subject_boundary": (
            "主体候选只能来自人、公司、单位等责任主体；事件、后果、金额、日期、证据只能作为事实答案或证据。"
        ),
    }
    counts = {
        "current_conversation_graph": len(current_expanded),
        "current_conversation_messages": len(message_ranked),
        "dossier_display_graph": len(dossier_expanded),
    }
    return pack, all_ranked, counts


def _build_fact_memory_unresolved_text(question: str) -> str:
    normalized_question = _normalize_text(question)
    return f"当前会话和案卷图谱里还不能确定“{normalized_question}”，没有足够证据回答。请补充这一事实，或指出要查看的具体主体/事件。"


def _looks_like_fact_memory_resolution_request(
    message_understanding: dict[str, Any],
) -> bool:
    subject_reference = (
        message_understanding.get("subject_reference")
        if isinstance(message_understanding, dict)
        else None
    )
    if not isinstance(subject_reference, dict):
        return False
    mention = _normalize_text(
        subject_reference.get("target_mention") or subject_reference.get("mention")
    )
    reference_type = _normalize_reference_type(
        subject_reference.get("reference_type") or subject_reference.get("target_type")
    )
    return not mention or reference_type == "unknown"


def _looks_like_pure_subject_reference_request(
    *,
    question: str,
    message_understanding: dict[str, Any],
) -> bool:
    subject_reference = (
        message_understanding.get("subject_reference")
        if isinstance(message_understanding, dict)
        else None
    )
    if not isinstance(subject_reference, dict):
        return False
    mention = _normalize_text(
        subject_reference.get("target_mention") or subject_reference.get("mention")
    )
    reference_type = _normalize_reference_type(
        subject_reference.get("reference_type") or subject_reference.get("target_type")
    )
    normalized_question = _normalize_text(question)
    if not mention or reference_type == "unknown" or not normalized_question:
        return False
    if any(
        token in normalized_question
        for token in ("任职", "就职", "工作", "拖欠", "欠薪", "工资", "金额", "数额", "多少", "叫什么", "谁实施")
    ):
        return False
    return any(
        token in normalized_question
        for token in ("这里", "这个", "这家", "该", "前面", "之前", "刚才", "说的", "指的是", "指谁", "哪家公司")
    )


def _build_fact_memory_no_evidence_response(
    *,
    question: str,
) -> tuple[str, float, dict[str, Any]]:
    pending = _build_pending_clarification_payload(
        mention="这个事实",
        reference_type="unknown",
        previous_question=_normalize_text(question),
        scope_attempted=[
            "current_conversation_graph",
            "current_conversation_messages",
            "dossier_display_graph",
        ],
    )
    return (
        _build_fact_memory_unresolved_text(question),
        0.28,
        _build_memory_resolution_trace(
            scope_order=[
                "current_conversation_graph",
                "current_conversation_messages",
                "dossier_display_graph",
            ],
            selected_scope="",
            candidate_count_by_scope={
                "current_conversation_graph": 0,
                "current_conversation_messages": 0,
                "dossier_display_graph": 0,
            },
            used_evidence=[],
            ambiguity=False,
            escalated_to_dossier=False,
            unresolved_reason="no_fact_evidence_found",
            pending_clarification=pending,
        ),
    )


async def _answer_fact_memory_question_with_agent(
    *,
    question: str,
    current_conversation_graph: dict[str, Any] | None,
    dossier_graph_context: dict[str, Any] | None,
    recent_messages: list[dict[str, Any]] | None,
    current_conversation_id: int,
) -> tuple[str, float, dict[str, Any]] | None:
    evidence_pack, ranked_facts, counts = _build_fact_memory_evidence_pack(
        question=question,
        current_conversation_graph=current_conversation_graph,
        dossier_graph_context=dossier_graph_context,
        recent_messages=recent_messages,
        current_conversation_id=current_conversation_id,
    )
    if not ranked_facts:
        return None

    messages = [
        {"role": "system", "content": _FACT_MEMORY_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(evidence_pack, ensure_ascii=False, indent=2),
        },
    ]
    try:
        llm = create_cloud_llm(
            model=FACT_MEMORY_QA_MODEL,
            temperature=0.0,
            max_tokens=900,
        )
        response = await llm.ainvoke(messages)
    except Exception as exc:
        logger.warning("Fact memory QA LLM call failed: %s", exc)
        return None

    content = response.content if hasattr(response, "content") else response
    parsed = _extract_json_object(str(content or ""))
    model_result = _normalize_fact_memory_model_result(parsed or {})
    if model_result is None:
        logger.warning("Fact memory QA LLM returned invalid JSON")
        return None
    used_evidence = _evidence_items_by_ids(
        ranked_facts,
        model_result.get("used_evidence_ids", []),
    )
    if model_result["decision"] != "answer" or not model_result["answer"]:
        pending = _build_pending_clarification_payload(
            mention="这个事实",
            reference_type="unknown",
            previous_question=_normalize_text(question),
            scope_attempted=[
                "current_conversation_graph",
                "current_conversation_messages",
                "dossier_display_graph",
            ],
        )
        return (
            model_result["answer"] or _build_fact_memory_unresolved_text(question),
            min(model_result["confidence"], 0.45),
            _build_memory_resolution_trace(
                scope_order=[
                    "current_conversation_graph",
                    "current_conversation_messages",
                    "dossier_display_graph",
                ],
                selected_scope=model_result["selected_scope"],
                candidate_count_by_scope=counts,
                used_evidence=used_evidence,
                ambiguity=model_result["decision"] == "clarify",
                escalated_to_dossier=model_result["selected_scope"].startswith("dossier_"),
                unresolved_reason=model_result["decision"],
                pending_clarification=pending,
            ),
        )

    selected_scope = model_result["selected_scope"]
    if not selected_scope and used_evidence:
        first_source = _normalize_text(used_evidence[0].get("source"))
        if first_source.startswith("current_conversation_graph"):
            selected_scope = "current_conversation_graph"
        elif first_source.startswith("current_conversation_message"):
            selected_scope = "current_conversation_messages"
        elif first_source.startswith("dossier"):
            selected_scope = "dossier_display_graph"

    return (
        model_result["answer"],
        max(model_result["confidence"], 0.7),
        _build_memory_resolution_trace(
            scope_order=[
                "current_conversation_graph",
                "current_conversation_messages",
                "dossier_display_graph",
            ],
            selected_scope=selected_scope,
            candidate_count_by_scope=counts,
            used_evidence=used_evidence,
            ambiguity=False,
            escalated_to_dossier=selected_scope.startswith("dossier_"),
            unresolved_reason=None,
        ),
    )


def _answer_fact_memory_question_from_evidence(
    *,
    question: str,
    current_conversation_graph: dict[str, Any] | None,
    dossier_graph_context: dict[str, Any] | None,
    recent_messages: list[dict[str, Any]] | None,
    current_conversation_id: int,
) -> tuple[str, float, dict[str, Any]] | None:
    evidence_pack, ranked_facts, counts = _build_fact_memory_evidence_pack(
        question=question,
        current_conversation_graph=current_conversation_graph,
        dossier_graph_context=dossier_graph_context,
        recent_messages=recent_messages,
        current_conversation_id=current_conversation_id,
    )
    del evidence_pack
    if not ranked_facts:
        return None

    current_facts = [
        fact
        for fact in ranked_facts
        if _normalize_text(fact.get("scope")) in {
            "current_conversation_graph",
            "current_conversation_messages",
        }
    ]
    selected = current_facts[0] if current_facts else ranked_facts[0]
    selected_scope = _normalize_text(selected.get("scope"))
    used_evidence = [_evidence_item_from_fact(selected)]
    escalated_to_dossier = selected_scope.startswith("dossier_")
    prefix = (
        "当前会话证据不足，结合案卷其他会话记录，"
        if escalated_to_dossier
        else "根据当前会话记录，"
    )
    fact_text = _normalize_text(selected.get("text") or selected.get("content"))
    if not fact_text:
        return None
    return (
        f"{prefix}{fact_text}。",
        0.72 if escalated_to_dossier else 0.78,
        _build_memory_resolution_trace(
            scope_order=[
                "current_conversation_graph",
                "current_conversation_messages",
                "dossier_display_graph",
            ],
            selected_scope=selected_scope,
            candidate_count_by_scope=counts,
            used_evidence=used_evidence,
            ambiguity=False,
            escalated_to_dossier=escalated_to_dossier,
            unresolved_reason=None,
        ),
    )


def _answer_from_current_conversation_graph_facts(
    *,
    question: str,
    current_conversation_graph: dict[str, Any] | None,
    current_conversation_id: int,
) -> tuple[str, float, dict[str, Any]] | None:
    return _answer_fact_memory_question_from_evidence(
        question=question,
        current_conversation_graph=current_conversation_graph,
        dossier_graph_context=None,
        recent_messages=None,
        current_conversation_id=current_conversation_id,
    )


def _build_clarification_response_text(
    *,
    question: str,
    pending_clarification: dict[str, Any],
) -> tuple[str, float, dict[str, Any]]:
    mention = _normalize_text(pending_clarification.get("target_mention")) or "这个主体"
    reference_type = _normalize_reference_type(pending_clarification.get("reference_type"))
    scope_attempted = normalize_subject_reference_scope_order(
        pending_clarification.get("scope_attempted")
    )
    kind, explicit_label = _infer_clarification_entity_kind(question)
    used_evidence = [
        {
            "source": "user_clarification",
            "message": _normalize_text(question),
            "entity_kind": kind,
            "resolved_label": explicit_label,
        }
    ]

    if reference_type == "organization":
        if kind == "organization" and explicit_label:
            return (
                f"已理解，你这里说的“{mention}”指 {explicit_label}。",
                0.94,
                _build_memory_resolution_trace(
                    scope_order=scope_attempted,
                    selected_scope="clarification_response",
                    candidate_count_by_scope={},
                    used_evidence=used_evidence,
                    ambiguity=False,
                    escalated_to_dossier=False,
                    unresolved_reason=None,
                ),
            )
        if kind == "self":
            pending = _build_pending_clarification_payload(
                mention=mention,
                reference_type=reference_type,
                previous_question=_normalize_text(pending_clarification.get("previous_question")),
                scope_attempted=scope_attempted,
            )
            return (
                f"你补充的是当事人本人，但上一轮问题询问的是“{mention}”。如果你想说明{mention}，请补充公司名称。",
                0.45,
                _build_memory_resolution_trace(
                    scope_order=scope_attempted,
                    selected_scope="clarification_response",
                    candidate_count_by_scope={},
                    used_evidence=used_evidence,
                    ambiguity=False,
                    escalated_to_dossier=False,
                    unresolved_reason="clarification_type_mismatch",
                    pending_clarification=pending,
                ),
            )
        pending = _build_pending_clarification_payload(
            mention=mention,
            reference_type=reference_type,
            previous_question=_normalize_text(pending_clarification.get("previous_question")),
            scope_attempted=scope_attempted,
        )
        return (
            f"我知道你是在补充上一轮“{mention}”的指代，但现在还缺具体公司名称。请直接补充公司名称，例如 B公司。",
            0.4,
            _build_memory_resolution_trace(
                scope_order=scope_attempted,
                selected_scope="clarification_response",
                candidate_count_by_scope={},
                used_evidence=used_evidence,
                ambiguity=False,
                escalated_to_dossier=False,
                unresolved_reason="clarification_entity_name_missing",
                pending_clarification=pending,
            ),
        )

    if reference_type in {"self", "person"}:
        if kind == "self":
            return (
                f"已理解，你这里说的“{mention}”指你本人。",
                0.92,
                _build_memory_resolution_trace(
                    scope_order=scope_attempted,
                    selected_scope="clarification_response",
                    candidate_count_by_scope={},
                    used_evidence=used_evidence,
                    ambiguity=False,
                    escalated_to_dossier=False,
                    unresolved_reason=None,
                ),
            )
        pending = _build_pending_clarification_payload(
            mention=mention,
            reference_type=reference_type,
            previous_question=_normalize_text(pending_clarification.get("previous_question")),
            scope_attempted=scope_attempted,
        )
        return (
            f"上一轮问题询问的是“{mention}”对应的人物主体。如果你想补充的是当事人本人，可以直接说“指的是我本人”。",
            0.4,
            _build_memory_resolution_trace(
                scope_order=scope_attempted,
                selected_scope="clarification_response",
                candidate_count_by_scope={},
                used_evidence=used_evidence,
                ambiguity=False,
                escalated_to_dossier=False,
                unresolved_reason="clarification_entity_name_missing",
                pending_clarification=pending,
            ),
        )

    pending = _build_pending_clarification_payload(
        mention=mention,
        reference_type=reference_type,
        previous_question=_normalize_text(pending_clarification.get("previous_question")),
        scope_attempted=scope_attempted,
    )
    return (
        f"我知道你是在补充上一轮“{mention}”的指代，但现在还需要更具体的主体名称或上下文。",
        0.36,
        _build_memory_resolution_trace(
            scope_order=scope_attempted,
            selected_scope="clarification_response",
            candidate_count_by_scope={},
            used_evidence=used_evidence,
            ambiguity=False,
            escalated_to_dossier=False,
            unresolved_reason="clarification_entity_name_missing",
            pending_clarification=pending,
        ),
    )


def build_subject_reference_response_text(
    *,
    question: str,
    message_understanding: dict[str, Any],
    dossier_context: dict[str, Any] | None,
    dossier_graph_context: dict[str, Any] | None,
    current_conversation_graph: dict[str, Any] | None = None,
    recent_messages: list[dict[str, Any]] | None,
    current_conversation_id: int,
) -> tuple[str, float, dict[str, Any]]:
    pending_clarification = extract_pending_subject_reference_clarification(recent_messages)
    if looks_like_subject_reference_clarification_response(
        message=question,
        pending_clarification=pending_clarification,
    ):
        return _build_clarification_response_text(
            question=question,
            pending_clarification=pending_clarification,
        )

    subject_reference = (
        message_understanding.get("subject_reference")
        if isinstance(message_understanding, dict)
        else None
    )
    subject_reference = subject_reference if isinstance(subject_reference, dict) else {}
    mention = (
        _normalize_text(subject_reference.get("target_mention"))
        or _normalize_text(subject_reference.get("mention"))
        or "这个主体"
    )
    reference_type = _normalize_reference_type(
        subject_reference.get("reference_type") or subject_reference.get("target_type")
    )
    scope_order = normalize_subject_reference_scope_order(subject_reference.get("scope_order"))
    candidate_count_by_scope: dict[str, int] = {}
    graph_fact_answer = _answer_from_current_conversation_graph_facts(
        question=question,
        current_conversation_graph=(
            current_conversation_graph if isinstance(current_conversation_graph, dict) else None
        ),
        current_conversation_id=current_conversation_id,
    )
    if graph_fact_answer is not None:
        return graph_fact_answer

    for index, scope_name in enumerate(scope_order):
        candidates = _candidate_pool_for_scope(
            scope_name=scope_name,
            subject_reference=subject_reference,
            dossier_context=dossier_context if isinstance(dossier_context, dict) else None,
            dossier_graph_context=(
                dossier_graph_context if isinstance(dossier_graph_context, dict) else None
            ),
            current_conversation_graph=(
                current_conversation_graph if isinstance(current_conversation_graph, dict) else None
            ),
            recent_messages=recent_messages,
            current_conversation_id=current_conversation_id,
        )
        candidate_count_by_scope[scope_name] = len(candidates)
        if not candidates:
            continue

        top_candidate = candidates[0]
        top_score = int(top_candidate.get("score") or 0)
        second_score = int(candidates[1].get("score") or 0) if len(candidates) > 1 else -1
        ambiguity = len(candidates) > 1 and top_score < second_score + 2
        escalated_to_dossier = scope_name.startswith("dossier_")
        used_evidence = list(top_candidate.get("evidence") or [])
        if ambiguity and bool(subject_reference.get("needs_clarification_if_ambiguous", True)):
            labels = "、".join(candidate["label"] for candidate in candidates[:3])
            pending = _build_pending_clarification_payload(
                mention=mention,
                reference_type=reference_type,
                previous_question=_normalize_text(question),
                scope_attempted=scope_order[: index + 1],
            )
            return (
                f"我还不能稳定确定“{mention}”具体指向哪个主体，当前候选有：{labels}。请再补一句上下文，或直接说出具体主体名称。",
                0.42,
                _build_memory_resolution_trace(
                    scope_order=scope_order,
                    selected_scope=scope_name,
                    candidate_count_by_scope=candidate_count_by_scope,
                    used_evidence=used_evidence,
                    ambiguity=True,
                    escalated_to_dossier=escalated_to_dossier,
                    unresolved_reason="ambiguous_reference",
                    pending_clarification=pending,
                ),
            )

        response_text, confidence = _build_resolution_success_text(
            mention=mention,
            label=top_candidate["label"],
            selected_scope=scope_name,
        )
        return (
            response_text,
            confidence if top_score >= 5 else min(confidence, 0.82),
            _build_memory_resolution_trace(
                scope_order=scope_order,
                selected_scope=scope_name,
                candidate_count_by_scope=candidate_count_by_scope,
                used_evidence=used_evidence,
                ambiguity=False,
                escalated_to_dossier=escalated_to_dossier,
                unresolved_reason=None,
            ),
        )

    pending = _build_pending_clarification_payload(
        mention=mention,
        reference_type=reference_type,
        previous_question=_normalize_text(question),
        scope_attempted=scope_order,
    )
    return (
        f"当前还不能稳定确定“{mention}”具体指向哪个主体。请补充一句上下文，或直接给出主体名称。",
        0.28,
        _build_memory_resolution_trace(
            scope_order=scope_order,
            selected_scope="",
            candidate_count_by_scope=candidate_count_by_scope,
            used_evidence=[],
            ambiguity=False,
            escalated_to_dossier=False,
            unresolved_reason="no_matching_subject_found",
            pending_clarification=pending,
        ),
    )


async def build_subject_reference_response_text_async(
    *,
    question: str,
    message_understanding: dict[str, Any],
    dossier_context: dict[str, Any] | None,
    dossier_graph_context: dict[str, Any] | None,
    current_conversation_graph: dict[str, Any] | None = None,
    recent_messages: list[dict[str, Any]] | None,
    current_conversation_id: int,
) -> tuple[str, float, dict[str, Any]]:
    pending_clarification = extract_pending_subject_reference_clarification(recent_messages)
    if looks_like_subject_reference_clarification_response(
        message=question,
        pending_clarification=pending_clarification,
    ):
        return _build_clarification_response_text(
            question=question,
            pending_clarification=pending_clarification,
        )

    if _looks_like_pure_subject_reference_request(
        question=question,
        message_understanding=message_understanding,
    ):
        return build_subject_reference_response_text(
            question=question,
            message_understanding=message_understanding,
            dossier_context=dossier_context,
            dossier_graph_context=dossier_graph_context,
            current_conversation_graph=current_conversation_graph,
            recent_messages=recent_messages,
            current_conversation_id=current_conversation_id,
        )

    fact_memory_answer = await _answer_fact_memory_question_with_agent(
        question=question,
        current_conversation_graph=(
            current_conversation_graph if isinstance(current_conversation_graph, dict) else None
        ),
        dossier_graph_context=(
            dossier_graph_context if isinstance(dossier_graph_context, dict) else None
        ),
        recent_messages=recent_messages,
        current_conversation_id=current_conversation_id,
    )
    if fact_memory_answer is not None:
        return fact_memory_answer

    fallback_answer = _answer_fact_memory_question_from_evidence(
        question=question,
        current_conversation_graph=(
            current_conversation_graph if isinstance(current_conversation_graph, dict) else None
        ),
        dossier_graph_context=(
            dossier_graph_context if isinstance(dossier_graph_context, dict) else None
        ),
        recent_messages=recent_messages,
        current_conversation_id=current_conversation_id,
    )
    if fallback_answer is not None:
        return fallback_answer

    if _looks_like_fact_memory_resolution_request(message_understanding):
        return _build_fact_memory_no_evidence_response(question=question)

    return build_subject_reference_response_text(
        question=question,
        message_understanding=message_understanding,
        dossier_context=dossier_context,
        dossier_graph_context=dossier_graph_context,
        current_conversation_graph=current_conversation_graph,
        recent_messages=recent_messages,
        current_conversation_id=current_conversation_id,
    )


async def answer_subject_reference_message_async(
    *,
    question: str,
    message_understanding: dict[str, Any],
    dossier_context: dict[str, Any] | None,
    dossier_graph_context: dict[str, Any] | None,
    current_conversation_graph: dict[str, Any] | None = None,
    case_summary: str | None,
    recent_messages: list[dict[str, Any]] | None,
    current_conversation_id: int,
) -> dict[str, Any]:
    response_text, confidence_score, resolution_trace = await build_subject_reference_response_text_async(
        question=question,
        message_understanding=message_understanding,
        dossier_context=dossier_context,
        dossier_graph_context=dossier_graph_context,
        current_conversation_graph=current_conversation_graph,
        recent_messages=recent_messages,
        current_conversation_id=current_conversation_id,
    )
    pending_clarification = (
        resolution_trace.get("pending_clarification")
        if isinstance(resolution_trace.get("pending_clarification"), dict)
        else None
    )
    return {
        "response": response_text,
        "sources": [],
        "route_intent": "DOSSIER_MEMORY",
        "case_summary": case_summary or "",
        "is_final": True,
        "confidence_score": confidence_score,
        "missing_info": [],
        "resolution_trace": resolution_trace,
        "pending_clarification": pending_clarification,
    }
