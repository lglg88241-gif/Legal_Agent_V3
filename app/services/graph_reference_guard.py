from __future__ import annotations

import json
import logging
from typing import Any

from app.core.llm_factory import create_cloud_llm


logger = logging.getLogger(__name__)

PENDING_CLARIFICATION_TYPE = "subject_reference_resolution"
GUARD_SOURCE = "graph_reference_guard"
MODEL_NAME = "glm-4-flash"
MODEL_VERSION = "semantic-reference-guard-v1"
NON_RESPONSIBLE_CATEGORIES = {"客体", "事件", "后果"}
SELF_ANCHOR_PLACEHOLDER = "当事人"
SELF_REFERENCE_LABELS = {"我", "本人", "我本人", "自己", "当事人", "劳动者", "劳动者本人", "申请人", "我方", "我们"}
ORGANIZATION_REFERENCE_LABELS = {"公司", "单位", "用人单位"}
GENERIC_REFERENCE_LABELS = {
    "我",
    "本人",
    "自己",
    "当事人",
    "劳动者",
    "申请人",
    "我们",
    "公司",
    "单位",
    "用人单位",
    "他",
    "她",
    "TA",
    "ta",
    "这个人",
    "那个人",
    "对方",
    "人事",
    "领导",
    "主管",
    "经理",
    "负责人",
}
GENERIC_REFERENCE_CUES = tuple(sorted(GENERIC_REFERENCE_LABELS, key=len, reverse=True))
ORG_HINTS = (
    "公司",
    "科技",
    "集团",
    "企业",
    "单位",
    "工厂",
    "厂",
    "事务所",
    "银行",
    "医院",
    "学校",
    "机构",
    "社保局",
    "仲裁委",
    "法院",
    "人社局",
)

SYSTEM_PROMPT = """你是案情图谱写入前的语义指代解析器，不提供法律建议。

你的任务：判断当前用户事实叙述中的口语化指代，是否能结合原句、当前会话图谱候选主体、以及抽取器准备写入的图谱动作，安全解析为已有或明确出现的责任主体。

设计原则：
1. 你必须用语义上下文判断，不要做关键词替换。
2. 如果原句已经明确说明“我叫张三”，后续“我/本人/当事人”通常指张三。
3. 如果原句已经明确说明“在一家叫蓝海科技的公司”，后续“公司”通常指蓝海科技。
4. 如果只有一个合理候选主体，也可以解析，但要说明依据。
5. 如果有多个合理候选且原句无法区分，必须 needs_clarification=true，不能猜。
6. “你/助手/系统”不是案情责任主体，不能写入图谱；遇到这种指代应要求澄清或阻断。
7. 客体、事件、后果不是责任主体，不能作为 source/target 被错误解析成责任主体。
8. 如果待写入动作已使用明确实体名且没有含糊指代，可以 allow。
9. 如果只有“我/本人/当事人/劳动者/申请人”等第一人称自我锚点未命名，可以保留为“当事人”锚点写入会话图谱，同时要求用户确认真实姓名；不要因此整批丢弃 A 公司、劳动合同、辞退等明确事实。
10. 如果未解析的是“他/她/对方/公司/单位/人事/经理”等可能指向多个责任主体的泛称，必须只阻断含该泛称的动作，明确实体动作仍可写入。

只输出 JSON object，不要 markdown，不要解释文字。格式：
{
  "decision": "allow" | "clarify" | "block",
  "confidence": "high" | "medium" | "low",
  "reason": "...",
  "clarification_question": "",
  "resolutions": [
    {
      "mention": "公司",
      "resolved_label": "蓝海科技",
      "reference_type": "organization" | "self" | "person" | "unknown",
      "confidence": "high",
      "reason": "原句已说明在一家叫蓝海科技的公司"
    }
  ],
  "rewritten_actions": [
    {
      "action": "ADD",
      "source": "蓝海科技",
      "source_category": "主体",
      "relation": "拖欠工资",
      "target": "工资",
      "target_category": "客体",
      "old_target": "",
      "target_name": ""
    }
  ]
}
"""


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _normalize_key(value: Any) -> str:
    return _normalize_text(value).lower()


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


