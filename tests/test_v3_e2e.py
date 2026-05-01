"""
Phase-1 baseline snapshot test for core-surgery-v1.

This test locks down the deterministic calculation pipeline
(extractors + payload builder) against a real-world compound
labor dispute scenario. All assertions must pass GREEN before
any refactoring begins.

Scenario:
    Worker entered Shenzhen WangPu Hardware Factory last September,
    boss Li Si never signed a contract. Worked until end of March
    this year when the factory closed. Monthly salary 8000 RMB.
    Two months' wages owed plus 10k deposit. Machines moved next
    door, rebranded as WangPu Tech and kept operating.

Hard assertions:
    - Double salary gap: 48000 (8000 * 6 compensable months)
    - Owed wages: 16000 (8000 * 2 months)
    - Deposit refund: 10000
    - Total claim: 74000
    - Output mentions key legal concepts and actionable next steps
    - Output does NOT aggressively demand new entity information
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.schemas.case_state import CaseState, _extract_salary, _extract_months, _extract_owed_wages, _extract_deposit
from app.logic.calculators import PayloadResult, build_claim_payload


# ---------------------------------------------------------------------------
# Canonical test input
# ---------------------------------------------------------------------------
CASE_SUMMARY = (
    "去年9月进深圳旺铺五金厂，老板李四没签合同。"
    "干到今年3月底厂子关门，月薪8000。"
    "拖欠两月工资和1万押金。"
    "机器搬隔壁村换皮旺铺科技营业。"
)
QUESTION = "怎么维权？能赔多少？"
GRAPH_CONTEXT = ""


# ===================================================================
# 1. Extractor unit tests — lock down numeric extraction precision
# ===================================================================

class TestExtractors:

    def test_salary_extraction_with_prefix_pattern(self) -> None:
        """月薪8000 (number AFTER unit) must be extracted."""
        assert _extract_salary("月薪8000") == 8000.0

    def test_salary_extraction_with_suffix_pattern(self) -> None:
        """8000元工资 (number BEFORE unit) must be extracted."""
        assert _extract_salary("8000元工资") == 8000.0

    def test_salary_extraction_with_yuan(self) -> None:
        """月薪8000元 must be extracted."""
        assert _extract_salary("月薪8000元") == 8000.0

    def test_salary_extraction_from_case(self) -> None:
        """Full case text must yield 8000."""
        assert _extract_salary(CASE_SUMMARY) == 8000.0

    def test_month_span_relative_time(self) -> None:
        """去年9月 to 今年3月 = 7 months."""
        assert _extract_months(CASE_SUMMARY) == 7

    def test_owed_wages_two_months(self) -> None:
        """拖欠两月工资 with salary 8000 = 16000."""
        assert _extract_owed_wages(CASE_SUMMARY, 8000.0) == 16000.0

    def test_deposit_ten_thousand(self) -> None:
        """1万押金 = 10000."""
        assert _extract_deposit(CASE_SUMMARY) == 10000.0


# ===================================================================
# 2. Payload builder integration test — the core assertion battery
# ===================================================================

class TestPayloadBuilder:

    def setup_method(self) -> None:
        cs = CaseState.from_text(CASE_SUMMARY, QUESTION, GRAPH_CONTEXT)
        self.result = build_claim_payload(cs)
        self.answer = self.result.answer

    # --- Numeric precision ---

    def test_double_salary_gap_48000(self) -> None:
        """Binary assertion: answer must contain '48000'."""
        assert "48000" in self.answer, (
            f"Expected '48000' (double salary gap) in answer but not found.\n"
            f"Answer snippet: {self.answer[:300]}"
        )

    def test_owed_wages_16000(self) -> None:
        """Binary assertion: answer must contain '16000'."""
        assert "16000" in self.answer, (
            f"Expected '16000' (owed wages) in answer but not found.\n"
            f"Answer snippet: {self.answer[:300]}"
        )

    def test_deposit_10000(self) -> None:
        """Binary assertion: answer must contain '10000'."""
        assert "10000" in self.answer, (
            f"Expected '10000' (deposit) in answer but not found.\n"
            f"Answer snippet: {self.answer[:300]}"
        )

    def test_total_claim_74000(self) -> None:
        """Binary assertion: answer must contain '74000'."""
        assert "74000" in self.answer, (
            f"Expected '74000' (total claim) in answer but not found.\n"
            f"Answer snippet: {self.answer[:300]}"
        )

    # --- Legal concept coverage ---

    def test_mentions_double_salary(self) -> None:
        assert "二倍工资差额" in self.answer

    def test_mentions_labor_contract_law(self) -> None:
        """Must reference Labor Contract Law (劳动合同法)."""
        assert "劳动合同法" in self.answer

    def test_mentions_entity_piercing(self) -> None:
        """Must mention corporate veil piercing / personality confusion."""
        assert "人格混同" in self.answer

    # --- Actionable next steps (not cold interrogation) ---

    def test_mentions_arbitration_or_evidence(self) -> None:
        """Must propose arbitration application or evidence checklist."""
        has_arbitration = "仲裁" in self.answer
        has_evidence = "证据" in self.answer
        assert has_arbitration or has_evidence, (
            "Answer must mention arbitration (仲裁) or evidence (证据) as next step"
        )

    def test_does_not_aggressively_demand_new_entity_info(self) -> None:
        """Must NOT coldly demand new legal entity details."""
        assert self.result.needs_core_followup is False, (
            "With salary, months, owed wages, and deposit all known, "
            "the system should NOT request more core fields."
        )

    def test_proposes_constructive_next_step(self) -> None:
        """When all core fields present, propose drafting documents."""
        next_step = self.result.constructive_next_step
        has_draft = any(
            keyword in next_step
            for keyword in ("仲裁申请书", "证据目录", "请求清单")
        )
        assert has_draft, (
            f"constructive_next_step should mention document drafting.\n"
            f"Got: {next_step}"
        )

    # --- Structural integrity ---

    def test_answer_has_known_facts_section(self) -> None:
        assert "已知事实" in self.answer

    def test_answer_has_calculation_section(self) -> None:
        assert "算账与确权" in self.answer or "确权" in self.answer

    def test_answer_has_enforcement_section(self) -> None:
        assert "高风险执行" in self.answer or "执行" in self.answer

    def test_answer_has_action_section(self) -> None:
        assert "下一步" in self.answer


# ===================================================================
# 3. Regression guards — existing tests must not break
# ===================================================================

class TestExistingRegressions:
    """
    Re-verify the two critical regressions to confirm the source
    edits didn't break them.
    """

    def test_advisor_fallback_48000_labor(self) -> None:
        result = build_claim_payload(
            CaseState.from_text(
                "员工于2024年1月入职，月工资12000元，工作了5个月，公司一直未签劳动合同，后公司准备注销。",
                "公司没签合同我能要什么赔偿？",
                "",
            )
        )
        assert "二倍工资差额" in result.answer
        assert "48000" in result.answer

    def test_relative_time_span_months(self) -> None:
        months = _extract_months("去年9月进厂，干到今年3月底")
        assert months == 7

    def test_layered_payload_12000_salary(self) -> None:
        result = build_claim_payload(
            CaseState.from_text(
                "员工2024年1月入职，月工资12000元，工作了5个月，公司一直未签劳动合同。",
                "现在能赔多少？",
                "",
            )
        )
        assert "48000" in result.answer
        assert result.needs_core_followup is False


# ===================================================================
# 4. CaseState Pydantic model
# ===================================================================

class TestCaseState:

    def setup_method(self) -> None:
        self.state = CaseState.from_text(
            case_summary=CASE_SUMMARY,
            question=QUESTION,
            graph_context=GRAPH_CONTEXT,
        )

    def test_monthly_salary(self) -> None:
        assert self.state.monthly_salary == 8000.0

    def test_work_months(self) -> None:
        assert self.state.work_months == 7

    def test_compensable_months(self) -> None:
        """min(7-1, 11) = 6"""
        assert self.state.compensable_months == 6

    def test_double_salary_gap(self) -> None:
        assert self.state.double_salary_gap == 48000.0

    def test_owed_wages(self) -> None:
        assert self.state.owed_wages is None
        assert self.state.owed_months == 2

    def test_deposit(self) -> None:
        assert self.state.deposit == 10000.0

    def test_claim_total(self) -> None:
        assert self.state.claim_total == 74000.0

    def test_semantic_flags(self) -> None:
        assert self.state.has_unsigned_contract is True
        assert self.state.has_owed_wages is True
        assert self.state.has_deposit_dispute is True
        assert self.state.has_entity_change is True

    def test_from_text_without_salary_leaves_none(self) -> None:
        s = CaseState.from_text("去年入职，没签合同")
        assert s.monthly_salary is None
        assert s.double_salary_gap == 0.0
        assert s.claim_total == 0.0

    def test_salary_extractor_self_consistent(self) -> None:
        """Extractor function and CaseState.from_text must agree."""
        assert self.state.monthly_salary == _extract_salary(CASE_SUMMARY)

    def test_months_extractor_self_consistent(self) -> None:
        assert self.state.work_months == _extract_months(CASE_SUMMARY)


# ===================================================================
# 5. Calculators — pure-Python pipeline assertions
# ===================================================================

class TestCalculators:

    def setup_method(self) -> None:
        self.cs = CaseState.from_text(CASE_SUMMARY, QUESTION, GRAPH_CONTEXT)
        self.result = build_claim_payload(self.cs)

    # --- Numeric precision ---

    def test_answer_contains_48000(self) -> None:
        assert "48000" in self.result.answer

    def test_answer_contains_16000(self) -> None:
        assert "16000" in self.result.answer

    def test_answer_contains_10000(self) -> None:
        assert "10000" in self.result.answer

    def test_answer_contains_74000(self) -> None:
        assert "74000" in self.result.answer

    # --- Structural parity ---

    def test_needs_followup_false_when_all_fields_known(self) -> None:
        assert self.result.needs_core_followup is False

    def test_unknowns_empty_when_all_fields_known(self) -> None:
        assert self.result.unknowns == []

    # --- Legal concept coverage ---

    def test_mentions_double_salary(self) -> None:
        assert "二倍工资差额" in self.result.answer

    def test_mentions_labor_contract_law(self) -> None:
        assert "劳动合同法" in self.result.answer

    def test_mentions_entity_piercing(self) -> None:
        assert "人格混同" in self.result.answer

    # --- Actionable next steps ---

    def test_mentions_arbitration(self) -> None:
        assert "仲裁" in self.result.answer

    def test_constructive_step_mentions_drafting(self) -> None:
        assert "仲裁申请书" in self.result.constructive_next_step

    # --- Section structure ---

    def test_has_known_facts_section(self) -> None:
        assert "已知事实" in self.result.answer

    def test_has_calculation_section(self) -> None:
        assert "算账与确权" in self.result.answer

    def test_has_enforcement_section(self) -> None:
        assert "高风险执行" in self.result.answer

    def test_has_action_section(self) -> None:
        assert "下一步" in self.result.answer

    # --- Edge case: missing salary ---

    def test_missing_salary_triggers_followup(self) -> None:
        cs = CaseState.from_text("入职后没签合同，干了半年")
        result = build_claim_payload(cs)
        assert result.needs_core_followup is True
        assert "月工资标准" in result.unknowns

    # --- PayloadResult is a proper Pydantic model ---

    def test_result_is_pydantic_model(self) -> None:
        assert isinstance(self.result, PayloadResult)
        dumped = self.result.model_dump()
        assert "answer" in dumped
        assert "needs_core_followup" in dumped
