from __future__ import annotations

import re
from typing import Any

from app.services.conversation_mode import (
    normalize_conversation_mode,
    normalize_persistence_consent,
)

_SOURCE = "dossier_entity_onboarding_agent"
_PROVIDER = "rules"
_MODEL = "dossier-entity-onboarding-rules-v1"

_SELF_ANCHORS = ("我", "本人", "当事人", "劳动者", "申请人", "我们")
_THIRD_PARTY_MARKERS = (
    "我的委托人",
    "委托人",
    "客户",
    "其称",
    "其表示",
    "当事人称",
    "当事人表示",
)
_EXPLICIT_SELF_NAME_PATTERNS = (
    re.compile(r"(?:我叫|本人叫|我名叫)([\u4e00-\u9fff]{2,6})"),
    re.compile(r"([\u4e00-\u9fff]{2,6})(?:是这起|是本案|系本案|作为).{0,12}(?:劳动者|申请人|当事人)"),
)
_THIRD_PARTY_NAME_PATTERNS = (
    re.compile(r"(?:我的委托人|委托人|客户|劳动者|申请人)([\u4e00-\u9fff]{2,4})(?:称|说|表示|反映|认为)"),
    re.compile(r"(?:我的委托人|委托人|客户|劳动者|申请人)([\u4e00-\u9fff]{2,4})"),
    re.compile(r"([\u4e00-\u9fff]{2,6})(?:称|说|表示|反映)[，,]"),
)
_ORG_PATTERNS = (
    re.compile(r"([\u4e00-\u9fffA-Za-z0-9]{1,20}(?:公司|单位|集团|厂|学校|医院|银行|事务所|社保局|仲裁委|法院|人社局))"),
)
_PERSON_ROLE_PATTERNS = (
    re.compile(r"([\u4e00-\u9fff]{1,2}经理)"),
    re.compile(r"([\u4e00-\u9fff]{1,2}主管)"),
    re.compile(r"([\u4e00-\u9fff]{1,2}主任)"),
    re.compile(r"(?:^|[，,。；;\s的])((?:HR|hr|人事|法务|老板|负责人))(?:[，,。；;\s]|$)"),
)
_AUTHORITY_HINTS = ("社保局", "劳动仲裁委", "仲裁委", "法院", "人社局", "劳动监察")
_ROLE_LABELS = {"HR", "hr", "人事", "主管", "法务", "经理", "老板", "负责人"}
_FACT_LABEL_HINTS = {
    "合同",
    "劳动合同",
    "社保",
    "工资",
    "通知",
    "确认书",
    "证据",
    "时间",
    "日期",
    "赔偿",
}
_LEGAL_INTENTS = {"legal_question", "advice_question"}
_SUPPRESSED_INTENTS = {"subject_reference", "clarification_response", "document_request"}


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