def _safe_json_loads(value: str) -> dict[str, Any] | None:
    text = _normalize_text(value)
    if not text:
        return None
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _properties(record: dict[str, Any]) -> dict[str, Any]:
    props = record.get("properties")
    return props if isinstance(props, dict) else {}


def _record_value(record: dict[str, Any], *keys: str) -> Any:
    props = _properties(record)
    for key in keys:
        if key in record and record.get(key) is not None:
            return record.get(key)
        if key in props and props.get(key) is not None:
            return props.get(key)
    return None


def _record_label(record: dict[str, Any]) -> str:
    return _normalize_text(
        _record_value(
            record,
            "name",
            "canonical_label",
            "display_label",
            "label",
            "raw_label",
        )
    )


def _record_category(record: dict[str, Any]) -> str:
    return _normalize_text(_record_value(record, "category", "node_type"))


def _record_entity_type(record: dict[str, Any]) -> str:
    return _normalize_text(_record_value(record, "entity_type", "type"))


def _record_role(record: dict[str, Any]) -> str:
    return _normalize_text(_record_value(record, "role", "participant_role", "party_role"))


def _looks_like_organization_label(label: str) -> bool:
    normalized = _normalize_text(label)
    return bool(normalized) and any(hint in normalized for hint in ORG_HINTS)


def _candidate_kind(record: dict[str, Any]) -> str:
    label = _record_label(record)
    category = _record_category(record)
    entity_type = _normalize_key(_record_entity_type(record))
    role = _normalize_key(_record_role(record))
    if not label or category in NON_RESPONSIBLE_CATEGORIES:
        return ""
    if role == "current_party":
        return "person"
    if entity_type in {"organization", "company", "employer", "unit"}:
        return "organization"
    if entity_type in {"person", "individual", "natural_person"}:
        return "person"
    if _looks_like_organization_label(label):
        return "organization"
    if len(label) in {2, 3, 4} and all("\u4e00" <= char <= "\u9fff" for char in label):
        return "person"
    return "unknown"


