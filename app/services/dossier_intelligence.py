from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.services.conversation_store import (
    build_dossier_participant_lookup,
    get_dossier_for_user,
    list_dossier_conversations,
    list_dossier_participant_mentions,
    list_dossier_participants,
    serialize_dossier,
    serialize_dossier_participant,
    serialize_dossier_participant_mention,
)
from app.services.dossier_display_graph import build_dossier_display_graph
from app.services.dossier_graph import get_dossier_graph_payload

_STATUS_SUCCESS = "success"
_SOURCE_NAME = "dossier_intelligence"
_PROVIDER_NAME = "rules"
_MODEL_NAME = "dossier-intelligence-rules-v0"
_FALLBACK_REASON = "llm_not_enabled_in_skeleton"
_SUMMARY_FALLBACK_TEXT = "\u5f53\u524d\u6848\u5377\u6682\u65e0\u53ef\u7528\u6458\u8981\u3002"

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
    raw_stats = raw_graph.get("stats") if isinstance(raw_graph, dict) else {}
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


def _has_confirmed_self_naming(raw_graph_payload: dict[str, Any] | None) -> bool:
    payload = raw_graph_payload if isinstance(raw_graph_payload, dict) else {}

    if _has_explicit_self_alias(payload.get("participant_aliases")):
        return True

    confirmed_participants = payload.get("confirmed_participants")
    if not isinstance(confirmed_participants, list):
        return False

    return any(
        _participant_has_explicit_self_identity(participant)
        for participant in confirmed_participants
        if isinstance(participant, dict)
    )


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
    return raw_graph_payload, display_graph


async def build_dossier_intelligence(
    db: AsyncSession,
    *,
    current_user: User,
    dossier_id: int,
    conversation_id: int | None = None,
    include_display_graph: bool = True,
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
        "trace": {
            "steps": steps,
            "warnings": warnings,
        },
        "provider": _PROVIDER_NAME,
        "model": _MODEL_NAME,
        "fallback_reason": _FALLBACK_REASON,
    }
