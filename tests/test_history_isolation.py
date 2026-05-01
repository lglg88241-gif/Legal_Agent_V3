from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.logic.calculators import build_claim_payload
from app.schemas.case_state import CaseState


def test_historical_salary_does_not_trigger_notice_or_2n() -> None:
    cs = CaseState(
        case_summary="当前案件只确认存在解除争议，但月薪和工龄都没有核实。",
        question="公司突然让我走，我能主张什么？",
        historical_reference="历史案件：月薪20000元，工作4年，未提前30天通知。",
        monthly_salary=20000.0,
        monthly_salary_source="historical",
        service_years=4.0,
        service_years_source="historical",
        is_illegal_dismissal=True,
        no_30day_notice=True,
    )

    result = build_claim_payload(cs)

    assert "违法辞退赔偿金（2N）" not in result.answer
    assert "代通知金" not in result.answer
    assert all(step.item != "违法辞退赔偿金" for step in result.trace.steps)
    assert all(step.item != "代通知金" for step in result.trace.steps)


def test_non_labor_current_case_ignores_historical_labor_salary() -> None:
    scenarios = [
        ("刑事", "我朋友因故意伤害被刑拘，想问量刑。"),
        ("工伤", "我在工厂受伤被认定工伤十级，想问赔偿项目。"),
        ("借贷", "朋友借我5万元一直不还，我想起诉。"),
    ]

    historical_reference = "历史劳动案件：月薪20000元，工作4年，公司未提前30天通知。"

    for _, question in scenarios:
        cs = CaseState.from_text(
            case_summary="",
            question=question,
            graph_context="",
            historical_reference=historical_reference,
        )
        result = build_claim_payload(cs)
        assert "违法辞退赔偿金（2N）" not in result.answer
        assert "代通知金" not in result.answer

