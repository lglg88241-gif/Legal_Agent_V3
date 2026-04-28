"""
Advisor node: grounded legal answer generation.

The deterministic calculation pipeline lives in app/logic/calculators.py.
This module is responsible for:
  - converting retrieval output into structured law / case grounding blocks,
  - building the final grounded generation prompt,
  - applying last-mile hallucination guards to the LLM output.

Architecture contract:
  - build_claim_payload() still produces authoritative numeric conclusions.
  - The LLM is allowed to rewrite narrative explanation, but must not change
    authoritative amounts or cite law articles outside retrieved material.
  - build_advisor_system_prompt() remains a pure function for testability.
"""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agent.graph import AgentState

from app.schemas.case_state import CaseState
from app.logic.calculators import PayloadResult, build_claim_payload
from app.agent.guards import (
    detect_fabricated_statute,
    sanitize_hallucinated_statutes,
    sanitize_llm_answer,
    sanitize_unverified_claims,
)
from app.core.llm_provider import LLMProvider
from app.services.dossier_graph_tool import classify_dossier_graph_query

logger = logging.getLogger(__name__)

_QUESTION_AMOUNT_PATTERN = re.compile(r"(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元")
_USER_STATUTE_PATTERN = re.compile(r"(《[^》]{1,40}》)(?:第\s*(\d+)\s*条)?")
_PAYLOAD_FACT_KEYWORDS = (
    "赔偿金",
    "差额",
    "代通知金",
    "欠薪",
    "总额",
    "共计",
    "补助金",
    "补偿金",
    "一次性伤残补助金",
    "经济补偿",
)
_RAG_PREVIEW_LIMIT = 350
_MAX_OTHER_REFERENCE_ITEMS = 5
_DOSSIER_QUESTION_DEFAULT = "default"
_DOSSIER_QUESTION_MEMORY = "dossier_memory"
_DOSSIER_QUESTION_ADVICE = "dossier_advice"


def _normalize_number_token(raw: str) -> str:
    cleaned = raw.replace(",", "").replace("，", "")
    try:
        value = float(cleaned)
    except ValueError:
        return cleaned
    return str(int(value)) if value.is_integer() else cleaned


def _extract_question_amounts(question: str) -> list[str]:
    amounts: list[str] = []
    seen: set[str] = set()
    for match in _QUESTION_AMOUNT_PATTERN.finditer(question or ""):
        normalized = _normalize_number_token(match.group(1))
        if normalized in seen:
            continue
        seen.add(normalized)
        amounts.append(normalized)
    return amounts


def _truncate_rag_text(text: str, limit: int = _RAG_PREVIEW_LIMIT) -> str:
    normalized = re.sub(r"\s+", " ", text or "").strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _coerce_rag_score(entry: dict[str, Any]) -> float:
    for key in ("score", "coarse_score"):
        value = entry.get(key)
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return float("-inf")


def _clean_rag_snippet(snippet: str, *prefixes: str) -> str:
    result = snippet.strip()
    for prefix in prefixes:
        cleaned_prefix = (prefix or "").strip()
        if cleaned_prefix and result.startswith(cleaned_prefix):
            result = result[len(cleaned_prefix):].lstrip("：:，,;；。 \n")
    return result.strip()


def _normalize_dossier_question_mode(value: str | None) -> str:
    normalized = str(value or "").strip()
    if normalized in {
        _DOSSIER_QUESTION_DEFAULT,
        _DOSSIER_QUESTION_MEMORY,
        _DOSSIER_QUESTION_ADVICE,
    }:
        return normalized
    return _DOSSIER_QUESTION_DEFAULT


def _classify_dossier_question_mode(
    *,
    question: str,
    dossier_context: dict[str, Any] | None,
    dossier_graph_context: dict[str, Any] | None,
) -> str:
    if not isinstance(dossier_context, dict) and not isinstance(dossier_graph_context, dict):
        return _DOSSIER_QUESTION_DEFAULT

    query_kind = classify_dossier_graph_query(question)
    if query_kind == "memory":
        return _DOSSIER_QUESTION_MEMORY
    if query_kind == "advice":
        return _DOSSIER_QUESTION_ADVICE
    return _DOSSIER_QUESTION_DEFAULT


def _build_advisor_user_instruction(question: str, question_mode: str) -> str:
    normalized_mode = _normalize_dossier_question_mode(question_mode)
    if normalized_mode == _DOSSIER_QUESTION_MEMORY:
        return (
            f"用户的问题：{question}\n"
            "当前问题属于案卷记忆 / 主体指代查询。请优先依据 system 中的案卷上下文、案卷图谱摘要和案卷图谱查询结果作答。"
            "如果用户在问“当事人 / 我 / 本人 / 这个公司 / 这个人指的是谁”，先直接回答主体是谁，"
            "再用一句话说明依据来自案卷中已确认的主体映射、participant overlay 或原始标签映射。"
            "如果案卷里还有其他未确认的相似主体，只能说明“尚未确认，不能自动视为同一主体”，不要擅自合并。"
            "除非用户明确追问法律依据或下一步怎么办，否则不要主动展开法条、案例、仲裁建议或维权步骤。"
            "整段回答控制在 3 到 6 句话内。"
        )
    if normalized_mode == _DOSSIER_QUESTION_ADVICE:
        return (
            f"用户的问题：{question}\n"
            "请结合 system 中的案卷上下文、案卷图谱背景和相关法律检索结果，先直接回答用户的具体任务问题，"
            "再补充真正相关的法律依据与下一步建议。"
        )
    return (
        f"用户的问题：{question}\n"
        "请基于 system 中的【已确认事实】【相关法条原文】【参考案例】给出回答。"
        "必须引用法条编号，必须有下一步建议。"
    )


