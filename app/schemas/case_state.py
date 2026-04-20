"""
Strong-typed domain model for a legal case's quantifiable facts.

CaseState centralizes deterministic extraction (salary, duration,
owed wages, dismissal facts, deposit) into a single validated model.
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field, model_validator


class CaseState(BaseModel):
    """Immutable snapshot of all quantifiable facts in a legal case."""

    # Raw text inputs
    case_summary: str = ""
    question: str = ""
    graph_context: str = ""
    historical_reference: str = ""

    # Extracted numeric fields
    monthly_salary: float | None = Field(default=None, ge=0, description="月工资/月薪（元）")
    work_months: int | None = Field(default=None, ge=0, description="工作月数")
    service_years: float | None = Field(default=None, ge=0, le=50, description="工作年限")
    monthly_salary_source: str = Field(default="current", description="月薪来源：current/historical")
    work_months_source: str = Field(default="current", description="工作月数来源：current/historical")
    service_years_source: str = Field(default="current", description="工作年限来源：current/historical")
    compensable_months: int = Field(default=0, ge=0, description="可主张二倍工资月数（max 11）")
    double_salary_gap: float = Field(default=0.0, ge=0, description="二倍工资差额（元）")
    owed_wages: float | None = Field(default=0.0, ge=0, description="拖欠工资（元）")
    owed_months: int | None = Field(default=None, ge=0, le=120, description="拖欠工资月数")
    deposit: float = Field(default=0.0, ge=0, description="押金/保证金（元）")
    claim_total: float = Field(default=0.0, ge=0, description="可主张总额（元）")

    # Semantic flags
    has_unsigned_contract: bool = False
    has_owed_wages: bool = False
    has_deposit_dispute: bool = False
    has_entity_change: bool = False
    is_illegal_dismissal: bool = Field(default=False, description="是否违法辞退")
    no_30day_notice: bool = Field(default=False, description="是否未提前30天通知")

    @property
    def n_value(self) -> float:
        """Calculate N based on service years."""
        if self.service_years is None:
            return 0.0
        full_years = int(self.service_years)
        remainder = self.service_years - full_years
        if remainder == 0:
            return float(full_years)
        if remainder <= 0.5:
            return full_years + 0.5
        return full_years + 1.0

    @property
    def illegal_dismissal_compensation(self) -> float:
        """Illegal dismissal compensation = 2N * monthly salary."""
        if (
            not self.is_illegal_dismissal
            or self.monthly_salary is None
            or self.service_years is None
        ):
            return 0.0
        return round(self.monthly_salary * self.n_value * 2, 2)

    @property
    def notice_substitution_pay(self) -> float:
        """Payment in lieu of notice = one month salary."""
        if not self.no_30day_notice or self.monthly_salary is None:
            return 0.0
        return round(self.monthly_salary, 2)

    @model_validator(mode="after")
    def _recompute_derived(self) -> "CaseState":
        """Ensure derived fields remain coherent."""
        if self.owed_wages is not None:
            self.owed_wages = round(self.owed_wages, 2)
        self.deposit = round(self.deposit, 2)

        if self.monthly_salary is not None and self.work_months is not None:
            self.compensable_months = max(min(self.work_months - 1, 11), 0)
            if self.has_unsigned_contract:
                self.double_salary_gap = round(
                    self.monthly_salary * self.compensable_months, 2
                )

        self.double_salary_gap = round(self.double_salary_gap, 2)

        effective_owed_wages = 0.0
        if self.owed_wages is not None:
            effective_owed_wages = self.owed_wages
        elif self.owed_months is not None and self.monthly_salary is not None:
            effective_owed_wages = round(self.monthly_salary * self.owed_months, 2)

        self.claim_total = round(
            self.double_salary_gap
            + effective_owed_wages
            + self.deposit
            + self.illegal_dismissal_compensation
            + self.notice_substitution_pay,
            2,
        )
        return self

    @classmethod
    def from_text(
        cls,
        case_summary: str = "",
        question: str = "",
        graph_context: str = "",
        historical_reference: str = "",
    ) -> "CaseState":
        """Build a CaseState by running all extractors on merged text."""
        case_summary = case_summary or ""
        question = question or ""
        graph_context = graph_context or ""
        historical_reference = historical_reference or ""
        if not any((case_summary.strip(), question.strip(), graph_context.strip(), historical_reference.strip())):
            return cls()

        current_merged = " ".join(
            part for part in [case_summary, question, graph_context] if part
        ).strip()
        historical_merged = historical_reference.strip()

        salary = _extract_salary(current_merged)
        salary_source = "current"
        if salary is None and historical_merged:
            salary = _extract_salary(historical_merged)
            if salary is not None:
                salary_source = "historical"

        months = _extract_months(current_merged)
        months_source = "current"
        if months is None and historical_merged:
            months = _extract_months(historical_merged)
            if months is not None:
                months_source = "historical"

        service_years = _extract_service_years(current_merged)
        service_years_source = "current"
        if service_years is None and historical_merged:
            service_years = _extract_service_years(historical_merged)
            if service_years is not None:
                service_years_source = "historical"

        salary_for_current_calc = salary if salary_source == "current" else None
        owed_months = _extract_owed_months(current_merged)
        owed = None if owed_months is not None else _extract_owed_wages(current_merged, salary_for_current_calc)
        dep = _extract_deposit(current_merged)

        unsigned = any(
            tok in current_merged
            for tok in ("未签合同", "没签合同", "没签劳动合同", "未签劳动合同", "没有劳动合同")
        )
        has_owed = any(
            tok in current_merged for tok in ("拖欠", "欠薪", "未发工资", "克扣工资", "欠发")
        )
        has_deposit = any(tok in current_merged for tok in ("押金", "保证金"))
        has_entity = any(
            tok in current_merged
            for tok in (
                "换皮",
                "关门",
                "搬走",
                "换了个名",
                "新公司",
                "注销",
                "关联公司",
                "主体变更",
                "更名",
                "查封",
                "保全",
                "资产",
                "人格混同",
            )
        )
        is_illegal_dismissal = any(
            tok in current_merged
            for tok in (
                "违法辞退",
                "违法解除",
                "无故辞退",
                "口头辞退",
                "突然辞退",
                "没理由开除",
                "口头通知",
                "不用来了",
                "没有任何书面文件",
            )
        )
        no_30day_notice = any(
            tok in current_merged
            for tok in ("没有提前30天通知", "未提前30天", "没提前一个月")
        )

        return cls(
            case_summary=case_summary,
            question=question,
            graph_context=graph_context,
            historical_reference=historical_reference,
            monthly_salary=salary,
            monthly_salary_source=salary_source,
            work_months=months,
            work_months_source=months_source,
            service_years=service_years,
            service_years_source=service_years_source,
            has_unsigned_contract=unsigned,
            has_owed_wages=has_owed,
            has_deposit_dispute=has_deposit,
            has_entity_change=has_entity,
            is_illegal_dismissal=is_illegal_dismissal,
            no_30day_notice=no_30day_notice,
            owed_wages=owed,
            owed_months=owed_months,
            deposit=dep,
        )


def _extract_salary(text: str) -> float | None:
    """Extract monthly salary from text."""
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:元|块|人民币)?\s*(?:月薪|月工资|工资|薪资|薪水)",
        text,
    )
    if not match:
        match = re.search(
            r"(?:月薪|月工资|底薪|薪资|薪水|工资标准|工资为|工资是|工资)\s*"
            r"(\d+(?:\.\d+)?)(?!\s*个?月)",
            text,
        )
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _extract_months(text: str) -> int | None:
    """Extract work duration in months."""
    relative_span = re.search(r"去年\s*(\d{1,2})\s*月.*?今年\s*(\d{1,2})\s*月(?:底)?", text)
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

    for month_match in re.finditer(r"(\d+)\s*个月(?!工资|薪资|薪水|工资的|薪的)", text):
        prefix = text[max(0, month_match.start() - 8):month_match.start()]
        if any(token in prefix for token in ("拖欠", "欠薪", "欠发")):
            continue
        try:
            return max(int(month_match.group(1)), 0)
        except ValueError:
            pass

    return None


def _extract_service_years(text: str) -> float | None:
    """Extract service years from text."""
    patterns = (
        r"工作了?(?:整整|大约|约|近)?\s*(\d+(?:\.\d+)?)\s*年",
        r"工作\s*(\d+(?:\.\d+)?)\s*年",
        r"入职满\s*(\d+(?:\.\d+)?)\s*年",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        try:
            return float(match.group(1))
        except ValueError:
            continue
    return None


def _parse_cn_int(raw: str) -> int | None:
    """Parse small Chinese numerals such as 两、十一、三百二十五."""
    if not raw:
        return None
    stripped = raw.strip()
    if stripped.isdigit():
        return int(stripped)

    digits = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    units = {"十": 10, "百": 100, "千": 1000}

    total = 0
    current = 0
    has_token = False
    for char in stripped:
        if char in digits:
            current = digits[char]
            has_token = True
            continue
        if char in units:
            total += (current or 1) * units[char]
            current = 0
            has_token = True
            continue
        return None

    if not has_token:
        return None
    return total + current


def _extract_owed_months(text: str) -> int | None:
    """Extract owed wage months from text."""
    patterns = (
        r"拖欠工资\s*([0-9一二两三四五六七八九十百千零〇]+)\s*个?月",
        r"(?:拖欠|欠薪|欠发工资)[^0-9一二两三四五六七八九十百千零〇]{0,3}"
        r"([0-9一二两三四五六七八九十百千零〇]+)\s*(?:个月|月)\s*(?:工资|薪资|薪水)?",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        parsed = _parse_cn_int(match.group(1))
        if parsed is not None:
            return parsed
    return None


def _extract_owed_wages(text: str, monthly_salary: float | None = None) -> float | None:
    """
    Extract explicit owed wage amount.

    For backward compatibility, if the text only states owed months and a
    monthly salary is supplied, this helper can still derive the amount.
    CaseState.from_text deliberately does not use that derived amount when
    owed_months is present; it keeps owed_wages=None and defers to the
    calculator layer.
    """
    direct = re.search(
        r"(?:拖欠|欠薪|欠发)[^0-9一二两三四五六七八九十百千零〇]{0,6}"
        r"(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*(万元|万|元|块)",
        text,
    )
    if direct:
        value = float(direct.group(1).replace(",", "").replace("，", ""))
        if direct.group(2) in ("万元", "万"):
            value *= 10000
        return value

    owed_months = _extract_owed_months(text)
    if owed_months is not None and monthly_salary is not None:
        return monthly_salary * owed_months

    return None


def _extract_deposit(text: str) -> float:
    """Extract deposit/guarantee amount."""
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:万元|万)\s*(?:押金|保证金)", text)
    if match:
        return float(match.group(1)) * 10000
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:元|块)\s*(?:押金|保证金)", text)
    if match:
        return float(match.group(1))
    match = re.search(r"(?:押金|保证金)\s*(\d+(?:\.\d+)?)\s*(?:万元|万)", text)
    if match:
        return float(match.group(1)) * 10000
    match = re.search(r"(?:押金|保证金)\s*(\d+(?:\.\d+)?)\s*(?:元|块)", text)
    if match:
        return float(match.group(1))
    return 0.0
