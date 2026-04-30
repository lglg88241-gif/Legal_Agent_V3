from __future__ import annotations

import hashlib
import re
from typing import Any

from app.services.conversation_mode import (
    normalize_conversation_mode,
    normalize_persistence_consent,
)


SCHEMA_VERSION = "h9.conversation_entity_state.v1"
SOURCE = "conversation_entity_state_builder"
PROVIDER = "rules"
MODEL = "conversation-entity-state-rules-v1"

SELF_ANCHOR_LABELS = {
    "我",
    "本人",
    "当事人",
    "劳动者",
    "申请人",
    "我们",
    "劳动者本人",
    "我方当事人",
}
LEGAL_INTENTS = {"legal_question", "advice_question", "document_request"}
SUPPRESSED_INTENTS = {"subject_reference", "clarification_response"}
ORG_SUFFIXES = (
    "公司",
    "单位",
    "集团",
    "学校",
    "医院",
    "银行",
    "事务所",
    "社保局",
    "劳动仲裁委",
    "仲裁委",
    "法院",
    "人社局",
)
AUTHORITY_HINTS = {"社保局", "劳动仲裁委", "仲裁委", "法院", "人社局", "劳动监察"}
ROLE_LABELS = {"HR", "hr", "人事", "主管", "经理", "法务", "老板", "负责人"}
ROLE_SUFFIXES = ("经理", "主管", "主任", "负责人", "法务")
FACT_TYPES = {
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
    "issue",
    "action",
    "benefit",
    "notice",
}
FACT_LABEL_HINTS = {
    "合同",
    "劳动合同",
    "社保",
    "工资",
    "月薪",
    "通知",
    "确认书",
    "证据",
    "时间",
    "日期",
    "赔偿",
}

SELF_NAME_PATTERNS = (
    re.compile(r"(?:我叫|本人叫|本人名叫|我的名字叫|我名叫)([\u4e00-\u9fff]{2,6})"),
    re.compile(r"我是([\u4e00-\u9fff]{2,6})(?:[，,。；;]|，?是这起|，?是本案|，?作为|这起|本案)"),
    re.compile(r"([\u4e00-\u9fff]{2,6})(?:是这起|是本案|作为).{0,16}(?:劳动者|申请人|当事人)"),
)
THIRD_PARTY_PATTERNS = (
    re.compile(r"(?:我的委托人|委托人|客户)([\u4e00-\u9fff]{2,4}?)(?=称|说|表示|反映|认为|，|,|。|$)"),
    re.compile(r"(?:劳动者|申请人|当事人)([\u4e00-\u9fff]{2,4}?)(?=称|说|表示|反映|认为)"),
)
ORG_PATTERN = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9]{1,24}(?:公司|单位|集团|学校|医院|银行|事务所|社保局|劳动仲裁委|仲裁委|法院|人社局))"
)
PERSON_ROLE_PATTERNS = (
    re.compile(r"([\u4e00-\u9fff]{1,3}(?:经理|主管|主任|负责人|法务))"),
    re.compile(r"\b(HR|hr)\b"),
)


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


