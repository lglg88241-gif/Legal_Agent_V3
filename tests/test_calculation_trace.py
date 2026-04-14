"""
Calculation trace tests — Surgery 7.

Verifies the three pillars of the CalculationTrace contract:
  T1 — Trace structure: steps, total, formulas are correct
  T2 — Overclaim detection: CorrectionNotice generated with right content
  T3 — Prompt injection + Guard whitelist: correction text flows through safely

All 12 tests are deterministic — zero LLM calls, zero IO, zero network.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.schemas.case_state import CaseState
from app.logic.calculators import (
    CalculationStep,
    CalculationTrace,
    CorrectionNotice,
    PayloadResult,
    build_claim_payload,
)
from app.agent.nodes.advisor import build_advisor_system_prompt
from app.agent.guards import sanitize_llm_answer, _extract_notice_numbers


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_CANONICAL_CASE = (
    "去年9月进深圳旺铺五金厂，老板李四没签合同。"
    "干到今年3月底厂子关门，月薪8000。"
    "拖欠两月工资和1万押金。"
    "机器搬隔壁村换皮旺铺科技营业。"
)
_CANONICAL_Q = "怎么维权？能赔多少？"

# 12 months case — user asks for 12 months overclaim
_TWELVE_MONTH_CASE = (
    "员工入职后公司一直未签劳动合同，月薪12000元，工作了12个月。"
)
_OVERCLAIM_Q = "公司没签合同，我能要12个月双倍工资共144000元吗？"

_CANONICAL_PAYLOAD: PayloadResult = build_claim_payload(
    CaseState.from_text(_CANONICAL_CASE, _CANONICAL_Q, "")
)
_OVERCLAIM_PAYLOAD: PayloadResult = build_claim_payload(
    CaseState.from_text(_TWELVE_MONTH_CASE, _OVERCLAIM_Q, "")
)


# ═══════════════════════════════════════════════════════════════════════════
# T1 — CalculationTrace structure
# ═══════════════════════════════════════════════════════════════════════════

class TestCalculationTraceStructure:

    def test_trace_is_present_on_payload(self) -> None:
        """PayloadResult must carry a non-None CalculationTrace."""
        assert _CANONICAL_PAYLOAD.trace is not None
        assert isinstance(_CANONICAL_PAYLOAD.trace, CalculationTrace)

    def test_canonical_case_has_three_steps(self) -> None:
        """Canonical case has double-salary + owed-wages + deposit steps."""
        items = [s.item for s in _CANONICAL_PAYLOAD.trace.steps]
        assert "二倍工资差额" in items
        assert "拖欠工资" in items
        assert "押金退还" in items

    def test_double_salary_step_result_correct(self) -> None:
        """Double-salary step must equal 8000 × 6 = 48000."""
        step = next(
            s for s in _CANONICAL_PAYLOAD.trace.steps if s.item == "二倍工资差额"
        )
        assert step.result == 48000.0
        assert "8000" in step.formula
        assert "6" in step.formula

    def test_trace_total_equals_claim_total(self) -> None:
        """trace.total must equal the CaseState.claim_total (74000)."""
        assert _CANONICAL_PAYLOAD.trace.total == 74000.0

    def test_empty_case_has_empty_steps(self) -> None:
        """No facts → no steps, total == 0, no corrections."""
        payload = build_claim_payload(CaseState.from_text("", "一般咨询", ""))
        assert payload.trace.steps == []
        assert payload.trace.total == 0.0
        assert payload.trace.correction_notices == []

    def test_step_has_cap_rule_for_multi_month_case(self) -> None:
        """Double-salary step must include cap_rule when work_months > 1."""
        step = next(
            s for s in _CANONICAL_PAYLOAD.trace.steps if s.item == "二倍工资差额"
        )
        assert step.cap_rule is not None
        assert "11" in step.cap_rule  # statutory cap


# ═══════════════════════════════════════════════════════════════════════════
# T2 — Overclaim detection
# ═══════════════════════════════════════════════════════════════════════════

class TestOverclaimDetection:

    def test_overclaim_generates_correction_notice(self) -> None:
        """User asking for 144000 (12 months) must trigger CorrectionNotice."""
        notices = _OVERCLAIM_PAYLOAD.trace.correction_notices
        assert len(notices) >= 1
        notice = notices[0]
        assert isinstance(notice, CorrectionNotice)
        assert notice.is_overclaim is True

    def test_correction_notice_values_correct(self) -> None:
        """Notice must encode correct user_claimed, legal_limit, discrepancy."""
        notice = _OVERCLAIM_PAYLOAD.trace.correction_notices[0]
        assert notice.user_claimed == 144000.0
        assert notice.legal_limit == 132000.0   # 12000 × 11
        assert notice.discrepancy == 12000.0
        assert notice.item == "二倍工资差额"

    def test_correction_explanation_contains_key_facts(self) -> None:
        """Explanation must reference both figures and the statutory reason."""
        explanation = _OVERCLAIM_PAYLOAD.trace.correction_notices[0].explanation
        assert "144000" in explanation
        assert "132000" in explanation
        assert "11" in explanation          # correct compensable months
        assert "首月不计入" in explanation  # statutory reason

    def test_no_overclaim_for_correct_question(self) -> None:
        """Normal question without a specific overclaim amount → no notices."""
        payload = build_claim_payload(
            CaseState.from_text(_CANONICAL_CASE, _CANONICAL_Q, "")
        )
        assert payload.trace.correction_notices == []

    def test_no_overclaim_when_question_has_no_number(self) -> None:
        """Question with no amount → no notices regardless of case facts."""
        payload = build_claim_payload(
            CaseState.from_text(_TWELVE_MONTH_CASE, "公司没签合同怎么办？", "")
        )
        assert payload.trace.correction_notices == []

    def test_unrelated_large_number_does_not_trigger_overclaim(self) -> None:
        """
        A large number in the question that is NOT near double-salary keywords
        (e.g., a case number like '20230012345') must not trigger a notice.
        """
        question = "案号2023001234567，公司没签合同，请问我能维权吗？"
        payload = build_claim_payload(
            CaseState.from_text(_TWELVE_MONTH_CASE, question, "")
        )
        assert payload.trace.correction_notices == []


# ═══════════════════════════════════════════════════════════════════════════
# T3 — Prompt injection and Guard whitelist
# ═══════════════════════════════════════════════════════════════════════════

class TestPromptInjectionAndGuard:

    def test_correction_notice_injected_into_prompt(self) -> None:
        """When correction_notices is non-empty, prompt must contain VERBATIM block."""
        prompt = build_advisor_system_prompt(
            case_summary=_TWELVE_MONTH_CASE,
            graph_context="",
            rag_context="无有效检索结果",
            payload=_OVERCLAIM_PAYLOAD,
        )
        assert '<correction_notice priority="VERBATIM">' in prompt
        assert "132000" in prompt
        assert "144000" in prompt
        assert "首月不计入" in prompt

    def test_no_correction_block_when_no_notice(self) -> None:
        """Normal case without overclaim must NOT inject correction block."""
        prompt = build_advisor_system_prompt(
            case_summary=_CANONICAL_CASE,
            graph_context="",
            rag_context="无有效检索结果",
            payload=_CANONICAL_PAYLOAD,
        )
        assert '<correction_notice priority="VERBATIM">' not in prompt

    def test_guard_does_not_flag_correction_numbers(self) -> None:
        """
        An LLM answer that faithfully reproduces the correction notice
        (containing 144000 and 132000) must NOT be flagged as hallucination.
        """
        notice = _OVERCLAIM_PAYLOAD.trace.correction_notices[0]
        llm_answer = (
            f"根据法定规则，{notice.explanation}"
            f"因此建议按 {int(notice.legal_limit)} 元申请。"
        )
        result = sanitize_llm_answer(llm_answer, _OVERCLAIM_PAYLOAD, "")
        # Must pass through unchanged — no revert to payload fallback
        assert result != _OVERCLAIM_PAYLOAD.answer
        assert "132000" in result

    def test_guard_still_blocks_unrelated_fabricated_numbers(self) -> None:
        """
        Even with correction notices whitelisted, an LLM inventing a new
        unrelated number (e.g. 200000) must still trigger the guard.
        """
        llm_answer = (
            "您可以主张 200000 元的赔偿，超过法定上限。"
        )
        result = sanitize_llm_answer(llm_answer, _OVERCLAIM_PAYLOAD, "")
        assert result == _OVERCLAIM_PAYLOAD.answer

    def test_legal_basis_demotion_guard_in_prompt(self) -> None:
        """
        When steps carry legal_basis, prompt must include citation_demotion_guard.
        """
        prompt = build_advisor_system_prompt(
            case_summary=_CANONICAL_CASE,
            graph_context="",
            rag_context="无有效检索结果",
            payload=_CANONICAL_PAYLOAD,
        )
        assert "<citation_demotion_guard>" in prompt
