from __future__ import annotations

from typing import Any


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
        return normalized_entity_type in {"organization", "company", "employer"}
    if target_type == "person":
        return normalized_entity_type in {"person", "individual"}
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


def _register_candidate(
    bucket: dict[str, dict[str, Any]],
    label: str,
    *,
    entity_type: str,
    score: int,
    reason: str,
    scope_name: str,
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
        },
    )
    candidate["score"] += max(0, score)
    if reason and reason not in candidate["reasons"]:
        candidate["reasons"].append(reason)
    if candidate["entity_type"] == "unknown" and entity_type:
        candidate["entity_type"] = entity_type


def _candidate_pool_for_scope(
    *,
    scope_name: str,
    subject_reference: dict[str, Any],
    dossier_context: dict[str, Any] | None,
    dossier_graph_context: dict[str, Any] | None,
    recent_messages: list[dict[str, Any]] | None,
    current_conversation_id: int,
) -> list[dict[str, Any]]:
    mention_tokens = _mention_tokens(subject_reference)
    target_type = _normalize_key(
        subject_reference.get("reference_type") or subject_reference.get("target_type")
    ) or "unknown"
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
        if mention_tokens and any(token in raw_label for token in mention_tokens):
            _register_candidate(
                candidates,
                participant_label,
                entity_type=entity_type,
                score=6,
                reason="mention_mapping",
                scope_name=scope_name,
            )
        elif target_type != "unknown" and _target_type_matches(entity_type, target_type):
            _register_candidate(
                candidates,
                participant_label,
                entity_type=entity_type,
                score=2,
                reason="mention_type_match",
                scope_name=scope_name,
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
            reason=",".join(reasons),
            scope_name=scope_name,
        )

    matched_nodes = (
        dossier_graph_context.get("matched_nodes", [])
        if isinstance(dossier_graph_context, dict)
        else []
    )
    for node in matched_nodes if isinstance(matched_nodes, list) else []:
        if not isinstance(node, dict):
            continue
        if scope_name == "current_conversation_graph":
            if not _scope_matches_conversation(
                scope_name,
                conversation_id=_extract_node_conversation_id(node),
                current_conversation_id=current_conversation_id,
            ):
                continue
        elif scope_name == "dossier_cross_conversation_candidates":
            if not _scope_matches_conversation(
                scope_name,
                conversation_id=_extract_node_conversation_id(node),
                current_conversation_id=current_conversation_id,
            ):
                continue
        elif scope_name not in {"dossier_display_graph"}:
            continue

        label = _normalize_text(
            node.get("display_label")
            or node.get("participant_label")
            or node.get("raw_label")
            or node.get("label")
        )
        if not label:
            continue
        entity_type = _normalize_text(node.get("type") or node.get("graph_node_type"))
        normalized_label = _normalize_key(label)
        raw_label = _normalize_key(node.get("raw_label") or node.get("label"))
        score = 0
        reasons: list[str] = []
        if target_type != "unknown" and _target_type_matches(entity_type, target_type):
            score += 1
            reasons.append("matched_node_type")
        if mention_tokens and any(token in normalized_label or token in raw_label for token in mention_tokens):
            score += 2
            reasons.append("matched_node_text")
        if score <= 0:
            continue
        _register_candidate(
            candidates,
            label,
            entity_type=entity_type,
            score=score,
            reason=",".join(reasons),
            scope_name=scope_name,
        )

    return sorted(
        candidates.values(),
        key=lambda item: (-int(item.get("score") or 0), str(item.get("label") or "")),
    )


def _subject_reference_response(
    *,
    mention: str,
    selected_scope: str,
    top_candidate: dict[str, Any],
    top_score: int,
    candidate_count_by_scope: dict[str, int],
    ambiguity: bool,
    whether_escalated_to_dossier: bool,
) -> tuple[str, float, dict[str, Any]]:
    trace = {
        "selected_scope": selected_scope,
        "candidate_count_by_scope": candidate_count_by_scope,
        "ambiguity": ambiguity,
        "whether_escalated_to_dossier": whether_escalated_to_dossier,
    }
    if selected_scope == "current_conversation_graph":
        return (
            f"在当前会话里，这里的“{mention}”指 {top_candidate['label']}。"
            "我优先使用了当前会话图谱里的主体线索。",
            0.92 if top_score >= 5 else 0.8,
            trace,
        )
    if selected_scope == "current_conversation_messages":
        return (
            f"结合当前会话上下文，这里的“{mention}”更可能指 {top_candidate['label']}。"
            "这个判断主要来自当前会话里已经出现的主体称呼和消息上下文。",
            0.76,
            trace,
        )
    return (
        f"当前会话里还没有直接确认“{mention}”对应的主体。"
        f"从案卷其他线索看，它更可能指 {top_candidate['label']}，但仍需要你确认是否为同一主体。",
        0.6,
        trace,
    )


def build_subject_reference_response_text(
    *,
    question: str,
    message_understanding: dict[str, Any],
    dossier_context: dict[str, Any] | None,
    dossier_graph_context: dict[str, Any] | None,
    recent_messages: list[dict[str, Any]] | None,
    current_conversation_id: int,
) -> tuple[str, float, dict[str, Any]]:
    _ = question
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
    scope_order = normalize_subject_reference_scope_order(subject_reference.get("scope_order"))
    candidate_count_by_scope: dict[str, int] = {}

    for scope_name in scope_order:
        candidates = _candidate_pool_for_scope(
            scope_name=scope_name,
            subject_reference=subject_reference,
            dossier_context=dossier_context if isinstance(dossier_context, dict) else None,
            dossier_graph_context=(
                dossier_graph_context if isinstance(dossier_graph_context, dict) else None
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
        whether_escalated_to_dossier = scope_name.startswith("dossier_")
        if ambiguity and bool(subject_reference.get("needs_clarification_if_ambiguous", True)):
            labels = "、".join(candidate["label"] for candidate in candidates[:3])
            return (
                f"我还不能直接确定“{mention}”指的是哪一个主体，当前候选有：{labels}。"
                "请再补一句上下文，或直接说出具体主体名称。",
                0.42,
                {
                    "selected_scope": scope_name,
                    "candidate_count_by_scope": candidate_count_by_scope,
                    "ambiguity": True,
                    "whether_escalated_to_dossier": whether_escalated_to_dossier,
                },
            )
        return _subject_reference_response(
            mention=mention,
            selected_scope=scope_name,
            top_candidate=top_candidate,
            top_score=top_score,
            candidate_count_by_scope=candidate_count_by_scope,
            ambiguity=False,
            whether_escalated_to_dossier=whether_escalated_to_dossier,
        )

    return (
        f"当前还不能稳定确定“{mention}”具体指向哪个主体。"
        "请补充一句上下文，或直接给出主体名称。",
        0.28,
        {
            "selected_scope": "",
            "candidate_count_by_scope": candidate_count_by_scope,
            "ambiguity": False,
            "whether_escalated_to_dossier": False,
        },
    )


async def answer_subject_reference_message_async(
    *,
    question: str,
    message_understanding: dict[str, Any],
    dossier_context: dict[str, Any] | None,
    dossier_graph_context: dict[str, Any] | None,
    case_summary: str | None,
    recent_messages: list[dict[str, Any]] | None,
    current_conversation_id: int,
) -> dict[str, Any]:
    response_text, confidence_score, resolution_trace = build_subject_reference_response_text(
        question=question,
        message_understanding=message_understanding,
        dossier_context=dossier_context,
        dossier_graph_context=dossier_graph_context,
        recent_messages=recent_messages,
        current_conversation_id=current_conversation_id,
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
    }