def _records_from_graph_payload(graph_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(graph_payload, dict):
        return []
    records: list[dict[str, Any]] = []
    for key in ("nodes", "graph_nodes", "matched_nodes", "participants", "participant_mentions"):
        values = graph_payload.get(key)
        if not isinstance(values, list):
            continue
        records.extend(node for node in values if isinstance(node, dict))
    return records


def _candidate_subjects(
    *,
    existing_node_records: list[dict[str, Any]] | None = None,
    graph_payload: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    records = [
        record
        for record in list(existing_node_records or []) + _records_from_graph_payload(graph_payload)
        if isinstance(record, dict)
    ]
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for record in records:
        label = _record_label(record)
        if not label or label in seen:
            continue
        kind = _candidate_kind(record)
        if not kind:
            continue
        seen.add(label)
        result.append(
            {
                "label": label,
                "kind": kind,
                "role": _record_role(record),
                "category": _record_category(record),
                "entity_type": _record_entity_type(record),
            }
        )
    return result


def _action_entity_slots(action: dict[str, Any]) -> list[str]:
    action_type = _normalize_text(action.get("action")).upper()
    if action_type == "ADD":
        return ["source", "target"]
    if action_type == "UPDATE":
        return ["source", "old_target", "target"]
    if action_type == "DELETE":
        return ["source", "old_target", "target", "target_name"]
    return []


def _graph_action_mentions(actions: list[dict[str, Any]]) -> list[str]:
    mentions: list[str] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        for slot in _action_entity_slots(action):
            value = _normalize_text(action.get(slot))
            if value:
                mentions.append(value)
    return _sorted_unique(mentions)


def _mentions_need_resolution(mentions: list[str]) -> bool:
    for mention in mentions:
        normalized = _normalize_text(mention)
        if normalized in GENERIC_REFERENCE_LABELS:
            return True
    return False


def _first_reference_mention(*values: list[str]) -> str:
    flat_values = [item for value in values for item in value]
    for item in flat_values:
        normalized = _normalize_text(item)
        if normalized in GENERIC_REFERENCE_LABELS:
            return normalized
    for cue in GENERIC_REFERENCE_CUES:
        if any(cue and cue in _normalize_text(item) for item in flat_values):
            return cue
    return _normalize_text(flat_values[0]) if flat_values else ""


def _actions_still_contain_unresolved_references(actions: list[dict[str, Any]]) -> bool:
    return _mentions_need_resolution(_graph_action_mentions(actions))


def _split_actions_by_unresolved_references(
    actions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    allowed: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        action_mentions = [
            _normalize_text(action.get(slot))
            for slot in _action_entity_slots(action)
            if _normalize_text(action.get(slot))
        ]
        unresolved_mentions = [
            mention
            for mention in action_mentions
            if mention in GENERIC_REFERENCE_LABELS
        ]
        blocking_mentions = [
            mention
            for mention in unresolved_mentions
            if mention not in SELF_REFERENCE_LABELS
        ]
        if blocking_mentions:
            blocked.append(action)
            continue
        if unresolved_mentions:
            next_action = dict(action)
            for slot in _action_entity_slots(next_action):
                if _normalize_text(next_action.get(slot)) in SELF_REFERENCE_LABELS:
                    next_action[slot] = SELF_ANCHOR_PLACEHOLDER
            allowed.append(next_action)
            continue
        allowed.append(action)
    return allowed, blocked


def _generic_reference_mentions(mentions: list[str]) -> list[str]:
    return _sorted_unique(
        [
            mention
            for mention in mentions
            if _normalize_text(mention) in GENERIC_REFERENCE_LABELS
        ]
    )


def _unresolved_reference_mentions(mentions: list[str], resolutions: list[dict[str, Any]]) -> list[str]:
    replacement_map = _resolution_replacement_map(resolutions)
    unresolved: list[str] = []
    for mention in mentions:
        normalized = _normalize_text(mention)
        if normalized not in GENERIC_REFERENCE_LABELS:
            continue
        if normalized not in replacement_map:
            unresolved.append(normalized)
    return _sorted_unique(unresolved)


def _resolution_confidence_allows(value: Any) -> bool:
    confidence = _normalize_key(value)
    return confidence in {"high", "medium", ""}


def _resolution_replacement_map(resolutions: list[dict[str, Any]]) -> dict[str, str]:
    replacements: dict[str, str] = {}
    for resolution in resolutions:
        if not isinstance(resolution, dict):
            continue
        resolved_label = _normalize_text(
            resolution.get("resolved_label")
            or resolution.get("target_label")
            or resolution.get("label")
        )
        if not resolved_label or not _resolution_confidence_allows(resolution.get("confidence")):
            continue
        mention = _normalize_text(resolution.get("mention"))
        reference_type = _normalize_key(resolution.get("reference_type"))
        if mention:
            replacements[mention] = resolved_label
        if reference_type == "self" or mention in SELF_REFERENCE_LABELS:
            for label in SELF_REFERENCE_LABELS:
                replacements[label] = resolved_label
        if reference_type == "organization" and (not mention or mention in ORGANIZATION_REFERENCE_LABELS):
            for label in ORGANIZATION_REFERENCE_LABELS:
                replacements[label] = resolved_label
    return replacements


def _rewrite_actions_from_resolutions(
    actions: list[dict[str, Any]],
    resolutions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    replacements = _resolution_replacement_map(resolutions)
    if not replacements:
        return [dict(action) for action in actions]

    rewritten: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        next_action = dict(action)
        for slot in _action_entity_slots(next_action):
            value = _normalize_text(next_action.get(slot))
            if value in replacements:
                next_action[slot] = replacements[value]
        rewritten.append(next_action)
    return rewritten


def _prior_guard_resolutions(prior_reference_guard: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(prior_reference_guard, dict):
        return []
    if prior_reference_guard.get("needs_clarification"):
        return []
    resolutions = prior_reference_guard.get("reference_resolutions")
    return [item for item in list(resolutions or []) if isinstance(item, dict)]


def _build_clarification_question(*, mention: str, candidates: list[str]) -> str:
    candidate_text = "、".join(candidates[:5])
    if candidate_text:
        return f"为避免把案卷图谱写错，这里的“{mention or '这个主体'}”指的是哪个主体？当前候选有：{candidate_text}。请直接回复主体名称。"
    return f"为避免把案卷图谱写错，这里的“{mention or '这个主体'}”具体指谁？请直接回复主体名称。"


def _pending_clarification(*, mention: str, question: str, user_input: str, reference_type: str = "unknown") -> dict[str, Any]:
    return {
        "type": PENDING_CLARIFICATION_TYPE,
        "source": GUARD_SOURCE,
        "target_mention": mention or "这个主体",
        "reference_type": reference_type or "unknown",
        "previous_question": question,
        "scope_attempted": ["current_conversation_graph"],
        "expected_response": "entity_name_or_context",
        "pending_graph_update_input_text": _normalize_text(user_input),
    }


def _build_guard_result(
    *,
    decision: str,
    confidence: str,
    reason: str,
    user_input: str,
    allowed_actions: list[dict[str, Any]] | None = None,
    blocked_actions: list[dict[str, Any]] | None = None,
    resolutions: list[dict[str, Any]] | None = None,
    clarification_question: str = "",
    mention: str = "",
    reference_type: str = "unknown",
    model: str = MODEL_NAME,
    provider: str = "zhipu",
) -> dict[str, Any]:
    needs_clarification = decision == "clarify"
    questions = [clarification_question] if clarification_question else []
    pending = (
        _pending_clarification(
            mention=mention,
            reference_type=reference_type,
            question=clarification_question,
            user_input=user_input,
        )
        if needs_clarification
        else None
    )
    return {
        "source": GUARD_SOURCE,
        "model_version": MODEL_VERSION,
        "provider": provider,
        "model": model,
        "decision": decision,
        "confidence": confidence,
        "reason": reason,
        "needs_clarification": needs_clarification,
        "clarification_questions": questions,
        "pending_clarification": pending,
        "reference_resolutions": resolutions or [],
        "allowed_actions": allowed_actions or [],
        "blocked_actions": blocked_actions or [],
    }


def _normalize_model_result(
    parsed: dict[str, Any],
    *,
    user_input: str,
    original_actions: list[dict[str, Any]],
    mentions: list[str] | None = None,
    fallback_candidates: list[str],
    provider: str,
    model: str,
) -> dict[str, Any]:
    decision = _normalize_key(parsed.get("decision"))
    if decision not in {"allow", "clarify", "block"}:
        decision = "clarify"
    confidence = _normalize_key(parsed.get("confidence"))
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"
    reason = _normalize_text(parsed.get("reason")) or "reference_guard_model_decision"
    resolutions = [
        item for item in list(parsed.get("resolutions") or []) if isinstance(item, dict)
    ]
    rewritten_actions = [
        item for item in list(parsed.get("rewritten_actions") or []) if isinstance(item, dict)
    ]
    clarification_question = _normalize_text(parsed.get("clarification_question"))
    first_resolution = resolutions[0] if resolutions else {}
    original_mentions = mentions or _graph_action_mentions(original_actions)
    mention_candidates = _unresolved_reference_mentions(original_mentions, resolutions)
    mention = (
        _normalize_text(first_resolution.get("mention"))
        if _normalize_text(first_resolution.get("mention")) in GENERIC_REFERENCE_LABELS
        else ""
    ) or _first_reference_mention(mention_candidates or original_mentions)
    reference_type = _normalize_text(first_resolution.get("reference_type")) or "unknown"
    original_needs_action_rewrite = bool(original_actions) and _actions_still_contain_unresolved_references(original_actions)
    partial_allowed_actions_for_clarify: list[dict[str, Any]] = []

    if decision == "allow":
        if confidence not in {"high", "medium"}:
            decision = "clarify"
            reason = f"{reason}; low_confidence_allow_requires_clarification"
        elif original_needs_action_rewrite and not rewritten_actions:
            rewritten_actions = _rewrite_actions_from_resolutions(original_actions, resolutions)
            if _actions_still_contain_unresolved_references(rewritten_actions):
                partial_allowed_actions, partial_blocked_actions = _split_actions_by_unresolved_references(
                    rewritten_actions
                )
                if partial_allowed_actions:
                    decision = "clarify"
                    partial_allowed_actions_for_clarify = partial_allowed_actions
                    rewritten_actions = []
                    original_actions = partial_blocked_actions
                    reason = f"{reason}; partial_write_with_unresolved_reference"
                else:
                    decision = "clarify"
                    rewritten_actions = []
                    reason = f"{reason}; missing_rewritten_actions_for_unresolved_reference"
        elif rewritten_actions and _actions_still_contain_unresolved_references(rewritten_actions):
            rewritten_actions = _rewrite_actions_from_resolutions(rewritten_actions, resolutions)
            if _actions_still_contain_unresolved_references(rewritten_actions):
                partial_allowed_actions, partial_blocked_actions = _split_actions_by_unresolved_references(
                    rewritten_actions
                )
                if partial_allowed_actions:
                    decision = "clarify"
                    partial_allowed_actions_for_clarify = partial_allowed_actions
                    rewritten_actions = []
                    original_actions = partial_blocked_actions
                    reason = f"{reason}; partial_write_with_unresolved_reference"
                else:
                    decision = "clarify"
                    rewritten_actions = []
                    reason = f"{reason}; rewritten_actions_still_contain_unresolved_reference"
        elif not rewritten_actions:
            rewritten_actions = original_actions

    if decision == "block":
        decision = "clarify"
        reason = f"{reason}; blocked_by_model"

    if decision == "clarify" and not clarification_question:
        clarification_question = _build_clarification_question(
            mention=mention,
            candidates=fallback_candidates,
        )

    allowed_actions = rewritten_actions if decision == "allow" else []
    blocked_actions = original_actions if decision == "clarify" else []
    if decision == "clarify" and partial_allowed_actions_for_clarify:
        allowed_actions = partial_allowed_actions_for_clarify

    return _build_guard_result(
        decision=decision,
        confidence=confidence,
        reason=reason,
        user_input=user_input,
        allowed_actions=allowed_actions,
        blocked_actions=blocked_actions,
        resolutions=resolutions,
        clarification_question=clarification_question,
        mention=mention,
        reference_type=reference_type,
        model=model,
        provider=provider,
    )


def _build_model_payload(
    *,
    user_input: str,
    mentions: list[str],
    actions: list[dict[str, Any]],
    candidate_subjects: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "user_input": _normalize_text(user_input),
        "mentions_to_check": mentions,
        "candidate_subjects": candidate_subjects,
        "proposed_graph_actions": actions,
    }


async def _call_reference_guard_model(payload: dict[str, Any]) -> dict[str, str]:
    llm = create_cloud_llm(model=MODEL_NAME, temperature=0.0, max_tokens=1600)
    response = await llm.ainvoke(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
        ]
    )
    content = response.content if hasattr(response, "content") else response
    return {
        "provider": "zhipu",
        "model": MODEL_NAME,
        "content": content if isinstance(content, str) else str(content or ""),
    }


def _fallback_guard(
    *,
    user_input: str,
    mentions: list[str],
    actions: list[dict[str, Any]],
    candidate_subjects: list[dict[str, str]],
    reason: str,
) -> dict[str, Any]:
    unresolved_action_mentions = _mentions_need_resolution(mentions)
    if not unresolved_action_mentions:
        return _build_guard_result(
            decision="allow",
            confidence="medium",
            reason=reason,
            user_input=user_input,
            allowed_actions=actions,
            provider="fallback",
            model="semantic-reference-guard-fallback",
        )

    mention = _first_reference_mention(mentions, [user_input]) or "这个主体"
    candidates = [item.get("label", "") for item in candidate_subjects if item.get("label")]
    question = _build_clarification_question(mention=mention, candidates=candidates)
    return _build_guard_result(
        decision="clarify",
        confidence="low",
        reason=reason,
        user_input=user_input,
        blocked_actions=actions,
        clarification_question=question,
        mention=mention,
        reference_type="unknown",
        provider="fallback",
        model="semantic-reference-guard-fallback",
    )


async def evaluate_graph_action_reference_guard_async(
    *,
    user_input: str,
    actions: list[dict[str, Any]] | None,
    existing_node_records: list[dict[str, Any]] | None = None,
    prior_reference_guard: dict[str, Any] | None = None,
    model_caller: Any | None = None,
) -> dict[str, Any]:
    normalized_actions = [dict(action) for action in list(actions or []) if isinstance(action, dict)]
    if not normalized_actions:
        return _build_guard_result(
            decision="allow",
            confidence="high",
            reason="no_graph_actions",
            user_input=user_input,
            allowed_actions=[],
        )

    candidates = _candidate_subjects(existing_node_records=existing_node_records)
    mentions = _graph_action_mentions(normalized_actions)
    reference_mentions = _generic_reference_mentions(mentions)
    prior_resolutions = _prior_guard_resolutions(prior_reference_guard)
    if prior_resolutions and reference_mentions:
        rewritten_actions = _rewrite_actions_from_resolutions(normalized_actions, prior_resolutions)
        if not _actions_still_contain_unresolved_references(rewritten_actions):
            return _build_guard_result(
                decision="allow",
                confidence=_normalize_key(prior_reference_guard.get("confidence")) or "high",
                reason="prior_reference_guard_resolutions_applied",
                user_input=user_input,
                allowed_actions=rewritten_actions,
                blocked_actions=[],
                resolutions=prior_resolutions,
                model=_normalize_text(prior_reference_guard.get("model")) or MODEL_NAME,
                provider=_normalize_text(prior_reference_guard.get("provider")) or "prior_guard",
            )
    if not reference_mentions:
        return _build_guard_result(
            decision="allow",
            confidence="high",
            reason="no_unresolved_action_references",
            user_input=user_input,
            allowed_actions=normalized_actions,
        )
    payload = _build_model_payload(
        user_input=user_input,
        mentions=reference_mentions,
        actions=normalized_actions,
        candidate_subjects=candidates,
    )
    caller = model_caller or _call_reference_guard_model
    try:
        raw = await caller(payload)
        provider = _normalize_text(raw.get("provider")) if isinstance(raw, dict) else "unknown"
        model = _normalize_text(raw.get("model")) if isinstance(raw, dict) else MODEL_NAME
        content = raw.get("content") if isinstance(raw, dict) else ""
        parsed = _safe_json_loads(str(content or ""))
        if not isinstance(parsed, dict):
            raise ValueError("reference guard model returned non-json")
        return _normalize_model_result(
            parsed,
            user_input=user_input,
            original_actions=normalized_actions,
            mentions=reference_mentions,
            fallback_candidates=[item.get("label", "") for item in candidates],
            provider=provider or "unknown",
            model=model or MODEL_NAME,
        )
    except Exception as exc:
        logger.warning("Reference guard model failed; using conservative fallback: %s", exc)
        return _fallback_guard(
            user_input=user_input,
            mentions=reference_mentions,
            actions=normalized_actions,
            candidate_subjects=candidates,
            reason="model_unavailable_conservative_fallback",
        )


__all__ = [
    "GUARD_SOURCE",
    "PENDING_CLARIFICATION_TYPE",
    "evaluate_graph_action_reference_guard_async",
]
