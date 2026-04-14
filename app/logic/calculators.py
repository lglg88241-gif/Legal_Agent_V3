"""
Pure-Python claim calculation and layered answer builder.

Every function in this module is deterministic — no LLM, no IO,
no network. Input is a CaseState; output is a structured PayloadResult.

The LLM's only job downstream is to polish the prose and weave in
RAG-retrieved citations. It must NEVER override the numbers produced here.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.case_state import CaseState


# ======================================================================
# Output contract
# ======================================================================

class PayloadResult(BaseModel):
    """Structured output of the deterministic claim analysis pipeline."""

    answer: str = Field(description="Full layered answer text")
    needs_core_followup: bool = Field(description="True if missing critical fields")
    followup: str = Field(default="", description="Sandwich-style follow-up question")
    constructive_next_step: str = Field(default="", description="Proactive next action proposal")
    unknowns: list[str] = Field(default_factory=list, description="Missing core fields (max 2)")


# ======================================================================
# Text utilities (no IO)
# ======================================================================

def _normalize_text(text: str) -> str:
    return " ".join((text or "").strip().split())


def _split_fact_lines(case_summary: str, question: str, graph_context: str) -> list[str]:
    """Extract up to 8 distinct fact sentences from raw text inputs."""
    facts: list[str] = []
    for raw in (case_summary, question, graph_context):
        clean = _normalize_text(raw)
        if not clean:
            continue
        for part in re.split(r"[；;。！？\n]+", clean):
            item = part.strip(" ，,")
            if not item or item in facts:
                continue
            facts.append(item)
    return facts[:8]


def _merged_text(cs: CaseState) -> str:
    return " ".join(
        p for p in [cs.case_summary, cs.question, cs.graph_context] if p
    ).strip()


# ======================================================================
# Keyword detectors (pure, no IO)
# ======================================================================

_ENTITY_CHANGE_TOKENS = (
    "查封", "保全", "资产", "新公司", "人格混同", "关联公司",
    "主体变更", "更名", "注销", "换皮", "关门", "搬走", "换了个名",
)

_OWED_TOKENS = ("拖欠工资", "欠薪", "未发工资", "克扣工资", "拖欠")

_DISMISSAL_TOKENS = ("辞退", "开除", "解除劳动关系", "逼离职", "被迫离职")

_ASSET_TOKENS = ("新公司", "注销", "关联公司", "查封", "保全", "换皮", "关门", "搬走")

_LABOR_RELATION_TOKENS = ("劳动关系", "入职", "考勤", "工资流水", "发工资")


def _has_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(tok in text for tok in tokens)


# ======================================================================
# Core builder
# ======================================================================

def build_claim_payload(cs: CaseState) -> PayloadResult:
    """
    Build the full layered answer from a CaseState.

    This is the authoritative pure-Python replacement for the legacy
    module-level functions in legal_agent.py (Surgery 5).
    It produces structured PayloadResult output consumed by all nodes.
    """
    merged = _merged_text(cs)
    facts = _split_fact_lines(cs.case_summary, cs.question, cs.graph_context)

    calculation_lines: list[str] = []
    strategy_lines: list[str] = []
    action_lines: list[str] = []
    unknowns: list[str] = []

    # --- Unsigned contract / double salary ---
    if cs.has_unsigned_contract:
        calculation_lines.append(
            "只要已经形成实际用工关系，即使没有书面劳动合同，"
            "也可以先主张未签书面劳动合同的二倍工资差额。"
        )
        if cs.monthly_salary is not None and cs.work_months is not None:
            calculation_lines.append(
                f"按现有信息，计算公式可以先写为：月工资 {cs.monthly_salary:.0f} 元"
                f" × 可主张月数 {cs.compensable_months}"
                f" = 二倍工资差额约 {cs.double_salary_gap:.0f} 元。"
            )
        else:
            if cs.monthly_salary is None:
                unknowns.append("月工资标准")
            if cs.work_months is None:
                unknowns.append("未签合同持续月数")

    # --- Owed wages ---
    if cs.owed_wages > 0:
        calculation_lines.append(f"拖欠工资部分：约 {cs.owed_wages:.0f} 元。")
    elif cs.has_owed_wages and cs.monthly_salary is not None:
        calculation_lines.append("存在工资拖欠事实，待确认具体欠薪月数后可精确计算。")

    # --- Deposit ---
    if cs.deposit > 0:
        calculation_lines.append(
            f"押金/保证金部分：{cs.deposit:.0f} 元，"
            "用人单位收取押金本身违反《劳动合同法》第九条，可依法要求全额退还。"
        )
    elif cs.has_deposit_dispute:
        calculation_lines.append(
            "存在押金争议，用人单位向劳动者收取押金违反《劳动合同法》第九条，可要求退还。"
        )

    # --- Claim total ---
    if cs.claim_total > 0:
        parts: list[str] = []
        if cs.double_salary_gap > 0:
            parts.append(f"二倍工资差额 {cs.double_salary_gap:.0f} 元")
        if cs.owed_wages > 0:
            parts.append(f"拖欠工资 {cs.owed_wages:.0f} 元")
        if cs.deposit > 0:
            parts.append(f"押金退还 {cs.deposit:.0f} 元")
        calculation_lines.append(
            f"以上各项合计可主张总额约 {cs.claim_total:.0f} 元"
            f"（{' + '.join(parts)}）。"
        )

    # --- Labor relationship ---
    if _has_any(merged, _LABOR_RELATION_TOKENS):
        calculation_lines.append(
            "现有事实已经足以先从劳动关系成立、工资支付事实和未签约责任三个层面同时主张权利。"
        )

    # --- Strategy layer ---
    if cs.has_entity_change or _has_any(merged, _ENTITY_CHANGE_TOKENS):
        strategy_lines.append(
            "如果目标是查封新公司或关联主体，现阶段不要先卡在\u201c证据够不够\u201d"
            "这个问题上，而是先按人格混同、责任承继、实际用工主体三个方向布置材料。"
        )
        strategy_lines.append(
            "下一步应优先准备：工商档案、股东和法定代表人信息、工资发放账户、"
            "社保公积金缴纳主体、办公地点、业务连续性、聊天记录和财务往来材料。"
        )

    if not strategy_lines:
        strategy_lines.append(
            "高风险诉求可以先拆成两步走：先固定债权和责任主体，"
            "再根据对方资产和主体结构决定是否申请保全、追加被申请人或单独起诉。"
        )

    # --- Action layer ---
    if cs.has_unsigned_contract:
        action_lines.append(
            "先把入职时间、工资标准、考勤记录、工资流水、聊天记录整理成时间轴，"
            "这些足以支撑第一轮仲裁请求。"
        )
    if _has_any(merged, _OWED_TOKENS):
        action_lines.append(
            "欠薪部分建议同步准备工资条、银行流水、催要记录和考勤，"
            "便于把工资、经济补偿或赔偿一并主张。"
        )
    if _has_any(merged, _DISMISSAL_TOKENS):
        action_lines.append(
            "如果还涉及辞退或被迫离职，解除通知、谈话录音、"
            "岗位调整记录和绩效材料要单独归档。"
        )
    if _has_any(merged, _ASSET_TOKENS):
        action_lines.append(
            "如果担心对方转移资产，可在仲裁或起诉前同步申请财产保全，"
            "而不是等全部证据齐全后再行动。"
        )
    if not action_lines:
        action_lines.append(
            "先把可用证据按\u201c身份关系、金额、时间、对方主体\u201d四类归档，"
            "后续维权路径就能快速落地。"
        )

    # --- Follow-up decision ---
    needs_core_followup = False
    if cs.has_unsigned_contract and (cs.monthly_salary is None or cs.work_months is None):
        needs_core_followup = True
    elif not (cs.case_summary or "").strip():
        needs_core_followup = True

    if unknowns:
        sandwich_followup = (
            "你现在已经提供了有法律价值的关键事实，这些信息足以先帮你判断维权方向。"
            "按目前情况看，权利主张是有空间的，不需要因为材料还没凑齐就停在原地。"
            f"为了把金额和请求做得更准，我还需要你补 1 到 2 个核心信息：{'、'.join(unknowns[:2])}。"
        )
    else:
        sandwich_followup = (
            "你现有的信息已经能支持初步维权判断了。"
            "按现在的案情，主张路径已经比较清楚。"
            "如果你愿意，我接下来更建议直接帮你整理仲裁请求清单、证据目录，"
            "或者起草一份劳动仲裁申请书草稿。"
        )

    constructive_next_step = (
        "目前账目和维权方向已经基本清楚。接下来你可以选择先去劳动监察投诉，"
        "或者直接申请劳动仲裁。"
        "如果你愿意，我下一步可以直接帮你起草《劳动仲裁申请书》草稿，"
        "或者把证据目录整理成可提交版本。"
    )

    # --- Assemble answer ---
    answer_parts: list[str] = []
    if facts:
        answer_parts.append("已知事实：\n- " + "\n- ".join(facts))
    if calculation_lines:
        answer_parts.append("算账与确权层：\n- " + "\n- ".join(calculation_lines))
    else:
        answer_parts.append(
            "算账与确权层：\n- 现有信息虽然还不完整，"
            "但已经足以先明确可主张的权利方向，不需要整体停住。"
        )
    answer_parts.append("高风险执行层：\n- " + "\n- ".join(strategy_lines))
    answer_parts.append("下一步落地动作：\n- " + "\n- ".join(action_lines))
    if needs_core_followup:
        answer_parts.append("柔性追问：\n- " + sandwich_followup)
    else:
        answer_parts.append("建设性推进：\n- " + constructive_next_step)

    return PayloadResult(
        answer="\n\n".join(answer_parts),
        needs_core_followup=needs_core_followup,
        followup=sandwich_followup,
        constructive_next_step=constructive_next_step,
        unknowns=unknowns[:2],
    )