def _stable_id(*parts: Any) -> str:
    raw = "|".join(_normalize_text(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _graph_payload_graph(raw_graph_payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = raw_graph_payload if isinstance(raw_graph_payload, dict) else {}
    graph = payload.get("graph")
    return graph if isinstance(graph, dict) else {}


def _graph_nodes(raw_graph_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    nodes = _graph_payload_graph(raw_graph_payload).get("nodes")
    return [node for node in nodes if isinstance(node, dict)] if isinstance(nodes, list) else []


def _graph_edges(raw_graph_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    edges = _graph_payload_graph(raw_graph_payload).get("edges")
    return [edge for edge in edges if isinstance(edge, dict)] if isinstance(edges, list) else []


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


def _edge_conversation_id(edge: dict[str, Any]) -> int:
    direct = _positive_int(edge.get("conversation_id"))
    if direct > 0:
        return direct
    properties = edge.get("properties") if isinstance(edge.get("properties"), dict) else {}
    return _positive_int(properties.get("conversation_id"))


def _edge_node_ids(edge: dict[str, Any]) -> tuple[str, str]:
    return (
        _normalize_text(edge.get("source") or edge.get("from") or edge.get("source_id")),
        _normalize_text(edge.get("target") or edge.get("to") or edge.get("target_id")),
    )


def _node_incident_conversation_ids(raw_graph_payload: dict[str, Any] | None) -> dict[str, set[int]]:
    incident_ids: dict[str, set[int]] = {}
    for edge in _graph_edges(raw_graph_payload):
        conversation_id = _edge_conversation_id(edge)
        if conversation_id <= 0:
            continue
        for node_id in _edge_node_ids(edge):
            if not node_id:
                continue
            incident_ids.setdefault(node_id, set()).add(conversation_id)
    return incident_ids


def _is_self_anchor(label: str) -> bool:
    return _normalize_text(label) in SELF_ANCHOR_LABELS


def _looks_like_fact(label: str, node_type: str) -> bool:
    normalized_type = _normalize_key(node_type)
    if normalized_type in FACT_TYPES:
        return True
    return any(hint in label for hint in FACT_LABEL_HINTS)


def _looks_like_org(label: str) -> bool:
    return any(suffix in label for suffix in ORG_SUFFIXES)


def _normalize_org_label(label: str) -> str:
    normalized = _normalize_text(label)
    for separator in ("和", "与", "跟", "同", "在", "向"):
        if separator not in normalized:
            continue
        candidate = _normalize_text(normalized.rsplit(separator, 1)[-1])
        if candidate and any(candidate.endswith(suffix) for suffix in ORG_SUFFIXES):
            return candidate
    return normalized


def _looks_like_role_or_manager(label: str) -> bool:
    if label in ROLE_LABELS:
        return True
    return any(label.endswith(suffix) for suffix in ROLE_SUFFIXES)


def _normalize_person_role_label(label: str) -> str:
    normalized = _normalize_text(label)
    for prefix in ("人事", "法务"):
        if normalized.startswith(prefix) and len(normalized) > len(prefix) + 1:
            normalized = normalized[len(prefix) :]
    return normalized


def _looks_like_person_name(label: str) -> bool:
    normalized = _normalize_text(label)
    if not normalized or _looks_like_org(normalized) or _looks_like_role_or_manager(normalized):
        return False
    return len(normalized) in {2, 3, 4} and all("\u4e00" <= char <= "\u9fff" for char in normalized)


def _entity_type_for_label(label: str, node_type: str = "") -> str:
    normalized_type = _normalize_key(node_type)
    if any(hint in label for hint in AUTHORITY_HINTS):
        return "authority"
    if normalized_type in {"organization", "company", "employer", "unit"} or _looks_like_org(label):
        return "organization"
    if normalized_type in {"authority", "agency", "institution"}:
        return "authority"
    if normalized_type in {"role", "representative", "hr", "manager"}:
        return "role"
    if normalized_type in {"person", "natural_person", "person_or_role"}:
        return "person"
    if _looks_like_role_or_manager(label):
        return "person" if len(label) <= 4 and label not in ROLE_LABELS else "role"
    if _looks_like_person_name(label):
        return "person"
    return "unknown"


def _extract_self_name(message_text: str) -> str | None:
    for pattern in SELF_NAME_PATTERNS:
        match = pattern.search(message_text)
        if not match:
            continue
        candidate = _normalize_text(match.group(1))
        if candidate and candidate not in {"这起劳动", "这起", "本案", "劳动者", "申请人", "当事人"}:
            return candidate
    return None


def _extract_third_party_name(message_text: str) -> str | None:
    for pattern in THIRD_PARTY_PATTERNS:
        match = pattern.search(message_text)
        if not match:
            continue
        candidate = _normalize_text(match.group(1))
        if candidate and candidate not in {"当事人", "劳动者", "申请人"}:
            return candidate
    return None


def _is_third_party_retelling(message_text: str) -> bool:
    return any(marker in message_text for marker in ("我的委托人", "委托人", "客户", "其称", "其表示", "当事人称"))


def _message_units(message_text: str) -> list[str]:
    raw = str(message_text or "").strip()
    if not raw:
        return []
    units: list[str] = []
    for line in re.split(r"[\r\n]+", raw):
        for sentence in re.split(r"(?<=[。！？!?；;])\s*", line):
            sentence = _normalize_text(sentence)
            if sentence:
                units.append(sentence)
    return units or [_normalize_text(raw)]


def _message_entity_mentions(message_text: str, conversation_id: int | None) -> list[dict[str, Any]]:
    mentions: list[dict[str, Any]] = []
    for pattern in ORG_PATTERN.finditer(message_text):
        label = _normalize_org_label(pattern.group(1))
        if not label:
            continue
        mentions.append(
            {
                "label": label,
                "entity_type": "authority" if any(hint in label for hint in AUTHORITY_HINTS) else "organization",
                "raw_label": label,
                "raw_node_id": None,
                "conversation_id": conversation_id,
                "source": "message_text",
            }
        )
    for pattern in PERSON_ROLE_PATTERNS:
        for match in pattern.finditer(message_text):
            label = _normalize_person_role_label(match.group(1))
            if not label:
                continue
            mentions.append(
                {
                    "label": label,
                    "entity_type": _entity_type_for_label(label),
                    "raw_label": label,
                    "raw_node_id": None,
                    "conversation_id": conversation_id,
                    "source": "message_text",
                }
            )
    return mentions


def _graph_entity_mentions(
    raw_graph_payload: dict[str, Any] | None,
    conversation_id: int | None,
) -> list[dict[str, Any]]:
    mentions: list[dict[str, Any]] = []
    incident_conversation_ids = _node_incident_conversation_ids(raw_graph_payload)
    for node in _graph_nodes(raw_graph_payload):
        node_conversation_id = _node_conversation_id(node)
        node_id = _raw_node_id(node)
        if conversation_id:
            if node_conversation_id > 0:
                if node_conversation_id != conversation_id:
                    continue
            elif not node_id or conversation_id not in incident_conversation_ids.get(node_id, set()):
                continue
        label = _node_label(node)
        node_type = _node_type(node)
        if not label or _looks_like_fact(label, node_type):
            continue
        entity_type = _entity_type_for_label(label, node_type)
        if _is_self_anchor(label):
            entity_type = "person"
        if entity_type not in {"person", "organization", "role", "authority"}:
            continue
        mentions.append(
            {
                "label": label,
                "entity_type": entity_type,
                "raw_label": label,
                "raw_node_id": _raw_node_id(node),
                "conversation_id": node_conversation_id or conversation_id,
                "source": "conversation_graph",
            }
        )
    return mentions


def _empty_state(
    *,
    conversation_id: int | None,
    reason: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE,
        "provider": PROVIDER,
        "model": MODEL,
        "conversation_id": conversation_id,
        "should_build": False,
        "skip_reason": reason,
        "entities": [],
        "relations": [],
        "clarification_tasks": [],
        "party_naming_decision": {
            "should_create_party_naming": False,
            "reason": reason,
            "legacy_fallback_only": True,
        },
        "trace": {
            "steps": ["build_context", f"skip:{reason}"],
            "warnings": [],
        },
    }


def _entity_bucket(
    *,
    conversation_id: int | None,
    canonical_label: str,
    entity_type: str,
    role: str = "",
    confidence: str = "medium",
    aliases: list[str] | None = None,
    evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": f"conversation:{conversation_id or 0}:entity:{_stable_id(conversation_id, canonical_label, entity_type)}",
        "canonical_label": canonical_label,
        "entity_type": entity_type or "unknown",
        "role": role,
        "aliases": _sorted_unique(aliases or []),
        "confidence": confidence,
        "evidence": evidence or [],
        "mentions": [],
        "source_conversation_id": conversation_id,
    }


def _add_mention(entity: dict[str, Any], mention: dict[str, Any]) -> None:
    conversation_id = _positive_int(mention.get("conversation_id")) or _positive_int(entity.get("source_conversation_id"))
    raw_label = _normalize_text(mention.get("raw_label") or mention.get("label") or entity.get("canonical_label"))
    raw_node_id = _normalize_text(mention.get("raw_node_id"))
    source = _normalize_text(mention.get("source")) or "rules"
    mention_id = (
        f"conversation:{conversation_id}:mention:{_stable_id(conversation_id, raw_node_id or raw_label, source)}"
    )
    current_mentions = entity.setdefault("mentions", [])
    if any(item.get("id") == mention_id for item in current_mentions if isinstance(item, dict)):
        return
    current_mentions.append(
        {
            "id": mention_id,
            "conversation_id": conversation_id,
            "raw_label": raw_label,
            "raw_node_id": raw_node_id or None,
            "source": source,
            "entity_type": mention.get("entity_type") or entity.get("entity_type") or "unknown",
        }
    )


def _merge_aliases(entity: dict[str, Any], aliases: list[str]) -> None:
    entity["aliases"] = _sorted_unique(list(entity.get("aliases") or []) + aliases)


def build_conversation_entity_state(
    *,
    message_text: str = "",
    message_understanding: dict[str, Any] | None = None,
    raw_graph_payload: dict[str, Any] | None = None,
    conversation_id: int | None = None,
    conversation_mode: str | None = None,
    persistence_consent: str | None = None,
) -> dict[str, Any]:
    normalized_mode = normalize_conversation_mode(conversation_mode)
    normalized_consent = normalize_persistence_consent(persistence_consent)
    mu = message_understanding if isinstance(message_understanding, dict) else {}
    intent = _normalize_key(mu.get("intent"))
    route_hint = _normalize_text(mu.get("route_hint")).upper()

    if normalized_consent == "temporary":
        return _empty_state(conversation_id=conversation_id, reason="temporary_conversation")
    if intent in LEGAL_INTENTS or route_hint == "LEGAL_QA" or normalized_mode == "LEGAL_QA":
        return _empty_state(conversation_id=conversation_id, reason="legal_qa")
    if intent in SUPPRESSED_INTENTS or route_hint in {"DOSSIER_MEMORY", "SUBJECT_REFERENCE"}:
        return _empty_state(conversation_id=conversation_id, reason="non_case_entity_flow")

    clean_message = _normalize_text(message_text)
    message_units = _message_units(str(message_text or ""))
    parsed_units = [
        {
            "text": unit,
            "self_name": _extract_self_name(unit),
            "third_party": _is_third_party_retelling(unit),
            "represented_name": _extract_third_party_name(unit) if _is_third_party_retelling(unit) else None,
        }
        for unit in message_units
    ]
    self_name = next(
        (
            _normalize_text(unit.get("self_name"))
            for unit in parsed_units
            if unit.get("self_name") and not unit.get("third_party")
        ),
        None,
    ) or next(
        (_normalize_text(unit.get("self_name")) for unit in parsed_units if unit.get("self_name")),
        None,
    )
    represented_names = _sorted_unique(
        [
            unit.get("represented_name")
            for unit in parsed_units
            if unit.get("third_party") and unit.get("represented_name")
        ]
    )
    third_party = bool(represented_names)
    graph_mentions = _graph_entity_mentions(raw_graph_payload, conversation_id)
    message_mentions = _message_entity_mentions(clean_message, conversation_id)
    self_anchor_mentions = [
        mention for mention in graph_mentions if _is_self_anchor(_normalize_text(mention.get("raw_label")))
    ]
    if any(anchor in clean_message for anchor in SELF_ANCHOR_LABELS):
        for anchor in SELF_ANCHOR_LABELS:
            if anchor in clean_message:
                self_anchor_mentions.append(
                    {
                        "label": anchor,
                        "entity_type": "person",
                        "raw_label": anchor,
                        "raw_node_id": None,
                        "conversation_id": conversation_id,
                        "source": "message_text",
                    }
                )

    entities_by_label: dict[str, dict[str, Any]] = {}
    trace_steps = ["build_context", "resolve_current_party", "extract_entities"]
    clarification_tasks: list[dict[str, Any]] = []

    for represented_name in represented_names:
        if self_name and _normalize_key(represented_name) == _normalize_key(self_name):
            continue
        entity = _entity_bucket(
            conversation_id=conversation_id,
            canonical_label=represented_name,
            entity_type="person",
            role="represented_party",
            confidence="high",
            aliases=["委托人", "当事人"],
            evidence=[
                {
                    "message_text": clean_message,
                    "reason": "律师/第三方转述中明确出现被代表当事人姓名。",
                }
            ],
        )
        _add_mention(
            entity,
            {
                "raw_label": represented_name,
                "conversation_id": conversation_id,
                "source": "message_text",
                "entity_type": "person",
            },
        )
        entities_by_label[_normalize_key(represented_name)] = entity

    if self_name:
        aliases = ["我", "本人", "当事人", "劳动者"]
        entity = _entity_bucket(
            conversation_id=conversation_id,
            canonical_label=self_name,
            entity_type="person",
            role="current_party",
            confidence="high",
            aliases=aliases,
            evidence=[
                {
                    "message_text": clean_message,
                    "reason": "用户自报姓名并说明自己是当前会话案件当事人。",
                }
            ],
        )
        _add_mention(
            entity,
            {
                "raw_label": self_name,
                "conversation_id": conversation_id,
                "source": "message_text",
                "entity_type": "person",
            },
        )
        for mention in self_anchor_mentions:
            _add_mention(entity, mention)
        entities_by_label[_normalize_key(self_name)] = entity
    elif self_anchor_mentions and not represented_names:
        placeholder = "当前会话当事人"
        entity = _entity_bucket(
            conversation_id=conversation_id,
            canonical_label=placeholder,
            entity_type="person",
            role="current_party",
            confidence="low",
            aliases=[_normalize_text(item.get("raw_label")) for item in self_anchor_mentions],
            evidence=[
                {
                    "message_text": clean_message,
                    "reason": "当前会话存在第一人称或当事人代称，但尚无明确姓名；应由会话层澄清，不进入案卷抽屉 party_naming 主线。",
                }
            ],
        )
        for mention in self_anchor_mentions:
            _add_mention(entity, mention)
        entities_by_label[_normalize_key(placeholder)] = entity
        clarification_tasks.append(
            {
                "type": "conversation_current_party_name",
                "status": "pending",
                "conversation_id": conversation_id,
                "raw_labels": _sorted_unique([item.get("raw_label") for item in self_anchor_mentions]),
                "prompt": "请确认当前会话中的“我/当事人”是谁。",
            }
        )

    represented_name_keys = {_normalize_key(name) for name in represented_names}
    for mention in graph_mentions + message_mentions:
        label = _normalize_text(mention.get("label"))
        if not label or _is_self_anchor(label):
            continue
        if self_name and _normalize_key(label) == _normalize_key(self_name):
            _add_mention(entities_by_label[_normalize_key(self_name)], mention)
            continue
        if _normalize_key(label) in represented_name_keys and _normalize_key(label) in entities_by_label:
            _add_mention(entities_by_label[_normalize_key(label)], mention)
            continue
        entity_type = _normalize_text(mention.get("entity_type")) or _entity_type_for_label(label)
        if entity_type not in {"person", "organization", "role", "authority"}:
            continue
        key = _normalize_key(label)
        entity = entities_by_label.get(key)
        if entity is None:
            role = ""
            if entity_type == "organization":
                role = "employer" if "公司" in label or "单位" in label else ""
            elif entity_type in {"person", "role"} and _looks_like_role_or_manager(label):
                role = "manager_or_hr"
            entity = _entity_bucket(
                conversation_id=conversation_id,
                canonical_label=label,
                entity_type=entity_type,
                role=role,
                confidence="medium",
                evidence=[],
            )
            entities_by_label[key] = entity
        _add_mention(entity, mention)

    entities = list(entities_by_label.values())
    entity_lookup = {_normalize_key(entity.get("canonical_label")): entity for entity in entities}
    relations: list[dict[str, Any]] = []

    organizations = [
        entity for entity in entities if entity.get("entity_type") in {"organization", "authority"}
    ]
    role_people = [
        entity
        for entity in entities
        if entity.get("entity_type") in {"person", "role"}
        and _looks_like_role_or_manager(_normalize_text(entity.get("canonical_label")))
    ]
    for role_entity in role_people:
        source_label = _normalize_text(role_entity.get("canonical_label"))
        for organization in organizations:
            target_label = _normalize_text(organization.get("canonical_label"))
            if not target_label or not source_label:
                continue
            if target_label in clean_message and source_label in clean_message:
                relation_label = "人事/代表/隶属" if "人事" in clean_message or "HR" in source_label.upper() else "代表/隶属"
                relations.append(
                    {
                        "id": f"conversation:{conversation_id or 0}:relation:{_stable_id(source_label, 'affiliation', target_label)}",
                        "source": role_entity["id"],
                        "source_label": source_label,
                        "target": organization["id"],
                        "target_label": target_label,
                        "relation": relation_label,
                        "relation_type": "affiliation",
                        "confidence": "high" if f"{target_label}的" in clean_message else "medium",
                        "conversation_id": conversation_id,
                        "evidence": [
                            {
                                "message_text": clean_message,
                                "reason": f"“{target_label}的人事/负责人{source_label}”语义表明二者是任职或代表关系，不是同一主体。",
                            }
                        ],
                    }
                )

    current_party = next((entity for entity in entities if entity.get("role") == "current_party"), None)
    if current_party is not None and any(token in clean_message for token in ("通知我", "口头通知我", "不用来了")):
        for role_entity in role_people:
            relations.append(
                {
                    "id": f"conversation:{conversation_id or 0}:relation:{_stable_id(role_entity.get('canonical_label'), 'notice', current_party.get('canonical_label'))}",
                    "source": role_entity["id"],
                    "source_label": role_entity.get("canonical_label"),
                    "target": current_party["id"],
                    "target_label": current_party.get("canonical_label"),
                    "relation": "通知",
                    "relation_type": "event_participation",
                    "confidence": "medium",
                    "conversation_id": conversation_id,
                    "evidence": [{"message_text": clean_message, "reason": "消息描述通知行为。"}],
                }
            )

    for edge in _graph_edges(raw_graph_payload):
        edge_conversation_id = _edge_conversation_id(edge)
        if conversation_id and edge_conversation_id > 0 and edge_conversation_id != conversation_id:
            continue
        source_id, target_id = _edge_node_ids(edge)
        if not source_id or not target_id:
            continue
        source_entity = next(
            (
                entity
                for entity in entities
                if any(mention.get("raw_node_id") == source_id for mention in entity.get("mentions", []))
            ),
            None,
        )
        target_entity = next(
            (
                entity
                for entity in entities
                if any(mention.get("raw_node_id") == target_id for mention in entity.get("mentions", []))
            ),
            None,
        )
        if source_entity is None or target_entity is None or source_entity["id"] == target_entity["id"]:
            continue
        label = _normalize_text(edge.get("label") or edge.get("type")) or "关联"
        relation_type = "affiliation" if "隶属" in label or "人事" in label or "代表" in label else "case_relation"
        relations.append(
            {
                "id": f"conversation:{conversation_id or edge_conversation_id or 0}:relation:{_stable_id(source_id, target_id, label)}",
                "source": source_entity["id"],
                "source_label": source_entity.get("canonical_label"),
                "target": target_entity["id"],
                "target_label": target_entity.get("canonical_label"),
                "relation": label,
                "relation_type": relation_type,
                "confidence": "medium",
                "conversation_id": edge_conversation_id or conversation_id,
                "evidence": [
                    {
                        "message_text": "",
                        "raw_edge_id": _normalize_text(edge.get("id")),
                        "reason": "来自当前会话原始图谱关系。",
                    }
                ],
            }
        )

    entities.sort(key=lambda item: (str(item.get("entity_type")), str(item.get("canonical_label"))))
    relations.sort(key=lambda item: (str(item.get("relation_type")), str(item.get("source_label")), str(item.get("target_label"))))

    party_naming_reason = "conversation_entity_state_resolves_current_party"
    if clarification_tasks:
        party_naming_reason = "conversation_clarification_plan_replaces_dossier_party_naming"
    if third_party and not self_name:
        party_naming_reason = "third_party_retelling_not_user_self_anchor"

    return {
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE,
        "provider": PROVIDER,
        "model": MODEL,
        "conversation_id": conversation_id,
        "should_build": True,
        "skip_reason": None,
        "entities": entities,
        "relations": relations,
        "clarification_tasks": clarification_tasks,
        "party_naming_decision": {
            "should_create_party_naming": False,
            "reason": party_naming_reason,
            "legacy_fallback_only": True,
        },
        "trace": {
            "steps": trace_steps + ["derive_relations", "build_conversation_entity_state"],
            "warnings": [],
        },
    }


__all__ = ["build_conversation_entity_state"]