def _split_rag_by_type(tool_results: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {"laws": [], "cases": [], "others": []}
    seen: set[str] = set()

    for item in tool_results:
        result = item.get("result")
        if not isinstance(result, str):
            continue
        try:
            parsed = json.loads(result)
        except Exception:
            continue
        if not isinstance(parsed, list):
            continue

        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
            content = _truncate_rag_text(str(entry.get("content") or ""))
            if not content:
                continue

            source = str(entry.get("source") or metadata.get("source") or "").strip()
            candidate_id = str(entry.get("candidate_id") or "").strip()
            dedupe_key = candidate_id or json.dumps(
                {
                    "source": source,
                    "content": content,
                    "metadata": metadata,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            normalized_entry = {
                "content": content,
                "source": source,
                "metadata": metadata,
                "score": _coerce_rag_score(entry),
                "candidate_id": candidate_id,
            }

            if metadata.get("article") or metadata.get("law_title"):
                grouped["laws"].append(normalized_entry)
            elif metadata.get("case_name"):
                grouped["cases"].append(normalized_entry)
            else:
                grouped["others"].append(normalized_entry)

    grouped["laws"].sort(key=lambda item: item.get("score", float("-inf")), reverse=True)
    grouped["cases"].sort(key=lambda item: item.get("score", float("-inf")), reverse=True)
    grouped["others"].sort(key=lambda item: item.get("score", float("-inf")), reverse=True)
    grouped["laws"] = grouped["laws"][:8]
    grouped["cases"] = grouped["cases"][:3]
    grouped["others"] = grouped["others"][:_MAX_OTHER_REFERENCE_ITEMS]
    return grouped


def _format_laws_block(law_entries: list[dict[str, Any]]) -> str:
    if not law_entries:
        return "无"

    lines: list[str] = []
    for index, entry in enumerate(law_entries, start=1):
        metadata = entry.get("metadata") or {}
        law_title = str(metadata.get("law_title") or "").strip()
        article = str(metadata.get("article") or "").strip()
        source = str(entry.get("source") or "").strip()
        snippet = _clean_rag_snippet(entry.get("content") or "", law_title, article)
        header = f"{law_title}{article}".strip() or source or f"法条材料{index}"
        lines.append(f"{index}. {header}：{snippet or entry.get('content') or ''}")
    return "\n".join(lines)


def _format_cases_block(case_entries: list[dict[str, Any]]) -> str:
    if not case_entries:
        return "无"

    lines: list[str] = []
    for index, entry in enumerate(case_entries, start=1):
        metadata = entry.get("metadata") or {}
        case_name = str(metadata.get("case_name") or "").strip()
        source = str(entry.get("source") or "").strip()
        page = metadata.get("page")
        snippet = _clean_rag_snippet(entry.get("content") or "", case_name)
        source_hint = ""
        if source:
            source_hint = f"（来源：{source}"
            if page:
                source_hint += f" 第{page}页"
            source_hint += "）"
        header = case_name or source or f"案例材料{index}"
        lines.append(f"{index}. {header}{source_hint} — 裁判要点：{snippet or entry.get('content') or ''}")
    return "\n".join(lines)


def _format_other_rag_context(other_entries: list[dict[str, Any]]) -> str:
    if not other_entries:
        return "无"

    lines: list[str] = []
    for index, entry in enumerate(other_entries, start=1):
        source = str(entry.get("source") or "").strip() or f"其他材料{index}"
        lines.append(f"{index}. [{source}] {entry.get('content') or ''}")
    return "\n".join(lines)


def _format_dossier_context_block(dossier_context: dict[str, Any] | None) -> str:
    if not isinstance(dossier_context, dict):
        return ""

    title = str(dossier_context.get("title") or "").strip() or "未命名案卷"
    case_type = str(dossier_context.get("case_type") or "").strip() or "未分类"
    summary = str(dossier_context.get("summary") or "").strip() or "暂无案卷摘要"
    status = str(dossier_context.get("status") or "").strip() or "active"
    conversation_count = int(dossier_context.get("conversation_count") or 0)
    active_conversation_id = dossier_context.get("active_conversation_id")
    conversations = dossier_context.get("conversations")

    conversation_lines: list[str] = []
    if isinstance(conversations, list):
        for index, item in enumerate(conversations[:5], start=1):
            if not isinstance(item, dict):
                continue
            conversation_id = item.get("id")
            title_text = str(item.get("title") or "").strip() or f"会话 #{conversation_id or index}"
            mode = str(item.get("conversation_mode") or "UNKNOWN").strip()
            consent = str(item.get("persistence_consent") or "unknown").strip()
            summary_text = str(item.get("case_summary") or "").strip()
            case_type_text = str(item.get("case_type") or "").strip()
            markers: list[str] = [f"mode={mode}", f"consent={consent}"]
            if case_type_text:
                markers.append(f"case_type={case_type_text}")
            if conversation_id == active_conversation_id:
                markers.append("current_conversation=true")
            line = f"{index}. {title_text} ({', '.join(markers)})"
            if summary_text:
                line += f" | 摘要：{summary_text[:120]}"
            conversation_lines.append(line)

    if not conversation_lines:
        conversation_lines.append("无已绑定会话摘要")

    return (
        "<dossier_context>\n"
        "[案卷上下文]\n"
        f"标题：{title}\n"
        f"类型：{case_type}\n"
        f"摘要：{summary}\n"
        f"状态：{status}\n"
        f"绑定会话数量：{conversation_count}\n"
        "已绑定会话：\n"
        f"{chr(10).join(conversation_lines)}\n"
        "使用规则：案卷上下文只能作为背景记忆和事实线索，不能替代检索到的法条原文、案例或其他RAG依据。"
        "如果案卷上下文与本轮用户输入冲突，优先指出冲突并向用户澄清，不要直接沿用旧案情。\n"
        "</dossier_context>"
    )

def _format_dossier_context_block(dossier_context: dict[str, Any] | None) -> str:
    if not isinstance(dossier_context, dict):
        return ""

    title = str(dossier_context.get("title") or "").strip() or "未命名案卷"
    case_type = str(dossier_context.get("case_type") or "").strip() or "未分类"
    summary = str(dossier_context.get("summary") or "").strip() or "暂无案卷摘要"
    status = str(dossier_context.get("status") or "").strip() or "active"
    conversation_count = int(dossier_context.get("conversation_count") or 0)
    active_conversation_id = dossier_context.get("active_conversation_id")
    conversations = dossier_context.get("conversations")
    participant_aliases = dossier_context.get("participant_aliases")

    conversation_lines: list[str] = []
    if isinstance(conversations, list):
        for index, item in enumerate(conversations[:5], start=1):
            if not isinstance(item, dict):
                continue
            conversation_id = item.get("id")
            title_text = str(item.get("title") or "").strip() or f"会话 #{conversation_id or index}"
            mode = str(item.get("conversation_mode") or "UNKNOWN").strip()
            consent = str(item.get("persistence_consent") or "unknown").strip()
            summary_text = str(item.get("case_summary") or "").strip()
            case_type_text = str(item.get("case_type") or "").strip()
            markers: list[str] = [f"mode={mode}", f"consent={consent}"]
            if case_type_text:
                markers.append(f"case_type={case_type_text}")
            if conversation_id == active_conversation_id:
                markers.append("current_conversation=true")
            line = f"{index}. {title_text} ({', '.join(markers)})"
            if summary_text:
                line += f" | 摘要：{summary_text[:120]}"
            conversation_lines.append(line)

    if not conversation_lines:
        conversation_lines.append("无已绑定会话摘要")

    alias_lines: list[str] = []
    if isinstance(participant_aliases, dict):
        for key in ("primary_party", "opposing_party"):
            item = participant_aliases.get(key)
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            role = str(item.get("role") or "").strip()
            party_side = str(item.get("party_side") or "").strip()
            confirmed = bool(item.get("confirmed_by_user"))
            if not label and not role:
                continue
            alias_lines.append(
                f"- {key}: label={label or '未填写'}; role={role or '未填写'}; "
                f"party_side={party_side or key}; confirmed_by_user={str(confirmed).lower()}"
            )

    if not alias_lines:
        alias_lines.append("- participant_aliases: none")

    return (
        "<dossier_context>\n"
        "[案卷上下文]\n"
        f"标题：{title}\n"
        f"类型：{case_type}\n"
        f"摘要：{summary}\n"
        f"状态：{status}\n"
        f"绑定会话数量：{conversation_count}\n"
        "participant_aliases:\n"
        f"{chr(10).join(alias_lines)}\n"
        "已绑定会话：\n"
        f"{chr(10).join(conversation_lines)}\n"
        "使用规则：案卷上下文只能作为背景记忆和事实线索，不能替代检索到的法律原文、案例或其他 RAG 依据。\n"
        "participant_aliases 只是用户确认的案卷别名，不等同真实姓名；不得编造其真实身份、背景信息或补充用户未提供的新事实。\n"
        "participant_aliases 仅在当前案卷内生效，不得跨案卷自动合并同名主体；不同案卷中的“我”“公司”“对方”默认不是同一实体。\n"
        "如果案卷上下文与本轮用户输入冲突，优先指出冲突并向用户澄清，不要直接沿用旧案情。\n"
        "</dossier_context>"
    )


def _format_dossier_graph_summary_block(dossier_graph_summary: str | None) -> str:
    if not isinstance(dossier_graph_summary, str):
        return ""
    return dossier_graph_summary.strip()


def _format_dossier_graph_context_block(dossier_graph_context: dict[str, Any] | None) -> str:
    if not isinstance(dossier_graph_context, dict):
        return ""

    if str(dossier_graph_context.get("source") or "").strip() != "graph_context":
        return ""

    scope = dossier_graph_context.get("scope") if isinstance(dossier_graph_context.get("scope"), dict) else {}
    stats = dossier_graph_context.get("stats") if isinstance(dossier_graph_context.get("stats"), dict) else {}
    participants = (
        dossier_graph_context.get("participants")
        if isinstance(dossier_graph_context.get("participants"), list)
        else []
    )
    mentions = (
        dossier_graph_context.get("participant_mentions")
        if isinstance(dossier_graph_context.get("participant_mentions"), list)
        else []
    )
    matched_nodes = (
        dossier_graph_context.get("matched_nodes")
        if isinstance(dossier_graph_context.get("matched_nodes"), list)
        else []
    )
    matched_edges = (
        dossier_graph_context.get("matched_edges")
        if isinstance(dossier_graph_context.get("matched_edges"), list)
        else []
    )
    warnings = (
        dossier_graph_context.get("warnings")
        if isinstance(dossier_graph_context.get("warnings"), list)
        else []
    )
    notes = (
        dossier_graph_context.get("notes")
        if isinstance(dossier_graph_context.get("notes"), list)
        else []
    )
    truncated = bool(dossier_graph_context.get("truncated", False))
    query_type = str(dossier_graph_context.get("query_type") or "").strip() or "summary"
    query = str(dossier_graph_context.get("query") or "").strip()
    legal_basis = bool(dossier_graph_context.get("legal_basis", False))

    participant_lines: list[str] = []
    for item in participants[:5]:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        role = str(item.get("role") or "").strip()
        side = str(item.get("party_side") or "").strip() or "unknown"
        entity_type = str(item.get("entity_type") or "").strip() or "unknown"
        if not label and not role:
            continue
        participant_lines.append(
            f"- label={label or '未填写'}; role={role or '未填写'}; party_side={side}; entity_type={entity_type}"
        )
    if not participant_lines:
        participant_lines.append("- none")

    mention_lines: list[str] = []
    for item in mentions[:6]:
        if not isinstance(item, dict):
            continue
        raw_label = str(item.get("raw_label") or "").strip()
        participant_label = str(item.get("participant_label") or "").strip()
        source = str(item.get("source") or "").strip() or "graph"
        if not raw_label and not participant_label:
            continue
        mention_lines.append(
            f"- raw_label={raw_label or '未填写'}; participant_label={participant_label or '未确认'}; source={source}"
        )
    if not mention_lines:
        mention_lines.append("- none")

    node_lines: list[str] = []
    for item in matched_nodes[:8]:
        if not isinstance(item, dict):
            continue
        display_label = str(item.get("display_label") or item.get("label") or "").strip()
        raw_label = str(item.get("raw_label") or "").strip()
        node_type = str(item.get("type") or "").strip() or "unknown"
        conversation_id = item.get("conversation_id")
        if not display_label and not raw_label:
            continue
        suffix = f"; conversation_id={conversation_id}" if conversation_id is not None else ""
        raw_suffix = f"; raw_label={raw_label}" if raw_label and raw_label != display_label else ""
        node_lines.append(
            f"- label={display_label or raw_label}; type={node_type}{suffix}{raw_suffix}"
        )
    if not node_lines:
        node_lines.append("- none")

    edge_lines: list[str] = []
    for item in matched_edges[:8]:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("type") or "").strip() or "CASE_RELATION"
        source = str(item.get("source") or "").strip() or "unknown"
        target = str(item.get("target") or "").strip() or "unknown"
        edge_lines.append(f"- source={source}; relation={label}; target={target}")
    if not edge_lines:
        edge_lines.append("- none")

    warning_lines = [f"- {str(item).strip()}" for item in warnings if str(item).strip()]
    if not warning_lines:
        warning_lines.append("- none")

    note_lines = [f"- {str(item).strip()}" for item in notes if str(item).strip()]
    note_lines.extend(
        [
            "- 以上 graph_context 仅作案情背景，不是法律依据，不能作为法条或案例来源引用。",
            "- 回答引用这部分信息时，应表述为：根据你当前案卷中已记录/已确认的信息……",
            "- 如 graph_context 与本轮用户输入冲突，应先澄清，不得直接沿用历史图谱结论。",
            "- 未经用户确认的主体不能直接视为同一主体；同名主体也不得跨案卷自动合并。",
        ]
    )
    if truncated:
        note_lines.append("- 本次 graph_context 已按条数限制截断，只展示部分案情图谱结果。")

    return (
        "<dossier_graph_context>\n"
        "案卷图谱查询结果（仅作案情背景，不是法律依据）\n"
        f"query_type={query_type}\n"
        f"query={query or 'none'}\n"
        f"legal_basis={str(legal_basis).lower()}\n"
        f"scope.user_id={scope.get('user_id')}\n"
        f"scope.dossier_id={scope.get('dossier_id')}\n"
        f"scope.conversation_id={scope.get('conversation_id')}\n"
        f"stats: conversation_count={int(stats.get('conversation_count') or 0)}, "
        f"node_count={int(stats.get('node_count') or 0)}, edge_count={int(stats.get('edge_count') or 0)}\n"
        "confirmed_participants:\n"
        f"{chr(10).join(participant_lines)}\n"
        "participant_mentions:\n"
        f"{chr(10).join(mention_lines)}\n"
        "matched_nodes:\n"
        f"{chr(10).join(node_lines)}\n"
        "matched_edges:\n"
        f"{chr(10).join(edge_lines)}\n"
        "warnings:\n"
        f"{chr(10).join(warning_lines)}\n"
        "notes:\n"
        f"{chr(10).join(note_lines)}\n"
        "</dossier_graph_context>"
    )


def _format_dossier_context_block_v2(dossier_context: dict[str, Any] | None) -> str:
    if not isinstance(dossier_context, dict):
        return ""

    title = str(dossier_context.get("title") or "").strip() or "未命名案卷"
    case_type = str(dossier_context.get("case_type") or "").strip() or "未分类"
    summary = str(dossier_context.get("summary") or "").strip() or "暂无案卷摘要"
    status = str(dossier_context.get("status") or "").strip() or "active"
    conversation_count = int(dossier_context.get("conversation_count") or 0)
    active_conversation_id = dossier_context.get("active_conversation_id")
    conversations = dossier_context.get("conversations")
    participant_aliases = dossier_context.get("participant_aliases")
    confirmed_participants = dossier_context.get("confirmed_participants")

    conversation_lines: list[str] = []
    if isinstance(conversations, list):
        for index, item in enumerate(conversations[:5], start=1):
            if not isinstance(item, dict):
                continue
            conversation_id = item.get("id")
            title_text = str(item.get("title") or "").strip() or f"会话 #{conversation_id or index}"
            mode = str(item.get("conversation_mode") or "UNKNOWN").strip()
            consent = str(item.get("persistence_consent") or "unknown").strip()
            summary_text = str(item.get("case_summary") or "").strip()
            case_type_text = str(item.get("case_type") or "").strip()
            markers: list[str] = [f"mode={mode}", f"consent={consent}"]
            if case_type_text:
                markers.append(f"case_type={case_type_text}")
            if conversation_id == active_conversation_id:
                markers.append("current_conversation=true")
            line = f"{index}. {title_text} ({', '.join(markers)})"
            if summary_text:
                line += f" | 摘要：{summary_text[:120]}"
            conversation_lines.append(line)

    if not conversation_lines:
        conversation_lines.append("无已绑定会话摘要")

    participant_lines: list[str] = []
    if isinstance(confirmed_participants, list):
        for item in confirmed_participants:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            role = str(item.get("role") or "").strip()
            party_side = str(item.get("party_side") or "").strip() or "unknown"
            entity_type = str(item.get("entity_type") or "").strip() or "unknown"
            if not label and not role:
                continue
            participant_lines.append(
                f"- label={label or '未填写'}; role={role or '未填写'}; "
                f"party_side={party_side}; entity_type={entity_type}; confirmed_by_user=true"
            )

    alias_lines: list[str] = []
    if isinstance(participant_aliases, dict):
        for key in ("primary_party", "opposing_party"):
            item = participant_aliases.get(key)
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            role = str(item.get("role") or "").strip()
            party_side = str(item.get("party_side") or "").strip()
            confirmed = bool(item.get("confirmed_by_user"))
            if not label and not role:
                continue
            alias_lines.append(
                f"- {key}: label={label or '未填写'}; role={role or '未填写'}; "
                f"party_side={party_side or key}; confirmed_by_user={str(confirmed).lower()}"
            )

    if not participant_lines:
        participant_lines.append("- confirmed_participants: none")
    if not alias_lines:
        alias_lines.append("- participant_aliases: none")

    return (
        "<dossier_context>\n"
        "[案卷上下文]\n"
        f"标题：{title}\n"
        f"类型：{case_type}\n"
        f"摘要：{summary}\n"
        f"状态：{status}\n"
        f"绑定会话数量：{conversation_count}\n"
        "confirmed_participants:\n"
        f"{chr(10).join(participant_lines)}\n"
        "participant_aliases:\n"
        f"{chr(10).join(alias_lines)}\n"
        "已绑定会话：\n"
        f"{chr(10).join(conversation_lines)}\n"
        "使用规则：案卷上下文只能作为背景记忆和事实线索，不能替代检索到的法律原文、案例或其他 RAG 依据。\n"
        "confirmed participant labels 和 participant_aliases 都只是当前案卷内用户确认的称呼，不等同真实姓名；不得编造其真实身份、背景信息或补充用户未提供的新事实。\n"
        "confirmed participant labels 和 participant_aliases 仅在当前案卷内生效。\n"
        "未经用户确认的主体只能视为未确认 mention，不得直接视为同一主体。\n"
        "不得跨案卷自动合并同名主体；不同案卷中的“我”“公司”“对方”默认不是同一实体。\n"
        "如果案卷上下文与本轮用户输入冲突，优先指出冲突并向用户澄清，不要直接沿用旧案情。\n"
        "</dossier_context>"
    )


_format_dossier_context_block = _format_dossier_context_block_v2


def _build_guard_rag_context(
    laws_block: str,
    cases_block: str,
    other_rag_context: str,
) -> str:
    sections = [
        section.strip()
        for section in (laws_block, cases_block, other_rag_context)
        if isinstance(section, str) and section.strip() and section.strip() != "无"
    ]
    return "\n\n".join(sections) or "无有效检索结果"


def _extract_payload_facts(payload: PayloadResult) -> str:
    facts: list[str] = []
    seen: set[str] = set()

    for raw_line in payload.answer.splitlines():
        cleaned = raw_line.strip()
        if not cleaned:
            continue
        normalized = cleaned.lstrip("-•").strip()
        if not normalized:
            continue
        if not (
            any(keyword in normalized for keyword in _PAYLOAD_FACT_KEYWORDS)
            or re.search(r"《[^》]{1,40}》第[一二三四五六七八九十百零\d]+条", normalized)
        ):
            continue
        bullet = f"- {normalized}"
        if bullet in seen:
            continue
        seen.add(bullet)
        facts.append(bullet)

    return "\n".join(facts) if facts else "无已确认金额结论"


def _build_amount_verification_block(question: str, payload: PayloadResult) -> str:
    question_amounts = _extract_question_amounts(question)
    if not question_amounts:
        return ""

    authoritative_numbers: set[str] = set()
    for step in payload.trace.steps:
        authoritative_numbers.add(_normalize_number_token(str(step.result)))
    authoritative_numbers.add(_normalize_number_token(str(payload.trace.total)))
    for notice in payload.trace.correction_notices:
        authoritative_numbers.update(
            {
                _normalize_number_token(str(notice.user_claimed)),
                _normalize_number_token(str(notice.legal_limit)),
                _normalize_number_token(str(notice.discrepancy)),
            }
        )
        authoritative_numbers.update(
            amount for amount in question_amounts if amount in notice.explanation
        )

    unverified_amounts = [amount for amount in question_amounts if amount not in authoritative_numbers]
    if not unverified_amounts:
        return ""

    joined_amounts = "、".join(f"{amount}元" for amount in unverified_amounts)
    return f"""

<amount_verification_notice priority="VERBATIM">
用户提出了具体金额主张（{joined_amounts}），但系统当前尚未产出对应的核验结果。
你必须明确说明这些金额未经系统验证，不能直接认同，也不能围绕这些未经核验的数字展开维权策略推演。
你应当把回答重心放在“还需要哪些事实才能计算”，例如月工资、工作时间、欠薪月数、赔偿项目等。
</amount_verification_notice>"""


def _extract_user_statute_references(question: str) -> list[tuple[str, str | None]]:
    references: list[tuple[str, str | None]] = []
    seen: set[tuple[str, str | None]] = set()
    for match in _USER_STATUTE_PATTERN.finditer(question or ""):
        law_name = match.group(1)
        article = f"第{match.group(2)}条" if match.group(2) else None
        item = (law_name, article)
        if item in seen:
            continue
        seen.add(item)
        references.append(item)
    return references


def _rag_contains_statute_reference(rag_context: str, law_name: str, article: str | None) -> bool:
    normalized_rag = re.sub(r"\s+", "", rag_context or "")
    if law_name not in normalized_rag:
        return False
    if not article:
        return True
    combined = f"{law_name}{article}"
    return combined in normalized_rag or article in normalized_rag


def _build_statute_verification_block(question: str, rag_context: str) -> str:
    missing_references: list[str] = []
    for law_name, article in _extract_user_statute_references(question):
        if _rag_contains_statute_reference(rag_context, law_name, article):
            continue
        missing_references.append(f"{law_name}{article or ''}")

    if not missing_references:
        return ""

    joined_references = "、".join(missing_references)
    return f"""

<statute_verification_notice priority="VERBATIM">
用户提到了以下具体法律/条文，但知识库当前未检索到对应原文：{joined_references}。
你必须明确告知知识库未收录或未检索到该法律/条文，并建议用户通过国家法律法规数据库等官方渠道核验。
严禁凭训练记忆补充条文内容，严禁把它替换映射成其他法律条文后继续作答。
</statute_verification_notice>"""


def _payload_has_authoritative_claims(payload: PayloadResult) -> bool:
    return bool(
        payload.trace.steps
        or payload.trace.correction_notices
        or payload.trace.total > 0
    )


def _build_statute_guard_rag_sources(
    tool_results: list[dict[str, Any]],
    candidate_sources: list[str],
) -> list[str]:
    """
    Build a clean RAG source list for statute guards.

    Important: do NOT use build_context_blocks() output here, because it embeds
    tool-call arguments (including the user's raw query) and would make the
    guard falsely believe a hallucinated law/article was retrieved by RAG.
    """
    fragments: list[str] = []
    seen: set[str] = set()

    for item in tool_results:
        result = item.get("result")
        if not isinstance(result, str):
            continue
        cleaned = result.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        fragments.append(cleaned)

    for source in candidate_sources:
        cleaned = source.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        fragments.append(cleaned)

    return fragments


def _compute_heuristic_confidence(state: dict[str, Any]) -> float | None:
    """
    Surgery #11: 当 reflector 未执行时，基于检索源数量和 payload 存在性估算 confidence。

    规则：
    - 0源 + 无 payload -> 0.35
    - 1-2 源 -> 0.55
    - 3+ 源 + 有 payload -> 0.78
    - 其他 -> 0.65

    若 confidence_score 已由 reflector 写入（>= 0.01），返回 None 表示不覆盖。
    """
    if float(state.get("confidence_score", 0.0) or 0.0) >= 0.01:
        return None

    rag_sources = state.get("rag_sources") or []
    source_count = len(rag_sources) if isinstance(rag_sources, list) else 0
    has_payload = bool(state.get("claim_payload"))

    if source_count == 0 and not has_payload:
        return 0.35
    if source_count <= 2:
        return 0.55
    if source_count >= 3 and has_payload:
        return 0.78
    return 0.65


def _prepend_fabricated_statute_notice(answer: str, question: str) -> str:
    detection = detect_fabricated_statute(question)
    if not detection.get("is_fabricated"):
        return answer.strip()

    law_name = detection.get("law_name", "")
    article_num = detection.get("article_num")
    max_articles = detection.get("max_articles")
    suggested_correction = detection.get("suggested_correction", "")
    notice = (
        f"⚠️ 您提到的\"《{law_name}》第{article_num}条\"在现行法律中并不存在"
        f"（《{law_name}》共有{max_articles}条）。{suggested_correction}"
    )
    clean_answer = answer.strip()
    if notice in clean_answer:
        return clean_answer
    if not clean_answer:
        return notice
    return f"{notice}\n\n{clean_answer}"


# ─────────────────────────────────────────────────────────────────────────────
# Prompt builder (pure function — testable without LLM)
# ─────────────────────────────────────────────────────────────────────────────

def build_advisor_system_prompt(
    case_summary: str,
    graph_context: str,
    rag_context: str,
    payload: PayloadResult,
    question: str = "",
    historical_reference: str = "",
    laws_block: str = "",
    cases_block: str = "",
    payload_facts: str = "",
    dossier_context: str = "",
    dossier_graph_summary: str = "",
    dossier_graph_context: str = "",
    question_mode: str = _DOSSIER_QUESTION_DEFAULT,
) -> str:
    """
    Build the full advisor system prompt.

    Extracted as a pure function so tests can assert on prompt content
    without invoking any LLM or async machinery.

    Surgery 7 additions:
    - <correction_notice priority="VERBATIM"> block when overclaim detected.
    - Legal-basis citation demotion guard (step.legal_basis with article
      numbers must be verified against rag_context before LLM uses them).

    Surgery 8 additions:
    - <amount_verification_notice priority="VERBATIM"> when the user quotes
      an amount the deterministic pipeline has not verified.
    - <statute_verification_notice priority="VERBATIM"> when the user cites
      a law/article absent from the retrieved RAG context.
    """
    # --- VERBATIM correction block (Surgery 7) ---
    correction_block = ""
    if payload.trace.correction_notices:
        notice_texts = "\n".join(
            f"【{n.item}纠错】{n.explanation}"
            for n in payload.trace.correction_notices
        )
        correction_block = f"""

<correction_notice priority="VERBATIM">
以下纠错说明由后端程序依据法定规则生成，数字经过精确计算，内容权威可靠。
你必须在回答的「算账与确权层」中原样转述以下内容，不得修改、删减、重新表述或重新计算：
{notice_texts}
转述后，继续完成其余层次的分析。
</correction_notice>"""

    # --- Legal basis citation demotion guard (Surgery 7) ---
    legal_basis_block = ""
    step_bases = [s.legal_basis for s in payload.trace.steps if s.legal_basis]
    if step_bases:
        legal_basis_block = """

<citation_demotion_guard>
计算轨迹中包含法律依据名称（如《劳动合同法》相关规定）。
规则：法律依据名称你可以直接使用；但如果你想补充具体条文编号（如"第82条"），
必须先确认该编号已在 <rag_context> 中原文出现。
若未出现，只写法律名称，不写条文编号。
</citation_demotion_guard>"""

    amount_verification_block = _build_amount_verification_block(question, payload)
    statute_verification_block = _build_statute_verification_block(question, rag_context)

    normalized_question_mode = _normalize_dossier_question_mode(question_mode)
    payload_facts = payload_facts or _extract_payload_facts(payload)
    laws_block = laws_block or "无"
    cases_block = cases_block or "无"
    rag_context = rag_context or "无"
    memory_only_mode = normalized_question_mode == _DOSSIER_QUESTION_MEMORY

    if memory_only_mode:
        laws_block = "当前问题属于案卷记忆 / 主体指代查询，本轮不强制使用法律检索结果。"
        cases_block = "当前问题属于案卷记忆 / 主体指代查询，本轮不强制使用案例材料。"
        rag_context = "当前问题属于案卷记忆 / 主体指代查询；如用户未追问法律依据，请忽略法律检索材料。"

    dossier_block = dossier_context.strip()
    dossier_graph_block = dossier_graph_summary.strip()
    dossier_graph_context_block = dossier_graph_context.strip()
    retrieval_relevance_guard = """

<retrieval_relevance_guard>
如果检索材料与当前问题或当前案型明显无关，例如劳动争议问题却检索到刑法、环境犯罪或其他不相干案例，不得为了“补充依据”而硬塞进回答。
与当前问题无关的法条、案例和其他 RAG 材料应直接忽略。
</retrieval_relevance_guard>"""
    if memory_only_mode:
        rag_binding_block = """

<rag_binding>
当前问题属于案卷记忆 / 主体指代 / 历史事实回顾。
你应优先依据 <dossier_context>、<dossier_graph_summary>、<dossier_graph_context> 回答，直接说明案卷中已记录或已确认的信息。
如果问题是在问“当事人 / 我 / 本人 / 这个公司 / 这个人指的是谁”，先直接说明主体是谁，再说明依据来自已确认主体映射或原始标签映射。
除非用户明确追问法律依据，否则不要主动展开法条解释、案例引用或其他法律检索内容。
</rag_binding>"""
        output_contract_block = """

<output_contract>
1. 只返回 JSON，格式：{"answer": "...", "sources": [...]}
2. answer：用律师对当事人讲话的口吻，不要出现任何“层”标签。
3. answer：当前问题若属于案卷记忆查询，应先直接回答“案卷里记录了什么 / 主体是谁 / 关键事实有哪些 / 证据有哪些”，必要时用条目列出。
4. 如果问题是主体指代问题，先回答主体名称，再用一句话说明依据来自案卷已确认主体或原始标签映射。
5. 如果案卷里还有未确认的相似主体，只能说明“尚未确认，不能自动视为同一主体”，不要擅自合并。
6. 除非用户明确追问法律依据或下一步怎么办，否则不要主动引用法条编号、案例名称，也不要展开法律 RAG 内容或维权建议。
7. answer：控制在 3 到 6 句话内，保持聚焦，不要展开大段法律分析。
8. 如果案卷信息不足，直接说明“当前案卷已记录的信息有限”，不要编造或补齐未确认事实。
9. sources：只能填写检索材料中真实出现过的来源，不能编造；如果本轮回答未实际使用法律检索来源，可返回空数组。
</output_contract>"""
    else:
        rag_binding_block = """

<rag_binding>
你必须优先依据【相关法条原文】和【参考案例】作答；<rag_context> 只作为补充背景材料。
若【相关法条原文】中出现了具体法律名称和条文编号，应自然引用到回答中。
若【参考案例】中存在与当前问题直接相关的案例名称和裁判要点，应在用户询问类案时加以说明。
</rag_binding>"""
        output_contract_block = """

<output_contract>
1. 只返回 JSON，格式：{"answer": "...", "sources": [...]}
2. answer：用律师对当事人讲话的口吻，不要出现任何“层”标签。
3. answer：必须明确引用法条编号，但仅限【相关法条原文】或 <rag_context> 中已出现的编号。
4. answer：当【参考案例】中存在与当前问题直接相关的案例时，应在回答中引用至少 1 个案件名简称（如“郑某诉霍尼韦尔案”“某途教育公司诉王某华案”），并用一句话总结裁判要点；如果检索到的案例与当前问题不相关，应明确忽略，不要为了满足格式硬塞进回答。
5. answer：结尾给出 1 到 2 个明确的下一步行动建议。
6. 如果用户提出的是具体任务型问题，例如“准备哪些证据”“下一步怎么办”“仲裁请求怎么写”“风险是什么”，必须先直接回答该问题，给出明确清单、步骤或判断，不要只说“我可以帮你整理”。
7. 只有当用户没有提出明确任务时，才可以使用“如果你愿意，我可以继续帮你整理成证据目录/仲裁申请书”这类补充性表达；该类表达不能替代直接回答。
8. sources：只能填写检索材料中真实出现过的来源，不能编造。
</output_contract>"""

    prompt = f"""
你不是普通的文本精校员，而是一位资深的中国法律咨询律师。请基于下面给定的事实、法条原文和参考案例，给出一段专业、有共情、可落地的回答。
{dossier_block}
{dossier_graph_block}
{dossier_graph_context_block}
{correction_block}
<number_lock>
【数字锁定 — 最高优先级，任何其他规则均不得推翻此条】
涉及金额时，必须沿用【已确认事实】中的计算结果，不得自行重算，不得改写金额，不得补充新的金额结论。
如需解释金额，只能解释其法律意义，不能重新推导公式或替换成其他数字。
</number_lock>

<citation_lock>
【引用锁定 — 次高优先级】
法条条文编号（"第X条"格式）只允许来自【相关法条原文】或 <rag_context> 中已经出现的内容。
任何其他情况：严禁补写任何"第X条"。
若你想引用某部法律，但材料中没有对应编号，只写法律名称，不写条文编号。
</citation_lock>
{legal_basis_block}
{amount_verification_block}
{statute_verification_block}
<amount_guard>
当用户主动提出具体赔偿金额而系统尚未核验时，你不得认同、背书或围绕该金额展开策略推演。
此时必须明确说明金额尚未核验，并引导用户补充月工资、工作时间、欠薪月数、赔偿项目等计算事实。
</amount_guard>

<statute_guard>
当用户引用具体法律名称或条文编号时，只有在【相关法条原文】或 <rag_context> 中检索到同一法律或条文后才可引用。
如果未检索到，必须明确告知知识库未检索到，并建议用户通过国家法律法规数据库等官方渠道查证。
严禁凭训练记忆回答用户提问的具体法条内容，严禁拿其他法律条文“代答”。
</statute_guard>

【硬约束】
1. 严禁引用未在【相关法条原文】中出现的法条编号或条文表述。
2. 涉及金额时，必须沿用【已确认事实】中的计算结果，不得自行重算。
3. 不得使用“算账与确权层”“高风险执行层”“下一步落地动作”这类内部分层标签。
4. 如果【相关法条原文】为空或为“无”，直接说明“暂未检索到精确法条原文，以下基于通用规则给出方向性建议”，禁止伪造条款号。
{retrieval_relevance_guard}
{rag_binding_block}

<dossier_graph_context_guard>
如果 system 中出现 dossier_graph_context，它只能作为当前案卷的结构化案情背景，不是法律依据。
你只能用“根据你当前案卷中已记录/已确认的信息……”这类表述引用它。
不得写“根据图谱可知法律规定”“根据图谱可以证明你一定胜诉”。
如果 graph_context 与用户本轮输入冲突，必须先指出冲突并澄清，不得直接把历史图谱当作已证实事实。
未经用户确认的主体和 relation 不得直接当作同一主体或已证实关系。
</dossier_graph_context_guard>

<grounded_context>
【已确认事实】
案件摘要：{case_summary or "无案件摘要"}
图谱上下文：{graph_context or "无图谱上下文"}
已确认金额与依据：
{payload_facts}

【相关法条原文】（来自向量检索 + 重排）
{laws_block}

【参考案例】（来自向量检索 + 重排）
{cases_block}

【其他参考材料】
<rag_context>
{rag_context}
</rag_context>

<historical_reference>
{historical_reference or "无历史案件参考"}
</historical_reference>
历史案件参考只可作为背景提示，严禁当作当前案件事实参与计算、抽取月薪、工龄或案型。
</grounded_context>

{output_contract_block}

<examples>
【示例：正确回答】
根据《劳动合同法》第八十七条，用人单位违法解除劳动合同的，应按经济补偿标准的二倍支付赔偿金。结合您已确认的月工资和工龄，现阶段可优先主张违法辞退赔偿金。下一步建议先固定解除通知、工资流水和考勤记录，再准备仲裁申请材料。

【示例：错误回答（被严格禁止）】
依据《劳动合同法》第82条和第87条，你肯定还能再多要三个月工资。
错误原因：① 材料中未出现的条文编号被擅自补写；② 擅自新增金额结论。

【示例：正确引用案例】
Input: “试用期违法辞退典型案例”
cases_block 第1条：“某途教育公司诉王某华劳动争议案 — 裁判要点：试用期超过法定上限，应就违法约定试用期承担赔偿责任。”
正确输出片段：“参考某途教育公司诉王某华案，法院认为试用期约定超过法定上限的，用人单位需承担相应赔偿责任；这类案例对判断试用期解除是否合法有直接参考价值。”
</examples>
""".strip()

    return prompt


# ─────────────────────────────────────────────────────────────────────────────
# Node function
# ─────────────────────────────────────────────────────────────────────────────

async def generate_final_answer_v3(self, state: AgentState) -> dict[str, Any]:
    tool_results = state.get("tool_results", [])
    candidate_sources = self._extract_candidate_sources(tool_results)
    split_rag = _split_rag_by_type(tool_results)
    laws_block = _format_laws_block(split_rag["laws"])
    cases_block = _format_cases_block(split_rag["cases"])
    rag_context = _format_other_rag_context(split_rag["others"])
    guard_rag_context = _build_guard_rag_context(laws_block, cases_block, rag_context)
    statute_guard_rag_sources = _build_statute_guard_rag_sources(tool_results, candidate_sources)
    case_summary = (state.get("case_summary") or "").strip()
    graph_context = (
        (state.get("graph_context") or "").strip()
        or (state.get("graph_memory") or "").strip()
    )
    historical_reference = (state.get("historical_reference") or "").strip()
    _cs_for_payload = CaseState.from_text(
        case_summary,
        state["question"],
        graph_context,
        historical_reference=historical_reference,
    )
    # Fix 2: 从 fact_updater 检测到的月薪补充（from_text 无法从首轮摘要解析时使用）
    if _cs_for_payload.monthly_salary is None:
        _wage = state.get("_detected_monthly_wage")
        if isinstance(_wage, (int, float)) and _wage > 0:
            _cs_for_payload.monthly_salary = float(_wage)
            logger.debug(
                "Fix2: monthly_salary补充自_detected_monthly_wage: %.0f",
                _cs_for_payload.monthly_salary,
            )
    layered_payload = build_claim_payload(_cs_for_payload)
    payload_facts = _extract_payload_facts(layered_payload)
    _wage_bridged = (
        _cs_for_payload.monthly_salary is not None
        and state.get("_detected_monthly_wage") is not None
        and str(int(_cs_for_payload.monthly_salary)) not in (case_summary or "")
    )
    display_case_summary = case_summary or ""
    if _wage_bridged:
        display_case_summary = (
            display_case_summary.strip()
            + f"\n- 已知月薪：{int(_cs_for_payload.monthly_salary)} 元（来自会话上下文）"
        ).strip()

    dossier_context_block = _format_dossier_context_block(state.get("dossier_context"))
    dossier_graph_summary_block = _format_dossier_graph_summary_block(
        state.get("dossier_graph_summary")
    )
    dossier_graph_context_block = _format_dossier_graph_context_block(
        state.get("dossier_graph_context")
    )
    dossier_question_mode = _classify_dossier_question_mode(
        question=state["question"],
        dossier_context=state.get("dossier_context"),
        dossier_graph_context=state.get("dossier_graph_context"),
    )

    advisor_system_prompt = build_advisor_system_prompt(
        question=state["question"],
        case_summary=display_case_summary,
        graph_context=graph_context,
        historical_reference=historical_reference,
        rag_context=rag_context,
        payload=layered_payload,
        laws_block=laws_block,
        cases_block=cases_block,
        payload_facts=payload_facts,
        dossier_context=dossier_context_block,
        dossier_graph_summary=dossier_graph_summary_block,
        dossier_graph_context=dossier_graph_context_block,
        question_mode=dossier_question_mode,
    )

    fallback = {"answer": layered_payload.answer, "sources": []}
    llm_router = self._get_llm_router()
    logger.info(
        "Advisor generation | requested_provider=%s requested_model=%s base_url=%s",
        LLMProvider.GPT_5_4.value,
        "gpt-5.4",
        getattr(llm_router, "openai_base_url", ""),
    )

    content = await self._chat_model(
        self.expert_llm,
        [
            {"role": "system", "content": advisor_system_prompt},
            {
                "role": "user",
                "content": _build_advisor_user_instruction(
                    state["question"],
                    dossier_question_mode,
                ),
            },
        ],
        provider=LLMProvider.GPT_5_4,
    )

    parsed = self._safe_json_load(content, fallback)
    raw_answer = parsed.get("answer") or parsed.get("response") or fallback["answer"]

    if not isinstance(raw_answer, str) or not raw_answer.strip():
        raw_answer = fallback["answer"]

    # ── Post-generation hallucination guard (Surgery 6+7) ─────────────────
    answer = sanitize_llm_answer(raw_answer, layered_payload, guard_rag_context)
    payload_for_claim_guard = layered_payload if _payload_has_authoritative_claims(layered_payload) else None
    answer = sanitize_unverified_claims(answer, state["question"], payload_for_claim_guard)
    answer = sanitize_hallucinated_statutes(
        answer,
        state["question"],
        statute_guard_rag_sources,
    )
    answer = _prepend_fabricated_statute_notice(answer, state["question"])

    heuristic_confidence = _compute_heuristic_confidence(
        {
            "confidence_score": state.get("confidence_score", 0.0),
            "rag_sources": candidate_sources,
            "claim_payload": layered_payload if _payload_has_authoritative_claims(layered_payload) else None,
        }
    )
    confidence_score = (
        float(heuristic_confidence)
        if heuristic_confidence is not None
        else float(state.get("confidence_score", 0.0))
    )

    sources = self._normalize_sources(parsed.get("sources", []), candidate_sources)
    return {
        "response": answer.strip(),
        "sources": sources,
        "confidence_score": confidence_score,
        "is_final": True,
    }
