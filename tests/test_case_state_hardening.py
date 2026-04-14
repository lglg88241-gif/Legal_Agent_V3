"""
Defensive hardening tests for CaseState and PayloadResult.

These tests verify that the new Pydantic-based pipeline is MORE
robust than the legacy _safe_extract_* functions it replaces:
  - Negative numeric values are rejected at the data-contract layer
  - Zero salary suppresses all double-salary calculations
  - Empty inputs never raise exceptions
  - PayloadResult uses attribute access (not dict subscript)

All assertions are deterministic and require no LLM or IO.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.schemas.case_state import CaseState
from app.logic.calculators import PayloadResult, build_claim_payload


# ---------------------------------------------------------------------------
# T1: Negative monthly_salary is rejected at Pydantic validation time
# ---------------------------------------------------------------------------

def test_case_state_rejects_negative_salary() -> None:
    """CaseState must raise ValidationError for negative monthly_salary."""
    with pytest.raises(ValidationError):
        CaseState(monthly_salary=-500)


# ---------------------------------------------------------------------------
# T2: Negative work_months is rejected at Pydantic validation time
# ---------------------------------------------------------------------------

def test_case_state_rejects_negative_months() -> None:
    """CaseState must raise ValidationError for negative work_months."""
    with pytest.raises(ValidationError):
        CaseState(work_months=-3)


# ---------------------------------------------------------------------------
# T3: has_unsigned_contract=False suppresses double_salary_gap even when
#     salary and months are both present
# ---------------------------------------------------------------------------

def test_case_state_unsigned_false_no_double_salary() -> None:
    """When has_unsigned_contract is False, double_salary_gap must be 0."""
    cs = CaseState(
        monthly_salary=8000.0,
        work_months=6,
        has_unsigned_contract=False,
    )
    assert cs.double_salary_gap == 0.0
    assert cs.claim_total == 0.0


# ---------------------------------------------------------------------------
# T4: monthly_salary=0 produces no double-salary calculation
# ---------------------------------------------------------------------------

def test_case_state_zero_salary_no_double_salary() -> None:
    """Zero salary with unsigned contract yields zero double_salary_gap."""
    cs = CaseState(
        monthly_salary=0.0,
        work_months=6,
        has_unsigned_contract=True,
    )
    assert cs.double_salary_gap == 0.0
    assert cs.claim_total == 0.0


# ---------------------------------------------------------------------------
# T5: Empty inputs must never raise — all numeric fields default to 0/None
# ---------------------------------------------------------------------------

def test_case_state_empty_inputs_safe_zeros() -> None:
    """from_text with all-empty strings must return a safe all-zero CaseState."""
    cs = CaseState.from_text("", "", "")
    assert cs.monthly_salary is None
    assert cs.work_months is None
    assert cs.compensable_months == 0
    assert cs.double_salary_gap == 0.0
    assert cs.owed_wages == 0.0
    assert cs.deposit == 0.0
    assert cs.claim_total == 0.0
    assert cs.has_unsigned_contract is False


# ---------------------------------------------------------------------------
# T6: PayloadResult must be accessed via attributes, NOT dict subscript
# ---------------------------------------------------------------------------

def test_payload_result_attribute_access_not_dict() -> None:
    """PayloadResult is a Pydantic model; dict-style access must raise TypeError."""
    cs = CaseState.from_text(
        "未签合同，月薪8000，干了7个月",
        "能赔多少？",
        "",
    )
    result = build_claim_payload(cs)
    assert isinstance(result, PayloadResult)

    # Attribute access must work
    assert isinstance(result.answer, str)
    assert isinstance(result.needs_core_followup, bool)
    assert isinstance(result.unknowns, list)

    # Dict subscript must raise TypeError (not silently return None)
    with pytest.raises(TypeError):
        _ = result["answer"]  # type: ignore[index]


# ---------------------------------------------------------------------------
# Bonus: model_validator consistency — derived fields always coherent
# ---------------------------------------------------------------------------

def test_model_validator_recalculates_on_construction() -> None:
    """Derived fields must be coherent immediately after construction."""
    cs = CaseState(
        monthly_salary=10000.0,
        work_months=12,
        has_unsigned_contract=True,
        owed_wages=5000.0,
        deposit=2000.0,
    )
    # compensable_months = min(12-1, 11) = 11
    assert cs.compensable_months == 11
    # double_salary_gap = 10000 * 11 = 110000
    assert cs.double_salary_gap == 110000.0
    # claim_total = 110000 + 5000 + 2000
    assert cs.claim_total == 117000.0
