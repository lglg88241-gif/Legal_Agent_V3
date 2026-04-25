from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Dossier
from app.services.conversation_store import (
    list_dossier_conversations,
    list_recent_conversation_messages,
)
from app.services.llm_service import llm_service

_MAX_CONVERSATIONS = 8
_MAX_MESSAGES_PER_CONVERSATION = 4
_MAX_TEXT_LENGTH = 300
_MAX_CASE_STATE_LENGTH = 500


def _trim_text(text: str | None, limit: int = _MAX_TEXT_LENGTH) -> str:
    normalized = " ".join((text or "").strip().split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _serialize_case_state(case_state: dict[str, Any] | None) -> str:
    if not isinstance(case_state, dict) or not case_state:
        return ""
    payload = json.dumps(case_state, ensure_ascii=False, sort_keys=True)
    return _trim_text(payload, _MAX_CASE_STATE_LENGTH)


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
    for index, conversation in enumerate(conversations[:_MAX_CONVERSATIONS], start=1):
        parts: list[str] = [
            f"会话{index}",
            f"标题：{_trim_text(conversation.title or '', 120) or f'会话 #{conversation.id}'}",
            f"模式：{getattr(conversation, 'conversation_mode', 'UNKNOWN') or 'UNKNOWN'}",
            f"保存同意：{getattr(conversation, 'persistence_consent', 'unknown') or 'unknown'}",
        ]

        case_type = _trim_text(getattr(conversation, "case_type", None) or "", 100)
        if case_type:
            parts.append(f"案由：{case_type}")

        case_summary = _trim_text(getattr(conversation, "case_summary", None) or "")
        if case_summary:
            parts.append(f"会话摘要：{case_summary}")

        case_state_text = _serialize_case_state(getattr(conversation, "case_state_json", None))
        if case_state_text:
            parts.append(f"结构化案情：{case_state_text}")

        messages = await list_recent_conversation_messages(
            db,
            conversation_id=conversation.id,
            limit=_MAX_MESSAGES_PER_CONVERSATION,
        )
        message_lines: list[str] = []
        for message in messages:
            content = _trim_text(message.content)
            if not content:
                continue
            role = "用户" if message.role == "user" else "助手"
            message_lines.append(f"- {role}：{content}")
        if message_lines:
            parts.append("最近对话：\n" + "\n".join(message_lines))

        if len(parts) <= 4:
            continue
        conversation_blocks.append("\n".join(parts))

    if not conversation_blocks:
        raise ValueError("暂无可用于生成摘要的绑定会话内容")

    return {
        "title": _trim_text(dossier.title or "", 120) or f"案卷 #{dossier.id}",
        "case_type": _trim_text(dossier.case_type or "", 100),
        "manual_summary": _trim_text(dossier.summary or "", 500),
        "conversation_blocks": conversation_blocks,
        "source_count": len(conversation_blocks),
    }


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
        "   - 已讨论法律问题\n"
        "   - 后续待确认事项\n"
        "4. 如果某部分信息不足，请明确写“待补充”。\n\n"
        f"[案卷标题]\n{materials['title']}\n\n"
        f"[案卷类型]\n{materials['case_type'] or '未分类'}\n\n"
        f"[用户手动摘要]\n{materials['manual_summary'] or '无'}\n\n"
        "[绑定会话材料]\n"
        + "\n\n".join(materials["conversation_blocks"])
    )

    summary = await llm_service.async_generate(
        prompt,
        system_prompt=(
            "你是一个谨慎的法律案卷整理助手。"
            "你的任务是把已有会话材料整理成结构化摘要，不得编造、补完或推测没有明确出现的事实。"
        ),
    )
    normalized_summary = (summary or "").strip()
    if not normalized_summary:
        raise RuntimeError("AI 摘要生成失败，请稍后重试")
    if normalized_summary.startswith("系统繁忙") or "Error:" in normalized_summary:
        raise RuntimeError(normalized_summary)

    return {
        "auto_summary": normalized_summary,
        "auto_summary_source_count": materials["source_count"],
    }
