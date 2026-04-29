from __future__ import annotations

from typing import Any


ACK_SOURCE = "case_update_acknowledgement"
ACK_PROVIDER = "rules"
ACK_MODEL = "case-update-ack-rules-v1"
ACK_FALLBACK_TEXT = "已收到你的事实补充，系统会按当前会话设置进行处理。"
MAX_FACT_SUMMARY_LENGTH = 100


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _truncate_fact_summary(text: str, limit: int = MAX_FACT_SUMMARY_LENGTH) -> str:
    normalized = _normalize_text(text)
    if len(normalized) <= limit:
        return normalized
    shortened = normalized[: limit - 3].rstrip("，,；;。.!！？? ")
    return f"{shortened}..."


def _build_result(
    *,
    text: str,
    intent: str,
    graph_update_scheduled: bool,
    graph_update_actions: list[Any] | None,
    persistence_consent: str | None,
    used_persisted_fact_text: bool,
    fallback_used: bool = False,
    fallback_reason: str | None = None,
) -> dict[str, Any]:
    graph_update_actions_count: int | None = None
    if isinstance(graph_update_actions, list):
        graph_update_actions_count = len(graph_update_actions)
    return {
        "text": text,
        "source": ACK_SOURCE,
        "provider": ACK_PROVIDER,
        "model": ACK_MODEL,
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
        "trace": {
            "intent": intent,
            "graph_update_scheduled": graph_update_scheduled,
            "graph_update_actions_count": graph_update_actions_count,
            "persistence_consent": _normalize_text(persistence_consent) or None,
            "used_persisted_fact_text": used_persisted_fact_text,
        },
    }


def build_case_update_acknowledgement(
    *,
    plan: dict,
    intent: str,
    user_message_text: str,
    persisted_fact_text: str | None,
    graph_update_scheduled: bool,
    graph_update_actions: list | None,
    dossier_id: int | None,
    conversation_id: int,
    persistence_consent: str | None,
    route_intent: str,
    fallback_reason: str | None = None,
) -> dict[str, Any]:
    _ = dossier_id
    _ = conversation_id

    normalized_intent = _normalize_text(intent).lower()
    normalized_route_intent = _normalize_text(route_intent).upper()
    normalized_user_message = _normalize_text(user_message_text)
    normalized_persisted_fact = _normalize_text(persisted_fact_text)
    used_persisted_fact_text = bool(normalized_persisted_fact)
    effective_fact_text = normalized_persisted_fact or normalized_user_message
    fact_summary = _truncate_fact_summary(effective_fact_text)

    steps = plan.get("steps") if isinstance(plan, dict) else []
    step_names = {
        _normalize_text(step.get("name"))
        for step in steps
        if isinstance(step, dict) and _normalize_text(step.get("name"))
    }
    primary_route = _normalize_text(plan.get("primary_route")).upper() if isinstance(plan, dict) else ""

    if (
        primary_route != "CASE_UPDATE"
        or normalized_route_intent != "CASE_UPDATE"
        or "assistant_acknowledgement" not in step_names
    ):
        reason = fallback_reason or "plan_incomplete"
        return _build_result(
            text=ACK_FALLBACK_TEXT,
            intent=normalized_intent or "unknown",
            graph_update_scheduled=graph_update_scheduled,
            graph_update_actions=graph_update_actions,
            persistence_consent=persistence_consent,
            used_persisted_fact_text=used_persisted_fact_text,
            fallback_used=True,
            fallback_reason=reason,
        )

    if normalized_intent == "fact_update":
        if graph_update_scheduled:
            return _build_result(
                text=(
                    f"已记录这条新的案情事实：{fact_summary}。"
                    "\n系统会把相关主体、事件和证据线索纳入当前会话图谱和案卷后续分析。"
                ),
                intent=normalized_intent,
                graph_update_scheduled=graph_update_scheduled,
                graph_update_actions=graph_update_actions,
                persistence_consent=persistence_consent,
                used_persisted_fact_text=used_persisted_fact_text,
            )
        return _build_result(
            text=(
                f"已收到这条事实补充：{fact_summary}。"
                "\n当前会话设置不会写入结构化案情和图谱，如需持续跟踪可在案卷模式中开启。"
            ),
            intent=normalized_intent,
            graph_update_scheduled=graph_update_scheduled,
            graph_update_actions=graph_update_actions,
            persistence_consent=persistence_consent,
            used_persisted_fact_text=used_persisted_fact_text,
        )

    if normalized_intent == "confirm_previous_fact":
        if not normalized_persisted_fact:
            return _build_result(
                text="暂时没有可确认的事实，请先描述具体情况。",
                intent=normalized_intent,
                graph_update_scheduled=False,
                graph_update_actions=graph_update_actions,
                persistence_consent=persistence_consent,
                used_persisted_fact_text=False,
                fallback_used=True,
                fallback_reason=fallback_reason or "previous_fact_unavailable",
            )
        if graph_update_scheduled:
            return _build_result(
                text=(
                    f"已确认上一条事实：{fact_summary}。"
                    "\n系统会把对应主体、事件和关系信息更新到会话图谱和案卷分析。"
                ),
                intent=normalized_intent,
                graph_update_scheduled=graph_update_scheduled,
                graph_update_actions=graph_update_actions,
                persistence_consent=persistence_consent,
                used_persisted_fact_text=True,
            )
        return _build_result(
            text=(
                f"已确认上一条事实：{fact_summary}。"
                "\n当前会话设置不会写入结构化案情和图谱，如需持续跟踪可在案卷模式中开启。"
            ),
            intent=normalized_intent,
            graph_update_scheduled=graph_update_scheduled,
            graph_update_actions=graph_update_actions,
            persistence_consent=persistence_consent,
            used_persisted_fact_text=True,
        )

    return _build_result(
        text=ACK_FALLBACK_TEXT,
        intent=normalized_intent or "unknown",
        graph_update_scheduled=graph_update_scheduled,
        graph_update_actions=graph_update_actions,
        persistence_consent=persistence_consent,
        used_persisted_fact_text=used_persisted_fact_text,
        fallback_used=True,
        fallback_reason=fallback_reason or "unsupported_intent",
    )
