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
CURRENT_PARTY_DEFAULT_ALIASES = ("我", "本人", "当事人", "劳动者", "申请人")
LEGAL_INTENTS = {"legal_question", "advice_question", "document_request"}
SUPPRESSED_INTENTS = {"subject_reference", "clarification_response"}
ORG_SUFFIXES = (
    "有限责任公司",
    "股份有限公司",
    "集团有限公司",
    "集团公司",
    "有限公司",
    "公司",
    "单位",
    "集团",
    "企业",
    "科技",
    "中心",
    "工厂",
    "厂",
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
ROLE_LABELS = {"HR", "hr", "人事", "主管", "经理", "法务", "老板", "负责人", "法人", "法定代表人", "代表", "代理人"}
ROLE_SUFFIXES = ("经理", "主管", "主任", "负责人", "法务", "法人", "法定代表人", "代表", "代理人")
FACT_TYPES = {
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
}
FACT_LABEL_HINTS = {
    "欠薪",
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
    "仲裁申请",
    "申请书",
    "仲裁申请书",
    "工资流水",
    "银行流水",
    "微信聊天",
    "微信聊天记录",
    "聊天记录",
    "聊天截图",
    "打卡记录",
    "考勤记录",
    "转账记录",
    "录音",
    "截图",
    "凭证",
    "证人证言",
    "咨询主题",
    "案由",
    "纠纷",
    "劳动纠纷",
    "争议",
    "争议类型",
    "案件类型",
}
FACT_LABEL_HINTS = FACT_LABEL_HINTS | {
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
RESPONSIBLE_ENTITY_TYPES = {
    "person",
    "natural_person",
    "person_or_role",
    "person_role",
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
    types = _node_type_values(node)
    return types[0] if types else "unknown"


def _node_type_values(node: dict[str, Any]) -> list[str]:
    properties = node.get("properties") if isinstance(node.get("properties"), dict) else {}
    values: list[str] = []
    for key in ("type", "entity_type", "graph_node_type", "node_type", "category"):
        normalized = _normalize_text(node.get(key))
        if normalized:
            values.append(normalized)
    for key in ("node_type", "entity_type", "category"):
        normalized = _normalize_text(properties.get(key))
        if normalized:
            values.append(normalized)
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = _normalize_key(value)
        if key and key not in seen:
            seen.add(key)
            result.append(value)
    return result


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


def _node_belongs_to_conversation(
    node: dict[str, Any],
    raw_graph_payload: dict[str, Any] | None,
    conversation_id: int | None,
) -> bool:
    if not conversation_id:
        return True
    node_conversation_id = _node_conversation_id(node)
    if node_conversation_id > 0:
        return node_conversation_id == conversation_id
    node_id = _raw_node_id(node)
    if not node_id:
        return False
    return conversation_id in _node_incident_conversation_ids(raw_graph_payload).get(node_id, set())


def _graph_has_scoped_content(
    raw_graph_payload: dict[str, Any] | None,
    conversation_id: int | None,
) -> bool:
    for node in _graph_nodes(raw_graph_payload):
        if _node_belongs_to_conversation(node, raw_graph_payload, conversation_id):
            return True
    for edge in _graph_edges(raw_graph_payload):
        edge_conversation_id = _edge_conversation_id(edge)
        if not conversation_id or edge_conversation_id == conversation_id:
            return True
    return False


def _is_self_anchor(label: str) -> bool:
    return _normalize_text(label) in SELF_ANCHOR_LABELS


def _is_case_graph_subject_category(value: str) -> bool:
    return _normalize_text(value) == "主体"


def _is_case_graph_non_subject_category(value: str) -> bool:
    return _normalize_text(value) in {"客体", "事件", "后果"}


def _looks_like_fact(label: str, node_type: str, node_types: list[str] | None = None) -> bool:
    normalized_types = {_normalize_key(value) for value in (node_types or [node_type]) if _normalize_key(value)}
    if normalized_types & FACT_TYPES:
        return True
    if any(_is_case_graph_non_subject_category(value) for value in (node_types or [node_type])):
        return True
    if _looks_like_duration_or_time_quantity(label):
        return True
    return any(hint in label for hint in FACT_LABEL_HINTS)


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
    if any(hint in normalized for hint in FACT_LABEL_HINTS):
        return False
    if len(normalized) in {2, 3} and all("\u4e00" <= char <= "\u9fff" for char in normalized):
        return True
    return len(normalized) <= 8 and any(char.isalpha() for char in normalized) and any("\u4e00" <= char <= "\u9fff" for char in normalized)


def _is_specific_person_label(label: str) -> bool:
    normalized = _normalize_text(label)
    return _looks_like_person_name(normalized) and normalized not in SELF_ANCHOR_LABELS


def _entity_type_for_label(label: str, node_type: str = "", node_types: list[str] | None = None) -> str:
    type_values = node_types or ([node_type] if node_type else [])
    if any(_is_case_graph_non_subject_category(value) for value in type_values):
        return "fact"
    normalized_type = _normalize_key(node_type)
    if normalized_type == "dynamiccaseentity":
        subject_type = next((value for value in type_values if _is_case_graph_subject_category(value)), "")
        if subject_type:
            normalized_type = ""
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


def _graph_node_role(label: str, entity_type: str, node: dict[str, Any]) -> str:
    properties = node.get("properties") if isinstance(node.get("properties"), dict) else {}
    for key in ("role", "participant_role", "party_role"):
        role = _normalize_text(node.get(key) or properties.get(key))
        if role:
            if role in {"current_party", "本人", "当事人", "劳动者", "申请人"}:
                return "current_party"
            if role in {"represented_party", "委托人", "客户"}:
                return "represented_party"
            return role
    party_side = _normalize_key(
        node.get("party_side")
        or node.get("participant_party_side")
        or properties.get("party_side")
        or properties.get("participant_party_side")
    )
    if party_side in {"claimant", "primary", "current_party"} and entity_type == "person":
        return "current_party"
    if entity_type == "organization" and _looks_like_org(label):
        return "employer"
    if entity_type in {"person", "role"} and _looks_like_role_or_manager(label):
        return "manager_or_hr"
    return ""


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
        node_types = _node_type_values(node)
        node_type = node_types[0] if node_types else "unknown"
        if not label:
            continue
        if _looks_like_fact(label, node_type, node_types):
            continue
        entity_type = _entity_type_for_label(label, node_type, node_types)
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
    source: str = "conversation_graph",
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
        "source": source,
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


def _build_entities_from_graph(
    *,
    graph_mentions: list[dict[str, Any]],
    raw_graph_payload: dict[str, Any] | None,
    conversation_id: int | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    entities_by_label: dict[str, dict[str, Any]] = {}
    self_anchor_mentions: list[dict[str, Any]] = []
    warnings: list[str] = []
    raw_node_lookup: dict[str, dict[str, Any]] = {}
    for node in _graph_nodes(raw_graph_payload):
        node_id = _raw_node_id(node)
        if node_id:
            raw_node_lookup[node_id] = node

    for mention in graph_mentions:
        label = _normalize_text(mention.get("label"))
        if not label:
            continue
        if _is_self_anchor(label):
            self_anchor_mentions.append(mention)
            continue
        raw_node_id = _normalize_text(mention.get("raw_node_id"))
        raw_node = raw_node_lookup.get(raw_node_id, {})
        entity_type = _normalize_text(mention.get("entity_type")) or _entity_type_for_label(label)
        if entity_type not in {"person", "organization", "role", "authority"}:
            continue
        role = _graph_node_role(label, entity_type, raw_node)
        key = _normalize_key(label)
        entity = entities_by_label.get(key)
        if entity is None:
            entity = _entity_bucket(
                conversation_id=conversation_id,
                canonical_label=label,
                entity_type=entity_type,
                role=role,
                confidence="high" if raw_node_id else "medium",
                source="conversation_graph",
                evidence=[
                    {
                        "source": "conversation_graph",
                        "raw_node_id": raw_node_id or None,
                        "raw_label": _normalize_text(mention.get("raw_label")) or label,
                        "reason": "来自当前会话图谱节点。",
                    }
                ],
            )
            entities_by_label[key] = entity
        elif role and not entity.get("role"):
            entity["role"] = role
        _add_mention(entity, mention)
        raw_label = _normalize_text(mention.get("raw_label"))
        if raw_label and raw_label != label:
            _merge_aliases(entity, [raw_label])
        if raw_label.startswith(("但", "但是")):
            warnings.append(f"graph_extractor_prefixed_org_label:{raw_label}")

    current_party_entities = [
        entity for entity in entities_by_label.values() if entity.get("role") == "current_party"
    ]
    if len(current_party_entities) == 1:
        current_party = current_party_entities[0]
        if (
            _normalize_text(current_party.get("entity_type")) == "person"
            and _is_specific_person_label(_normalize_text(current_party.get("canonical_label")))
        ):
            _merge_aliases(current_party, list(CURRENT_PARTY_DEFAULT_ALIASES))
    if len(current_party_entities) == 1 and self_anchor_mentions:
        current_party = current_party_entities[0]
        _merge_aliases(current_party, [_normalize_text(item.get("raw_label")) for item in self_anchor_mentions])
        for mention in self_anchor_mentions:
            _add_mention(current_party, mention)

    clarification_tasks: list[dict[str, Any]] = []
    if self_anchor_mentions and not current_party_entities:
        clarification_tasks.append(
            {
                "type": "conversation_current_party_name",
                "status": "pending",
                "conversation_id": conversation_id,
                "raw_labels": _sorted_unique([item.get("raw_label") for item in self_anchor_mentions]),
                "prompt": "当前会话图谱包含自称/当事人代称，但会话层尚未解析为具体主体。",
                "source": "conversation_graph",
            }
        )

    entities = list(entities_by_label.values())
    entities.sort(key=lambda item: (str(item.get("entity_type")), str(item.get("canonical_label"))))
    relations = _build_relations_from_graph(
        entities=entities,
        raw_graph_payload=raw_graph_payload,
        conversation_id=conversation_id,
    )
    return entities, relations, clarification_tasks, warnings


def _build_relations_from_graph(
    *,
    entities: list[dict[str, Any]],
    raw_graph_payload: dict[str, Any] | None,
    conversation_id: int | None,
) -> list[dict[str, Any]]:
    relations: list[dict[str, Any]] = []
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
                "confidence": "high" if edge_conversation_id > 0 else "medium",
                "conversation_id": edge_conversation_id or conversation_id,
                "origin": "conversation_graph",
                "evidence": [
                    {
                        "source": "conversation_graph",
                        "raw_edge_id": _normalize_text(edge.get("id")),
                        "reason": "来自当前会话原始图谱关系。",
                    }
                ],
            }
        )
    relations.sort(key=lambda item: (str(item.get("relation_type")), str(item.get("source_label")), str(item.get("target_label"))))
    return relations


def _build_fallback_entities_from_message(
    *,
    message_text: str,
    conversation_id: int | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
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
    message_mentions = _message_entity_mentions(clean_message, conversation_id)
    self_anchor_mentions = []
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
                        "source": "fallback_rules",
                    }
                )

    entities_by_label: dict[str, dict[str, Any]] = {}
    for represented_name in represented_names:
        if self_name and _normalize_key(represented_name) == _normalize_key(self_name):
            continue
        entity = _entity_bucket(
            conversation_id=conversation_id,
            canonical_label=represented_name,
            entity_type="person",
            role="represented_party",
            confidence="low",
            source="fallback_rules",
            aliases=["委托人", "当事人"],
            evidence=[{"source": "fallback_rules", "message_text": clean_message}],
        )
        _add_mention(
            entity,
            {
                "raw_label": represented_name,
                "conversation_id": conversation_id,
                "source": "fallback_rules",
                "entity_type": "person",
            },
        )
        entities_by_label[_normalize_key(represented_name)] = entity

    if self_name:
        entity = _entity_bucket(
            conversation_id=conversation_id,
            canonical_label=self_name,
            entity_type="person",
            role="current_party",
            confidence="low",
            source="fallback_rules",
            aliases=["我", "本人", "当事人", "劳动者"],
            evidence=[{"source": "fallback_rules", "message_text": clean_message}],
        )
        _add_mention(
            entity,
            {
                "raw_label": self_name,
                "conversation_id": conversation_id,
                "source": "fallback_rules",
                "entity_type": "person",
            },
        )
        for mention in self_anchor_mentions:
            _add_mention(entity, mention)
        entities_by_label[_normalize_key(self_name)] = entity

    represented_name_keys = {_normalize_key(name) for name in represented_names}
    for mention in message_mentions:
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
                confidence="low",
                source="fallback_rules",
                evidence=[{"source": "fallback_rules", "message_text": clean_message}],
            )
            entities_by_label[key] = entity
        mention["source"] = "fallback_rules"
        _add_mention(entity, mention)

    clarification_tasks: list[dict[str, Any]] = []
    if self_anchor_mentions and not self_name and not represented_names:
        clarification_tasks.append(
            {
                "type": "conversation_current_party_name",
                "status": "pending",
                "conversation_id": conversation_id,
                "raw_labels": _sorted_unique([item.get("raw_label") for item in self_anchor_mentions]),
                "prompt": "会话图谱不可用，fallback 只提示需要会话层澄清，不进入案卷 canonical graph。",
                "source": "fallback_rules",
            }
        )

    entities = list(entities_by_label.values())
    entities.sort(key=lambda item: (str(item.get("entity_type")), str(item.get("canonical_label"))))
    return entities, [], clarification_tasks


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

    graph_available = _graph_has_scoped_content(raw_graph_payload, conversation_id)
    if graph_available:
        graph_mentions = _graph_entity_mentions(raw_graph_payload, conversation_id)
        entities, relations, clarification_tasks, warnings = _build_entities_from_graph(
            graph_mentions=graph_mentions,
            raw_graph_payload=raw_graph_payload,
            conversation_id=conversation_id,
        )
        entity_source = "conversation_graph"
        fallback_reason = None
        party_naming_reason = (
            "conversation_graph_projection_requires_current_party_clarification"
            if clarification_tasks
            else "conversation_graph_projection"
        )
        trace_steps = [
            "build_context",
            "read_conversation_graph",
            "project_graph_entities",
            "project_graph_relations",
            "build_conversation_entity_state",
        ]
    else:
        entities, relations, clarification_tasks = _build_fallback_entities_from_message(
            message_text=message_text,
            conversation_id=conversation_id,
        )
        entity_source = "fallback_rules"
        fallback_reason = "conversation_graph_unavailable"
        warnings = ["conversation_graph_unavailable; fallback entities are low confidence and must not become dossier canonical entities"]
        party_naming_reason = (
            "fallback_requires_conversation_layer_clarification"
            if clarification_tasks
            else "fallback_rules_low_confidence"
        )
        trace_steps = [
            "build_context",
            "conversation_graph_unavailable",
            "fallback_rules_projection",
            "build_conversation_entity_state",
        ]

    return {
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE,
        "provider": PROVIDER,
        "model": MODEL,
        "conversation_id": conversation_id,
        "should_build": True,
        "skip_reason": None,
        "entity_source": entity_source,
        "fallback_reason": fallback_reason,
        "entities": entities,
        "relations": relations,
        "clarification_tasks": clarification_tasks,
        "party_naming_decision": {
            "should_create_party_naming": False,
            "reason": party_naming_reason,
            "legacy_fallback_only": True,
        },
        "trace": {
            "steps": trace_steps,
            "warnings": warnings,
            "entity_source": entity_source,
            "fallback_reason": fallback_reason,
        },
    }


__all__ = ["build_conversation_entity_state"]
