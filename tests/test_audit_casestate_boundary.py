"""
Audit B1: CaseState + build_claim_payload boundary爆破测试。

所有测试均为零 LLM、零 IO、零网络的纯确定性断言。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pydantic import ValidationError

from app.schemas.case_state import CaseState
from app.logic.calculators import build_claim_payload


# ═══════════════════════════════════════════════════════════════════════
# B1-1: 月薪为 0 时不产生除零错误，PayloadResult 正常构建
# ═══════════════════════════════════════════════════════════════════════

def test_zero_salary_no_division_by_zero() -> None:
    """月薪 0 时 build_claim_payload 不应抛出任何异常，double_salary_gap 应为 0。"""
    cs = CaseState(
        case_summary="",
        question="",
        monthly_salary=0.0,
        work_months=6,
        has_unsigned_contract=True,
    )
    payload = build_claim_payload(cs)
    assert payload.trace.total == 0.0
    assert cs.double_salary_gap == 0.0


# ═══════════════════════════════════════════════════════════════════════
# B1-2: 时间倒挂（入职日期 > 离职日期） —— CaseState 是否产生负数？
# 注：CaseState 无日期字段，时间解析由 _extract_months() 承担。
# 测试：从 text 构造导致 work_months < 0 的情况是否被拦截。
# ═══════════════════════════════════════════════════════════════════════

def test_time_inversion_work_months_not_negative() -> None:
    """
    当 work_months 直接被设为负数时，ge=0 约束应拒绝它（ValidationError）。
    验证 Surgery 5 的防御有效。
    """
    with pytest.raises(ValidationError):
        CaseState(
            monthly_salary=8000.0,
            work_months=-3,
            has_unsigned_contract=True,
        )


def test_from_text_time_inversion_graceful() -> None:
    """
    from_text() 接收"今年9月入职，去年3月离职"（倒挂文本）。
    _extract_months() 对去年/今年模式的计算：(12-9+1)+3=7，仍为正数。
    但如果两个月份数字构成"今年月份小于去年月份"，应确认结果不会为负。
    """
    # 构造真实倒挂：去年12月入职，今年1月离职 → months = (12-12+1)+1 = 2，正数
    payload = build_claim_payload(
        CaseState.from_text(
            "去年12月进厂，干到今年1月，月薪8000，没签合同。",
            "",
            "",
        )
    )
    # 不管月数是多少，总额不应为负
    assert payload.trace.total >= 0.0


# ═══════════════════════════════════════════════════════════════════════
# B1-3: 入职当月即离职（work_months=1），可主张月数应为 0
# ═══════════════════════════════════════════════════════════════════════

def test_one_month_work_compensable_months_is_zero() -> None:
    """
    工作 1 个月：compensable_months = max(min(1-1, 11), 0) = 0。
    double_salary_gap 应为 0（首月不计入）。
    """
    cs = CaseState(
        monthly_salary=10000.0,
        work_months=1,
        has_unsigned_contract=True,
    )
    assert cs.compensable_months == 0
    assert cs.double_salary_gap == 0.0


# ═══════════════════════════════════════════════════════════════════════
# B1-4: 极大月薪数值（天文数字）不应溢出
# ═══════════════════════════════════════════════════════════════════════

def test_extreme_large_salary_no_overflow() -> None:
    """月薪 999999999 元，11 个月二倍工资差额，Python float 不溢出，PayloadResult 正常构建。"""
    cs = CaseState(
        monthly_salary=999_999_999.0,
        work_months=12,
        has_unsigned_contract=True,
    )
    # Python float: max ~1.8e308；999999999*11=10999999989，远在范围内
    assert cs.double_salary_gap == pytest.approx(999_999_999.0 * 11, rel=1e-6)
    payload = build_claim_payload(cs)
    assert payload is not None
    assert payload.trace.total > 0


# ═══════════════════════════════════════════════════════════════════════
# B1-5: from_text() 接收完全无关文本（古诗），优雅降级，不抛异常
# ═══════════════════════════════════════════════════════════════════════

def test_from_text_irrelevant_poem_graceful_degradation() -> None:
    """完全无关文本不应触发任何异常，返回全零 CaseState。"""
    poem = "床前明月光，疑是地上霜。举头望明月，低头思故乡。"
    cs = CaseState.from_text(poem, poem, poem)
    assert cs.monthly_salary is None
    assert cs.work_months is None
    assert cs.claim_total == 0.0
    assert cs.has_unsigned_contract is False
    payload = build_claim_payload(cs)
    assert payload.trace.steps == []
    assert payload.trace.total == 0.0


# ═══════════════════════════════════════════════════════════════════════
# B1-6: from_text() 接收空字符串
# ═══════════════════════════════════════════════════════════════════════

def test_from_text_empty_strings_safe() -> None:
    """三个空字符串输入应产生合法全零 CaseState，不抛异常。"""
    cs = CaseState.from_text("", "", "")
    assert cs.monthly_salary is None
    assert cs.claim_total == 0.0
    payload = build_claim_payload(cs)
    assert payload.needs_core_followup is True  # 空输入应触发追问


# ═══════════════════════════════════════════════════════════════════════
# B1-7: from_text() 接收 None（Python 调用方不小心传 None）
# ═══════════════════════════════════════════════════════════════════════

def test_from_text_none_inputs_safe() -> None:
    """
    from_text() 的签名允许默认值，但若调用方显式传 None，
    应能优雅处理（不抛 AttributeError）。
    """
    # from_text 内部使用 `if part` 过滤，但 None 会导致类型问题
    try:
        cs = CaseState.from_text(None, None, None)  # type: ignore[arg-type]
        # 若不抛异常，结果应合法
        assert cs.claim_total == 0.0
    except (TypeError, AttributeError) as exc:
        # 记录：当前实现对 None 输入无防御，会抛 TypeError
        pytest.fail(f"from_text(None, None, None) raised {type(exc).__name__}: {exc}")


# ═══════════════════════════════════════════════════════════════════════
# B1-8: 所有可选字段缺失时，PayloadResult 结构完整性
# ═══════════════════════════════════════════════════════════════════════

def test_all_optional_fields_missing_payload_complete() -> None:
    """最小 CaseState（仅默认值）产生的 PayloadResult 所有必填字段必须存在。"""
    cs = CaseState()
    payload = build_claim_payload(cs)
    assert isinstance(payload.answer, str)
    assert isinstance(payload.needs_core_followup, bool)
    assert isinstance(payload.followup, str)
    assert isinstance(payload.constructive_next_step, str)
    assert isinstance(payload.unknowns, list)
    assert payload.trace is not None
    assert payload.trace.steps == []
    assert payload.trace.correction_notices == []


# ═══════════════════════════════════════════════════════════════════════
# B1-9: 浮点精度 — 月薪 7333.33，11 个月结果精度
# ═══════════════════════════════════════════════════════════════════════

def test_float_precision_7333_33_salary() -> None:
    """
    月薪 7333.33，工作 12 个月（可主张 11 个月）。
    double_salary_gap = 7333.33 × 11 = 80666.63。
    验证计算结果在合理精度范围内（误差 < 0.01 元）。
    """
    cs = CaseState(
        monthly_salary=7333.33,
        work_months=12,
        has_unsigned_contract=True,
    )
    expected = 7333.33 * 11
    assert cs.double_salary_gap == pytest.approx(expected, abs=0.01)


# ═══════════════════════════════════════════════════════════════════════
# B1-10: 跨闰年文本输入解析
# ═══════════════════════════════════════════════════════════════════════

def test_leap_year_text_parsing_no_exception() -> None:
    """
    提到 2024-02（闰年）的文本，_extract_months 不应抛异常。
    具体月份数值基于 '去年N月...今年M月' 模式。
    """
    text = "去年2月入职，干到今年2月，月薪10000，没签合同。"
    cs = CaseState.from_text(text, "", "")
    # 去年2月→今年2月：(12-2+1)+2 = 13个月
    assert cs.work_months == 13
    assert cs.compensable_months == min(13 - 1, 11)  # = 11


# ═══════════════════════════════════════════════════════════════════════
# B1-11: 超长工作年限（30年），可主张月数有上限 11
# ═══════════════════════════════════════════════════════════════════════

def test_thirty_year_work_compensable_months_capped_at_11() -> None:
    """
    工作 360 个月（30 年），法定上限 11 个月，compensable_months 不得超过 11。
    """
    cs = CaseState(
        monthly_salary=8000.0,
        work_months=360,
        has_unsigned_contract=True,
    )
    assert cs.compensable_months == 11
    assert cs.double_salary_gap == pytest.approx(8000.0 * 11, rel=1e-9)


# ═══════════════════════════════════════════════════════════════════════
# B1-12: 月薪负数被 ge=0 约束拦截（Surgery 5 防御确认）
# ═══════════════════════════════════════════════════════════════════════

def test_negative_salary_rejected_by_ge_constraint() -> None:
    """ge=0 约束应在构造时拒绝负工资，抛出 ValidationError。"""
    with pytest.raises(ValidationError):
        CaseState(monthly_salary=-1.0)


# ═══════════════════════════════════════════════════════════════════════
# B1-13: model_validator 在入职日期 None / work_months=None + has_unsigned_contract=True
# ═══════════════════════════════════════════════════════════════════════

def test_validator_unsigned_contract_no_months_no_salary() -> None:
    """
    has_unsigned_contract=True 但 work_months=None, monthly_salary=None 时，
    model_validator 不应崩溃，double_salary_gap 应保持 0。
    """
    cs = CaseState(has_unsigned_contract=True)
    assert cs.compensable_months == 0
    assert cs.double_salary_gap == 0.0
    assert cs.claim_total == 0.0


def test_validator_monthly_salary_none_work_months_set() -> None:
    """
    monthly_salary=None 但 work_months 有值时，
    model_validator 不应崩溃（None 检查在 `and` 短路中被拦截）。
    """
    cs = CaseState(
        monthly_salary=None,
        work_months=6,
        has_unsigned_contract=True,
    )
    assert cs.double_salary_gap == 0.0
    assert cs.compensable_months == 0  # 因 salary is None，validator 不执行计算
