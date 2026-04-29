from __future__ import annotations

import re
from typing import Any


PENDING_CLARIFICATION_TYPE = "subject_reference_resolution"

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
_ORGANIZATION_HINT_TOKENS = ("公司", "单位", "集团", "厂", "医院", "学校", "银行")
_CLARIFICATION_PREFIX_RE = re.compile(r"^(?:是|就是|指的是|说的是|我说的是|应该是|是指)\s*")


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
        return normalized_entity_type in {"organization", "company", "employer", "客体", "主体"}
    if target_type == "person":
        return normalized_entity_type in {"person", "individual", "主体", "individual_person"}
    if target_type == "self":
        return normalized_entity_type in {"person", "individual", "party", "self", "主体"}
    if target_type == "role":
        return normalized_entity_type in {"role", "person", "organization", "主体", "客体"}
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

        label = _normalize_text(
            node.get("display_label")
            or node.get("participant_label")
            or node.get("raw_label")
            or node.get("label")
        )
        if not label:
            continue
        entity_type = _normalize_text(
            node.get("type")
            or node.get("graph_node_type")
            or node.get("participant_entity_type")
        )
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
    response_text, confidence_score, resolution_trace = build_subject_reference_response_text(
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
