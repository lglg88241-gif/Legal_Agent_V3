from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.logic.calculators import build_claim_payload
from app.schemas.case_state import CaseState


def test_violation_dismissal_3年_月薪15000() -> None:
    cs = CaseState(
        monthly_salary=15000.0,
        service_years=3.0,
        is_illegal_dismissal=True,
    )
    result = build_claim_payload(cs)

    assert cs.n_value == 3.0
    assert cs.illegal_dismissal_compensation == 90000.0
    assert any(step.item == "违法辞退赔偿金" and step.result == 90000.0 for step in result.trace.steps)
    assert "90000" in result.answer


def test_violation_dismissal_4_3年() -> None:
    cs = CaseState(
        monthly_salary=10000.0,
        service_years=4.3,
        is_illegal_dismissal=True,
    )
    result = build_claim_payload(cs)

    assert cs.n_value == 4.5
    assert cs.illegal_dismissal_compensation == 90000.0
    assert any("N值 4.5" in step.formula for step in result.trace.steps if step.item == "违法辞退赔偿金")


def test_violation_dismissal_无30天通知() -> None:
    cs = CaseState(
        monthly_salary=12000.0,
        service_years=2.0,
        is_illegal_dismissal=True,
        no_30day_notice=True,
    )
    result = build_claim_payload(cs)

    assert cs.illegal_dismissal_compensation == 48000.0
    assert cs.notice_substitution_pay == 12000.0
    assert cs.claim_total == 60000.0
    assert any(step.item == "代通知金" and step.result == 12000.0 for step in result.trace.steps)


def test_合法辞退() -> None:
    cs = CaseState(
        monthly_salary=15000.0,
        service_years=3.0,
        is_illegal_dismissal=False,
    )
    result = build_claim_payload(cs)

    assert cs.illegal_dismissal_compensation == 0.0
    assert all(step.item != "违法辞退赔偿金" for step in result.trace.steps)