def _sorted_unique(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = _normalize_text(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _base_decision(
    *,
    conversation_id: int | None,
    dossier_id: int | None,
    trace_steps: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "source": _SOURCE,
        "provider": _PROVIDER,
        "model": _MODEL,
        "fallback_reason": None,
        "conversation_id": conversation_id,
        "dossier_id": dossier_id,
        "should_run_onboarding": True,
        "needs_current_party_confirmation": False,
        "current_party_resolution": {
            "status": "unknown",
            "resolved_name": None,
            "source_mention": None,
            "reason": "",
            "confidence": "low",
        },
        "third_party_retelling": {
            "is_third_party": False,
            "represented_party_name": None,
            "role": None,
            "reason": "",
        },
        "responsible_entities": [],
        "identity_candidates": [],
        "affiliation_candidates": [],
        "party_naming_decision": {
            "should_create_party_naming": False,
            "reason": "not_evaluated",
        },
        "trace": {
            "steps": trace_steps or ["build_onboarding_context"],
            "warnings": [],
        },
    }


def _not_applicable(
    *,
    conversation_id: int | None,
    dossier_id: int | None,
    reason: str,
) -> dict[str, Any]:
    decision = _base_decision(
        conversation_id=conversation_id,
        dossier_id=dossier_id,
        trace_steps=["build_onboarding_context", f"skip:{reason}"],
    )
    decision["should_run_onboarding"] = False
    decision["current_party_resolution"] = {
        "status": "not_applicable",
        "resolved_name": None,
        "source_mention": None,
        "reason": reason,
        "confidence": "high",
    }
    decision["party_naming_decision"] = {
        "should_create_party_naming": False,
        "reason": reason,
    }
    return decision


def _graph_nodes(raw_graph_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    graph = raw_graph_payload.get("graph") if isinstance(raw_graph_payload, dict) else {}
    nodes = graph.get("nodes") if isinstance(graph, dict) else []
    return [node for node in nodes if isinstance(node, dict)] if isinstance(nodes, list) else []


def _node_label(node: dict[str, Any]) -> str:
    properties = node.get("properties") if isinstance(node.get("properties"), dict) else {}
    for key in ("raw_label", "label", "graph_node_label", "display_label", "name", "title"):
        normalized = _normalize_text(node.get(key))
        if normalized:
            return normalized
    for key in ("raw_label", "name", "label", "title"):
        normalized = _normalize_text(properties.get(key))
        if normalized:
            return normalized
    return ""


def _node_type(node: dict[str, Any]) -> str:
    properties = node.get("properties") if isinstance(node.get("properties"), dict) else {}
    for key in ("type", "entity_type", "graph_node_type", "node_type", "category"):
        normalized = _normalize_text(node.get(key))
        if normalized:
            return normalized
    for key in ("node_type", "entity_type", "category"):
        normalized = _normalize_text(properties.get(key))
        if normalized:
            return normalized
    return "unknown"


def _node_conversation_id(node: dict[str, Any]) -> int:
    direct = _positive_int(node.get("conversation_id"))
    if direct > 0:
        return direct
    properties = node.get("properties") if isinstance(node.get("properties"), dict) else {}
    return _positive_int(properties.get("conversation_id"))


def _raw_node_id(node: dict[str, Any]) -> str:
    return _normalize_text(node.get("id") or node.get("graph_node_id"))


def _is_self_anchor(label: str) -> bool:
    return _normalize_text(label) in _SELF_ANCHORS


def _looks_like_fact(label: str, node_type: str) -> bool:
    normalized_type = _normalize_key(node_type)
    if normalized_type in {"event", "fact", "document", "amount", "time", "date", "issue", "action", "benefit"}:
        return True
    return any(hint in label for hint in _FACT_LABEL_HINTS)


def _entity_type_for_label(label: str, node_type: str) -> str:
    normalized_type = _normalize_key(node_type)
    if _is_self_anchor(label):
        return "party"
    if any(hint in label for hint in _AUTHORITY_HINTS):
        return "authority"
    if "公司" in label or "单位" in label or "集团" in label:
        return "organization"
    if label in _ROLE_LABELS:
        return "role"
    if any(hint in label for hint in ("经理", "主管", "人事", "HR", "hr", "法务", "老板", "负责人")):
        return "person" if len(label) <= 4 and label not in _ROLE_LABELS else "role"
    if normalized_type in {"organization", "company", "employer", "authority", "person", "role", "party"}:
        return normalized_type
    if len(label) in {2, 3, 4} and all("\u4e00" <= char <= "\u9fff" for char in label):
        return "person"
    return "unknown"


def _extract_explicit_self_name(message_text: str) -> str | None:
    for pattern in _EXPLICIT_SELF_NAME_PATTERNS:
        match = pattern.search(message_text)
        if match:
            return _normalize_text(match.group(1))
    return None


def _extract_third_party_name(message_text: str) -> str | None:
    for pattern in _THIRD_PARTY_NAME_PATTERNS:
        match = pattern.search(message_text)
        if match:
            candidate = _normalize_text(match.group(1))
            if candidate and candidate not in {"当事人", "劳动者", "申请人"}:
                return candidate
    return None


def _extract_message_entities(message_text: str) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    for pattern in _ORG_PATTERNS:
        for match in pattern.finditer(message_text):
            label = _normalize_text(match.group(1))
            if label:
                entities.append(
                    {
                        "label": label,
                        "entity_type": "authority" if any(hint in label for hint in _AUTHORITY_HINTS) else "organization",
                        "source": "message_text",
                    }
                )
    for pattern in _PERSON_ROLE_PATTERNS:
        for match in pattern.finditer(message_text):
            label = _normalize_text(match.group(1))
            if label:
                entities.append(
                    {
                        "label": label,
                        "entity_type": _entity_type_for_label(label, "person"),
                        "source": "message_text",
                    }
                )
    return entities


def _extract_graph_entities(
    raw_graph_payload: dict[str, Any] | None,
    *,
    conversation_id: int | None,
) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    for node in _graph_nodes(raw_graph_payload):
        node_conversation_id = _node_conversation_id(node)
        if conversation_id and node_conversation_id > 0 and node_conversation_id != conversation_id:
            continue
        label = _node_label(node)
        node_type = _node_type(node)
        if not label or _is_self_anchor(label) or _looks_like_fact(label, node_type):
            continue
        entity_type = _entity_type_for_label(label, node_type)
        if entity_type not in {"organization", "person", "role", "authority", "party"}:
            continue
        entities.append(
            {
                "label": label,
                "entity_type": entity_type,
                "source": "conversation_graph",
                "raw_node_id": _raw_node_id(node),
                "source_conversation_id": node_conversation_id or conversation_id,
            }
        )
    return entities


def _responsible_entities(
    *,
    message_text: str,
    raw_graph_payload: dict[str, Any] | None,
    conversation_id: int | None,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for entity in _extract_graph_entities(raw_graph_payload, conversation_id=conversation_id) + _extract_message_entities(message_text):
        label = _normalize_text(entity.get("label"))
        if not label:
            continue
        key = _normalize_key(label)
        current = merged.setdefault(
            key,
            {
                "label": label,
                "entity_type": entity.get("entity_type") or "unknown",
                "source": entity.get("source") or "rules",
                "should_enter_dossier_graph": True,
                "needs_naming": False,
                "needs_identity_confirmation": False,
                "reason": "责任主体应自动进入案卷图谱，不阻塞入卷流程。",
                "raw_node_ids": [],
                "source_conversation_ids": [],
            },
        )
        if entity.get("source") == "conversation_graph":
            current["source"] = "conversation_graph"
        if current.get("entity_type") == "unknown" and entity.get("entity_type"):
            current["entity_type"] = entity.get("entity_type")
        raw_node_id = _normalize_text(entity.get("raw_node_id"))
        if raw_node_id:
            current["raw_node_ids"].append(raw_node_id)
        source_conversation_id = _positive_int(entity.get("source_conversation_id"))
        if source_conversation_id > 0:
            current["source_conversation_ids"].append(source_conversation_id)

    results: list[dict[str, Any]] = []
    for entity in merged.values():
        entity["raw_node_ids"] = _sorted_unique(entity.get("raw_node_ids") or [])
        entity["source_conversation_ids"] = [
            int(item)
            for item in _sorted_unique(entity.get("source_conversation_ids") or [])
            if str(item).isdigit()
        ]
        results.append(entity)
    results.sort(key=lambda item: (str(item.get("entity_type")), str(item.get("label"))))
    return results


def _self_anchor_labels(
    *,
    message_text: str,
    raw_graph_payload: dict[str, Any] | None,
    conversation_id: int | None,
) -> list[str]:
    labels = [anchor for anchor in _SELF_ANCHORS if anchor in message_text]
    for node in _graph_nodes(raw_graph_payload):
        node_conversation_id = _node_conversation_id(node)
        if conversation_id and node_conversation_id > 0 and node_conversation_id != conversation_id:
            continue
        label = _node_label(node)
        if _is_self_anchor(label):
            labels.append(label)
    return _sorted_unique(labels)


def _affiliation_candidates(
    *,
    message_text: str,
    responsible_entities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    organizations = [
        entity for entity in responsible_entities if entity.get("entity_type") in {"organization", "authority"}
    ]
    people_or_roles = [
        entity for entity in responsible_entities if entity.get("entity_type") in {"person", "role"}
    ]
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for person in people_or_roles:
        source_label = _normalize_text(person.get("label"))
        if not source_label or not any(hint in source_label for hint in ("经理", "主管", "人事", "HR", "hr", "法务", "老板", "负责人")):
            continue
        for org in organizations:
            target_label = _normalize_text(org.get("label"))
            if not target_label or (source_label, target_label) in seen:
                continue
            if target_label in source_label or source_label in message_text and target_label in message_text:
                seen.add((source_label, target_label))
                candidates.append(
                    {
                        "source_label": source_label,
                        "target_label": target_label,
                        "relation": "人事负责人" if "人事" in message_text or "HR" in source_label.upper() else "代表",
                        "requires_user_confirmation": True,
                        "reason": "同一会话中出现责任人员与组织，作为隶属/代表关系候选。",
                    }
                )
    return candidates


def build_dossier_entity_onboarding_decision(
    *,
    message_text: str = "",
    message_understanding: dict[str, Any] | None = None,
    raw_graph_payload: dict[str, Any] | None = None,
    conversation_id: int | None = None,
    dossier_id: int | None = None,
    conversation_mode: str | None = None,
    persistence_consent: str | None = None,
    graph_updated: bool = True,
) -> dict[str, Any]:
    normalized_mode = normalize_conversation_mode(conversation_mode)
    normalized_consent = normalize_persistence_consent(persistence_consent)
    intent = _normalize_key(message_understanding.get("intent")) if isinstance(message_understanding, dict) else ""
    route_hint = _normalize_text(message_understanding.get("route_hint")).upper() if isinstance(message_understanding, dict) else ""

    if not graph_updated:
        return _not_applicable(conversation_id=conversation_id, dossier_id=dossier_id, reason="graph_not_updated")
    if not dossier_id:
        return _not_applicable(conversation_id=conversation_id, dossier_id=dossier_id, reason="dossier_not_bound")
    if normalized_consent == "temporary":
        return _not_applicable(conversation_id=conversation_id, dossier_id=dossier_id, reason="temporary_analysis")
    if intent in _LEGAL_INTENTS or route_hint == "LEGAL_QA" or normalized_mode == "LEGAL_QA":
        return _not_applicable(conversation_id=conversation_id, dossier_id=dossier_id, reason="legal_qa")
    if intent in _SUPPRESSED_INTENTS or route_hint == "DOSSIER_MEMORY":
        return _not_applicable(conversation_id=conversation_id, dossier_id=dossier_id, reason=f"{intent or route_hint.lower()}_not_onboarding")

    clean_message = _normalize_text(message_text)
    decision = _base_decision(conversation_id=conversation_id, dossier_id=dossier_id)
    decision["trace"]["steps"].append("extract_current_party")

    third_party = any(marker in clean_message for marker in _THIRD_PARTY_MARKERS)
    represented_name = _extract_third_party_name(clean_message) if third_party else None
    explicit_self_name = _extract_explicit_self_name(clean_message)
    self_labels = _self_anchor_labels(
        message_text=clean_message,
        raw_graph_payload=raw_graph_payload,
        conversation_id=conversation_id,
    )

    if third_party:
        decision["third_party_retelling"] = {
            "is_third_party": True,
            "represented_party_name": represented_name,
            "role": "委托人" if "委托人" in clean_message else "当事人",
            "reason": "消息以律师/第三方视角转述案情，当前用户本人不等同于当事人。",
        }
        decision["current_party_resolution"] = {
            "status": "not_applicable",
            "resolved_name": represented_name,
            "source_mention": "委托人" if "委托人" in clean_message else None,
            "reason": "第三方转述不生成用户本人空白命名任务。",
            "confidence": "high" if represented_name else "medium",
        }
    elif explicit_self_name:
        source_mention = self_labels[0] if self_labels else "我"
        decision["current_party_resolution"] = {
            "status": "resolved",
            "resolved_name": explicit_self_name,
            "source_mention": source_mention,
            "reason": "用户已明确自报当前会话当事人姓名。",
            "confidence": "high",
        }
        decision["identity_candidates"].append(
            {
                "source_label": source_mention,
                "target_label": explicit_self_name,
                "candidate_type": "same_entity",
                "requires_user_confirmation": False,
                "reason": "用户明示自称姓名，可作为当前会话当事人解析结果。",
            }
        )
    elif self_labels:
        decision["needs_current_party_confirmation"] = True
        decision["current_party_resolution"] = {
            "status": "needs_confirmation",
            "resolved_name": None,
            "source_mention": self_labels[0],
            "reason": "当前会话存在第一人称或当事人代称，但未给出明确姓名。",
            "confidence": "medium",
        }
    else:
        decision["current_party_resolution"] = {
            "status": "not_applicable",
            "resolved_name": None,
            "source_mention": None,
            "reason": "未发现需要命名的当前会话当事人代称。",
            "confidence": "medium",
        }

    responsible_entities = _responsible_entities(
        message_text=clean_message,
        raw_graph_payload=raw_graph_payload,
        conversation_id=conversation_id,
    )
    decision["responsible_entities"] = responsible_entities
    decision["affiliation_candidates"] = _affiliation_candidates(
        message_text=clean_message,
        responsible_entities=responsible_entities,
    )
    decision["trace"]["steps"].extend(["extract_responsible_entities", "derive_affiliation_candidates"])

    should_create_party_naming = (
        decision["current_party_resolution"]["status"] == "needs_confirmation"
        and not decision["third_party_retelling"]["is_third_party"]
    )
    if should_create_party_naming:
        reason = "self_anchor_without_explicit_name"
    elif explicit_self_name:
        reason = "explicit_current_party_name_resolved"
    elif third_party:
        reason = "third_party_retelling_not_user_self_anchor"
    else:
        reason = decision["current_party_resolution"]["reason"] or "not_applicable"
    decision["party_naming_decision"] = {
        "should_create_party_naming": should_create_party_naming,
        "reason": reason,
    }
    return decision
