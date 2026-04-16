"""
Strong-typed domain model for a legal case's quantifiable facts.

CaseState centralizes all deterministic extraction (salary, time span,
owed wages, deposit) into a single validated Pydantic object.  Every
downstream consumer (payload builder, advisor prompt, reflector) should
read numbers from this model instead of re-running regex extraction.

Usage:
    state = CaseState.from_text(case_summary, question, graph_context)
    state.monthly_salary   # float | None
    state.claim_total      # float  (always >= 0)
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, model_validator


class CaseState(BaseModel):
    """Immutable snapshot of all quantifiable facts in a legal case."""

    # --- Raw text inputs (kept for traceability) ---
    case_summary: str = ""
    question: str = ""
    graph_context: str = ""

    # --- Extracted numeric fields ---
    monthly_salary: float | None = Field(default=None, ge=0, description="月工资/月薪 (元)")
    work_months: int | None = Field(default=None, ge=0, description="工作月数")
    compensable_months: int = Field(default=0, ge=0, description="可主张二倍工资月数 (max 11)")
    double_salary_gap: float = Field(default=0.0, ge=0, description="二倍工资差额 (元)")
    owed_wages: float = Field(default=0.0, ge=0, description="拖欠工资 (元)")
    deposit: float = Field(default=0.0, ge=0, description="押金/保证金 (元)")
    claim_total: float = Field(default=0.0, ge=0, description="可主张总额 (元)")

    # --- Semantic flags ---
    has_unsigned_contract: bool = False
    has_owed_wages: bool = False
    has_deposit_dispute: bool = False
    has_entity_change: bool = False

    @model_validator(mode="after")
    def _recompute_derived(self) -> "CaseState":
        """Ensure derived fields are consistent with base fields."""
        self.owed_wages = round(self.owed_wages, 2)
        self.deposit = round(self.deposit, 2)
        if self.monthly_salary is not None and self.work_months is not None:
            self.compensable_months = max(min(self.work_months - 1, 11), 0)
            if self.has_unsigned_contract:
                self.double_salary_gap = round(self.monthly_salary * self.compensable_months, 2)
        self.double_salary_gap = round(self.double_salary_gap, 2)
        self.claim_total = round(self.double_salary_gap + self.owed_wages + self.deposit, 2)
        return self

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------
    @classmethod
    def from_text(
        cls,
        case_summary: str = "",
        question: str = "",
        graph_context: str = "",
    ) -> "CaseState":
        """Build a CaseState by running all extractors on merged text.

        None inputs are silently coerced to empty strings so callers
        that pass state.get(key) directly (which may return None) are
        safe without additional guards on the call site.
        """
        case_summary = case_summary or ""
        question = question or ""
        graph_context = graph_context or ""
        if not any((case_summary.strip(), question.strip(), graph_context.strip())):
            return cls()
        merged = " ".join(
            part for part in [case_summary, question, graph_context] if part
        ).strip()

        salary = _extract_salary(merged)
        months = _extract_months(merged)
        owed = _extract_owed_wages(merged, salary)
        dep = _extract_deposit(merged)

        unsigned = any(
            tok in merged
            for tok in ("未签合同", "没签合同", "没签劳动合同", "未签劳动合同", "没有劳动合同")
        )
        has_owed = any(
            tok in merged
            for tok in ("拖欠", "欠薪", "未发工资", "克扣工资", "欠发")
        )
        has_deposit = any(tok in merged for tok in ("押金", "保证金"))
        has_entity = any(
            tok in merged
            for tok in (
                "换皮", "关门", "搬走", "换了个名",
                "新公司", "注销", "关联公司", "主体变更", "更名",
                "查封", "保全", "资产", "人格混同",
            )
        )

        return cls(
            case_summary=case_summary,
            question=question,
            graph_context=graph_context,
            monthly_salary=salary,
            work_months=months,
            has_unsigned_contract=unsigned,
            has_owed_wages=has_owed,
            has_deposit_dispute=has_deposit,
            has_entity_change=has_entity,
            owed_wages=owed,
            deposit=dep,
        )


# ======================================================================
# Pure extraction functions (no LLM, no IO)
# ======================================================================

def _extract_salary(text: str) -> float | None:
    """Extract monthly salary from text. Supports both '8000元工资' and '月薪8000'."""
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:元|块|人民币|月薪|工资)", text)
    if not match:
        match = re.search(r"(?:月薪|工资|底薪|薪资|薪水)\s*(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _extract_months(text: str) -> int | None:
    """Extract work duration in months. Supports '去年N月...今年M月' and 'N个月'."""
    relative_span = re.search(
        r"去年\s*(\d{1,2})\s*月.*?今年\s*(\d{1,2})\s*月(?:底|末)?",
        text,
    )
    if relative_span:
        try:
            start_month = int(relative_span.group(1))
            end_month = int(relative_span.group(2))
            if 1 <= start_month <= 12 and 1 <= end_month <= 12:
                months = (12 - start_month + 1) + end_month
                return months if months > 0 else None
        except ValueError:
            pass

    worked_match = re.search(r"(?:工作|任职|入职后|用工)\D{0,6}(\d+)\s*个月", text)
    if worked_match:
        try:
            return max(int(worked_match.group(1)), 0)
        except ValueError:
            pass

    month_match = re.search(r"(\d+)\s*个月(?!工资|薪资|薪水|工资的|薪的)", text)
    if month_match:
        try:
            return max(int(month_match.group(1)), 0)
        except ValueError:
            pass

    return None


_CN_DIGIT_MAP = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _extract_owed_wages(text: str, monthly_salary: float | None = None) -> float:
    """Extract owed wages amount. Supports '拖欠N月工资' with Chinese digits."""
    direct = re.search(
        r"(?:欠薪|拖欠工资|欠发工资)\s*(\d+(?:\.\d+)?)\s*(?:元|块|万)?", text
    )
    if direct:
        val = float(direct.group(1))
        if "万" in text[direct.start():direct.end() + 2]:
            val *= 10000
        return val

    month_match = re.search(
        r"(?:拖欠|欠)[^\d一二三四五六七八九十两]{0,3}"
        r"(\d+|[一二三四五六七八九十两]+)\s*(?:个?月|个月的?)\s*(?:工资|薪)?",
        text,
    )
    if not month_match:
        month_match = re.search(
            r"(?:拖欠|欠)\s*([一二三四五六七八九十两]+|[0-9]+)\s*月\s*(?:工资|薪)?",
            text,
        )
    if month_match and monthly_salary:
        raw = month_match.group(1)
        n = _CN_DIGIT_MAP.get(raw)
        if n is None:
            try:
                n = int(raw)
            except ValueError:
                return 0.0
        return monthly_salary * n

    return 0.0


def _extract_deposit(text: str) -> float:
    """Extract deposit/guarantee amount. Supports 'N万押金', 'N元押金', etc."""
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:万|万元)\s*(?:押金|保证金)", text)
    if match:
        return float(match.group(1)) * 10000
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:元|块)\s*(?:押金|保证金)", text)
    if match:
        return float(match.group(1))
    match = re.search(r"(?:押金|保证金)\s*(\d+(?:\.\d+)?)\s*(?:万|万元)", text)
    if match:
        return float(match.group(1)) * 10000
    match = re.search(r"(?:押金|保证金)\s*(\d+(?:\.\d+)?)\s*(?:元|块)?", text)
    if match:
        return float(match.group(1))
    return 0.0
