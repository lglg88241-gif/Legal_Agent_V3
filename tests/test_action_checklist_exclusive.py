from __future__ import annotations

from app.logic.calculators import build_claim_payload
from app.schemas.case_state import CaseState


def test_labor_case_excludes_loan_checklist() -> None:
    result = build_claim_payload(
        CaseState.from_text(
            "员工2024年1月入职，月工资12000元，公司一直未签劳动合同，后被违法辞退。",
            "我能主张双倍工资差额和赔偿吗？",
            "",
        )
    )

    assert "借条" not in result.answer
    assert "民事诉讼" not in result.answer


def test_loan_case_excludes_labor_checklist() -> None:
    result = build_claim_payload(
        CaseState.from_text(
            "我借给朋友50000元，有借条，对方一直不还，还说只愿意还30000元。",
            "我应该怎么起诉并要回本金和利息？",
            "",
        )
    )

    assert "劳动仲裁" not in result.answer
    assert "考勤" not in result.answer


def test_divorce_case_excludes_loan_and_labor_checklist() -> None:
    result = build_claim_payload(
        CaseState.from_text(
            "我结婚8年，有婚前房产，婚后共同买房买车，现在准备离婚分割财产。",
            "财产怎么分？",
            "",
        )
    )

    assert "借条" not in result.answer
    assert "劳动仲裁" not in result.answer
