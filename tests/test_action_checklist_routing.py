from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.logic.calculators import build_claim_payload
from app.schemas.case_state import CaseState


def _answer(case_type: str, question: str) -> str:
    payload = build_claim_payload(
        CaseState.from_text(case_summary="", question=question, case_type=case_type)
    )
    return payload.answer


def test_action_checklist_route_labor() -> None:
    answer = _answer("劳动争议", "公司未签劳动合同，还拖欠我工资和社保。")
    assert "劳动仲裁流程" in answer
    assert "证据收集清单" in answer
    assert "借条" not in answer


def test_action_checklist_route_loan() -> None:
    answer = _answer("借贷", "朋友借我五万元一直不还，我准备起诉。")
    assert "借贷纠纷材料清单" in answer
    assert "民事诉讼路径" in answer
    assert "劳动仲裁流程" not in answer


def test_action_checklist_route_divorce() -> None:
    answer = _answer("离婚", "想离婚并分割夫妻共同财产，房子车子怎么处理。")
    assert "财产分割材料清单" in answer
    assert "财产分割原则" in answer
    assert "劳动仲裁流程" not in answer


def test_action_checklist_route_criminal() -> None:
    answer = _answer("刑事", "家人因故意伤害被拘留，现在想问怎么应对。")
    assert "刑事辩护律师" in answer
    assert "劳动仲裁流程" not in answer
    assert "借贷纠纷材料清单" not in answer


def test_action_checklist_route_work_injury() -> None:
    answer = _answer("工伤", "我在工厂受伤后被认定工伤十级，想问赔偿路径。")
    assert "工伤赔偿需依据《工伤保险条例》及伤残等级单独计算" in answer
    assert "工伤认定" in answer
    assert "劳动能力鉴定" in answer
    assert "劳动仲裁流程" not in answer
    assert "2N" not in answer


def test_action_checklist_route_general() -> None:
    answer = _answer("其他", "邻居长期噪音扰民，我想先固定证据。")
    assert "时间线" in answer
    assert "劳动仲裁流程" not in answer
    assert "借贷纠纷材料清单" not in answer
