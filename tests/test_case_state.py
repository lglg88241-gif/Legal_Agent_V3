from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.schemas.case_state import CaseState


def test_from_text_none_returns_default() -> None:
    """P1-A: from_text(None) should not raise."""
    result = CaseState.from_text(None)  # type: ignore[arg-type]
    assert result is not None
    assert result == CaseState()


def test_from_text_empty_string_returns_default() -> None:
    result = CaseState.from_text("")
    assert result is not None
    assert result == CaseState()


def test_no_floating_point_artifacts_on_derived_amounts() -> None:
    """P2-B: derived monetary fields should be rounded to 2 decimals."""
    result = CaseState(
        monthly_salary=26666.667,
        work_months=4,
        has_unsigned_contract=True,
    )
    assert result.double_salary_gap == 80000.0
    assert result.claim_total == 80000.0
    assert "80000.001" not in str(result.double_salary_gap)
