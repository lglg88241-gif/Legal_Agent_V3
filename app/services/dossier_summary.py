from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Dossier
from app.services.conversation_store import (
    list_dossier_conversations,
    list_recent_conversation_messages,
)
from app.services.llm_service import llm_service

logger = logging.getLogger(__name__)

_MAX_CONVERSATIONS = 8
_MAX_MESSAGES_PER_CONVERSATION = 4
_MAX_TEXT_LENGTH = 300
_MAX_SUMMARY_LENGTH = 500
_FALLBACK_ITEM_LIMIT = 5

_EVIDENCE_KEYWORDS = (
    "工资流水",
    "聊天记录",
    "录音",
    "考勤",
    "合同",
    "解除通知",
    "仲裁申请",
    "证据",
    "邮件",
    "微信",
)


def _trim_text(text: str | None, limit: int = _MAX_TEXT_LENGTH) -> str:
    normalized = " ".join((text or "").strip().split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _case_state_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _format_currency(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    amount = float(value)
    if amount <= 0:
        return None
    if amount.is_integer():
        return f"{int(amount)} 元"
    return f"{amount:.2f} 元"


def _extract_case_state_facts(case_state: dict[str, Any] | None) -> list[str]:
    payload = _case_state_dict(case_state)
    facts: list[str] = []

    salary = _format_currency(payload.get("monthly_salary"))
    if salary:
        facts.append(f"月工资约为 {salary}")

    work_months = payload.get("work_months")
    if isinstance(work_months, (int, float)) and float(work_months) > 0:
        facts.append(f"已知工作时长约为 {int(float(work_months))} 个月")

    if payload.get("has_unsigned_contract") is True:
        facts.append("存在未签订书面劳动合同线索")
    if payload.get("is_illegal_dismissal") is True:
        facts.append("存在违法解除或辞退线索")
    if payload.get("has_owed_wages") is True:
        facts.append("存在欠付工资线索")

    return facts


def _extract_recent_user_messages(messages: list[Any]) -> list[str]:
    lines: list[str] = []
    for message in messages:
        if getattr(message, "role", "") != "user":
            continue
        content = _trim_text(getattr(message, "content", None))
        if content:
            lines.append(content)
    return lines


def _unique_lines(lines: list[str], *, limit: int | None = None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for line in lines:
        cleaned = _trim_text(line)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
        if limit is not None and len(result) >= limit:
            break
    return result


def _build_dispute_focus(item: dict[str, Any]) -> list[str]:
    case_type = str(item.get("case_type") or "").strip()
    state_facts = item.get("case_state_facts") if isinstance(item.get("case_state_facts"), list) else []

    focuses: list[str] = []
    if any("未签订书面劳动合同" in fact for fact in state_facts):
        focuses.append("未签订书面劳动合同责任如何认定")
    if any("违法解除" in fact or "辞退" in fact for fact in state_facts):
        focuses.append("解除或辞退是否合法以及赔偿如何计算")
    if any("欠付工资" in fact for fact in state_facts):
        focuses.append("是否存在欠薪及补发责任")
    if any("月工资" in fact or "工作时长" in fact for fact in state_facts):
        focuses.append("工资标准、工龄与赔偿基数如何认定")
    if case_type and not focuses:
        focuses.append(f"围绕 {case_type} 相关问题的责任划分")
    return focuses


async def build_dossier_summary_materials(
    db: AsyncSession,
    *,
    user_id: int,
    dossier: Dossier,
) -> dict[str, Any]:
    conversations = await list_dossier_conversations(db, user_id, dossier.id)
    if not conversations:
        raise ValueError("暂无可用于生成摘要的绑定会话内容")

    conversation_blocks: list[str] = []
    conversation_items: list[dict[str, Any]] = []
    for index, conversation in enumerate(conversations[:_MAX_CONVERSATIONS], start=1):
        case_type = _trim_text(getattr(conversation, "case_type", None) or "", 100)
        case_summary = _trim_text(getattr(conversation, "case_summary", None) or "", _MAX_SUMMARY_LENGTH)
        case_state_facts = _extract_case_state_facts(getattr(conversation, "case_state_json", None))
        messages = await list_recent_conversation_messages(
            db,
            conversation_id=conversation.id,
            limit=_MAX_MESSAGES_PER_CONVERSATION,
        )
        recent_user_messages = _extract_recent_user_messages(messages)

        parts: list[str] = [f"会话{index}"]
        if case_type:
            parts.append(f"案由：{case_type}")
        if case_summary:
            parts.append(f"会话摘要：{case_summary}")
        if case_state_facts:
            parts.append("结构化案情：\n" + "\n".join(f"- {line}" for line in case_state_facts))
        if recent_user_messages:
            parts.append(
                "最近用户消息：\n" + "\n".join(f"- {message}" for message in recent_user_messages)
            )

        if len(parts) <= 1:
            continue

        conversation_blocks.append("\n".join(parts))
        conversation_items.append(
            {
                "case_type": case_type,
                "case_summary": case_summary,
                "case_state_facts": case_state_facts,
                "recent_user_messages": recent_user_messages,
            }
        )

    if not conversation_blocks:
        raise ValueError("暂无可用于生成摘要的绑定会话内容")

    return {
        "title": _trim_text(dossier.title or "", 120) or f"案卷 #{dossier.id}",
        "case_type": _trim_text(dossier.case_type or "", 100),
        "manual_summary": _trim_text(dossier.summary or "", _MAX_SUMMARY_LENGTH),
        "conversation_blocks": conversation_blocks,
        "conversation_items": conversation_items,
        "source_count": len(conversation_items),
    }


def _build_fallback_auto_summary(materials: dict[str, Any]) -> str:
    items = materials.get("conversation_items")
    if not isinstance(items, list) or not items:
        raise ValueError("暂无可用于生成摘要的绑定会话内容")

    overview_candidates: list[str] = []
    if materials.get("manual_summary"):
        overview_candidates.append(f"用户手动摘要：{materials['manual_summary']}")
    elif materials.get("case_type"):
        overview_candidates.append(f"当前案卷围绕 {materials['case_type']} 相关问题持续跟踪。")

    fact_lines: list[str] = []
    dispute_lines: list[str] = []
    evidence_lines: list[str] = []
    todo_lines: list[str] = []

    salary_known = False
    evidence_found = False

    for item in items[:_FALLBACK_ITEM_LIMIT]:
        case_summary = str(item.get("case_summary") or "").strip()
        case_type = str(item.get("case_type") or "").strip()
        case_state_facts = item.get("case_state_facts") if isinstance(item.get("case_state_facts"), list) else []
        recent_user_messages = (
            item.get("recent_user_messages") if isinstance(item.get("recent_user_messages"), list) else []
        )

        if case_summary:
            overview_candidates.append(case_summary)
            fact_lines.append(case_summary)

        for fact in case_state_facts:
            if "月工资" in fact:
                salary_known = True
            fact_lines.append(fact)

        for focus in _build_dispute_focus(item):
            dispute_lines.append(focus)

        if case_type and not dispute_lines:
            dispute_lines.append(f"围绕 {case_type} 的责任划分仍需进一步确认")

        for message in recent_user_messages:
            if any(keyword in message for keyword in _EVIDENCE_KEYWORDS):
                evidence_lines.append(f"用户近期提到：{message}")
                evidence_found = True
            elif not case_summary:
                fact_lines.append(f"用户近期补充：{message}")

        if not recent_user_messages:
            todo_lines.append("最近补充事实较少，仍需继续核对关键时间线和证据材料")

    if not salary_known:
        todo_lines.append("月工资标准暂未确认，仍需结合工资流水或薪酬记录核对")
    if not evidence_found:
        todo_lines.append("现有材料中尚未明确列出完整证据目录，仍需继续补充和整理")
    if not dispute_lines:
        todo_lines.append("核心争议焦点仍需结合后续补充材料进一步确认")

    overview_lines = _unique_lines(overview_candidates, limit=3)
    fact_lines = _unique_lines(fact_lines, limit=6)
    dispute_lines = _unique_lines(dispute_lines, limit=5)
    evidence_lines = _unique_lines(evidence_lines, limit=5)
    todo_lines = _unique_lines(todo_lines, limit=5)

    return (
        "以下为系统根据已有会话内容自动整理的简要摘要，未经过大模型深度归纳。\n\n"
        "案情概览\n"
        f"{chr(10).join(f'- {line}' for line in (overview_lines or ['暂未确认']))}\n\n"
        "已知事实\n"
        f"{chr(10).join(f'- {line}' for line in (fact_lines or ['暂未确认']))}\n\n"
        "争议焦点\n"
        f"{chr(10).join(f'- {line}' for line in (dispute_lines or ['暂未确认']))}\n\n"
        "证据线索\n"
        f"{chr(10).join(f'- {line}' for line in (evidence_lines or ['暂未确认']))}\n\n"
        "后续待确认事项\n"
        f"{chr(10).join(f'- {line}' for line in (todo_lines or ['暂未确认']))}"
    )


async def generate_dossier_auto_summary(
    db: AsyncSession,
    *,
    user_id: int,
    dossier: Dossier,
) -> dict[str, Any]:
    materials = await build_dossier_summary_materials(db, user_id=user_id, dossier=dossier)
    prompt = (
        "请基于以下案卷与绑定会话材料，生成一份中文智能摘要。\n"
        "严格要求：\n"
        "1. 只能依据给定材料，不得编造未出现的事实。\n"
        "2. 这是案卷智能整理，不要覆盖或改写用户的手动摘要。\n"
        "3. 使用以下固定结构输出，每个标题都要保留：\n"
        "   - 案情概览\n"
        "   - 已知事实\n"
        "   - 争议焦点\n"
        "   - 证据线索\n"
        "   - 后续待确认事项\n"
        "4. 如某部分信息不足，请明确写“暂未确认”。\n\n"
        f"[案卷标题]\n{materials['title']}\n\n"
        f"[案卷类型]\n{materials['case_type'] or '未分类'}\n\n"
        f"[用户手动摘要]\n{materials['manual_summary'] or '无'}\n\n"
        "[绑定会话材料]\n"
        + "\n\n".join(materials["conversation_blocks"])
    )

    warning: str | None = None
    try:
        summary = await llm_service.async_generate(
            prompt,
            system_prompt=(
                "你是一个谨慎的法律案卷整理助手。"
                "你的任务是把已有会话材料整理成结构化摘要，不得编造、补完或推测没有明确出现的事实。"
            ),
        )
    except Exception as exc:
        logger.warning("Dossier summary LLM call failed, using fallback summary: %s", exc)
        summary = ""

    normalized_summary = (summary or "").strip()
    if not normalized_summary or normalized_summary.startswith("系统繁忙") or "Error:" in normalized_summary:
        normalized_summary = _build_fallback_auto_summary(materials)
        warning = "LLM unavailable, used fallback summary"

    result = {
        "auto_summary": normalized_summary,
        "auto_summary_source_count": materials["source_count"],
    }
    if warning:
        result["warning"] = warning
    return result
