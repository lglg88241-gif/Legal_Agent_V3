"""
Audit B3: _detect_overclaim() 超额检测边界测试。

所有测试均为零 LLM、零 IO、零网络的纯确定性断言。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.schemas.case_state import CaseState
from app.logic.calculators import build_claim_payload

# ── 基础案情：12000元/月，工作12个月，未签合同
# legal_limit = 12000 × 11 = 132000
_BASE_TEXT = "员工入职后公司一直未签劳动合同，月薪12000元，工作了12个月。"


# ═══════════════════════════════════════════════════════════════════════
# B3-1: 用户问"我能要144000元吗"，实际计算132000 → 应触发纠错
# ═══════════════════════════════════════════════════════════════════════

def test_overclaim_144000_triggers_correction() -> None:
    """标准超额场景：用户声称144000，法定上限132000，应生成CorrectionNotice。"""
    payload = build_claim_payload(
        CaseState.from_text(
            _BASE_TEXT,
            "公司没签合同，我能要12个月双倍工资共144000元吗？",
            "",
        )
    )
    notices = payload.trace.correction_notices
    assert len(notices) >= 1
    assert notices[0].is_overclaim is True
    assert notices[0].user_claimed == 144000.0
    assert notices[0].legal_limit == 132000.0


# ═══════════════════════════════════════════════════════════════════════
# B3-2: 案号语境中的 144000 → 不应触发
# ═══════════════════════════════════════════════════════════════════════

def test_case_number_context_no_overclaim() -> None:
    """
    案号 "(2024)京0105民初144000号" 中的144000不在双倍工资关键词窗口内，
    不应触发超额纠错。
    """
    payload = build_claim_payload(
        CaseState.from_text(
            _BASE_TEXT,
            "案号(2024)京0105民初144000号，公司没签合同怎么办？",
            "",
        )
    )
    assert payload.trace.correction_notices == []


# ═══════════════════════════════════════════════════════════════════════
# B3-3: 工号中的大数字 → 不应触发
# ═══════════════════════════════════════════════════════════════════════

def test_employee_id_no_overclaim() -> None:
    """
    "我的工号是144000" — 144000 紧邻"工号"，不在双倍工资关键词窗口内，
    不应触发超额纠错。
    """
    payload = build_claim_payload(
        CaseState.from_text(
            _BASE_TEXT,
            "我的工号是144000，公司没签合同，请问我怎么维权？",
            "",
        )
    )
    assert payload.trace.correction_notices == []


# ═══════════════════════════════════════════════════════════════════════
# B3-4: 用户问"双倍工资赔偿能拿多少"但没给具体数字 → 不应触发
# ═══════════════════════════════════════════════════════════════════════

def test_no_specific_amount_no_overclaim() -> None:
    """问题中无具体金额数字，不应生成任何 CorrectionNotice。"""
    payload = build_claim_payload(
        CaseState.from_text(
            _BASE_TEXT,
            "公司没签合同，双倍工资赔偿能拿多少？",
            "",
        )
    )
    assert payload.trace.correction_notices == []


# ═══════════════════════════════════════════════════════════════════════
# B3-5: 用户给的数字 < 计算结果 → 不应触发（少要不纠正）
# ═══════════════════════════════════════════════════════════════════════

def test_underclaim_does_not_trigger() -> None:
    """
    用户问"我能要100000元吗"，法定上限132000。
    用户 underclimed（少要），不应生成 CorrectionNotice。
    """
    payload = build_claim_payload(
        CaseState.from_text(
            _BASE_TEXT,
            "公司没签合同，我能要双倍工资赔偿100000元吗？",
            "",
        )
    )
    assert payload.trace.correction_notices == []


# ═══════════════════════════════════════════════════════════════════════
# B3-6: 用户给的数字 = 计算结果 → 不应触发
# ═══════════════════════════════════════════════════════════════════════

def test_exact_claim_no_overclaim() -> None:
    """
    用户问"我能要132000元二倍工资差额吗"，恰好等于法定金额。
    1%容差：132000 <= 132000 * 1.01 → 不触发。
    """
    payload = build_claim_payload(
        CaseState.from_text(
            _BASE_TEXT,
            "公司没签合同，我能要二倍工资差额132000元吗？",
            "",
        )
    )
    assert payload.trace.correction_notices == []


# ═══════════════════════════════════════════════════════════════════════
# B3-7: 多个金额混合 — 超额项被识别，不相关项不干扰
# ═══════════════════════════════════════════════════════════════════════

def test_multiple_amounts_only_overclaim_flagged() -> None:
    """
    "我能要144000元赔偿加50000元精神损失费吗"。
    144000 在双倍工资关键词窗口内，50000 在"精神损失费"窗口内（无双倍关键词）。
    应仅生成144000超额的 CorrectionNotice，50000 不触发。
    """
    payload = build_claim_payload(
        CaseState.from_text(
            _BASE_TEXT,
            "公司没签合同，我能要144000元赔偿加50000元精神损失费吗？",
            "",
        )
    )
    notices = payload.trace.correction_notices
    assert len(notices) >= 1
    claimed_amounts = [n.user_claimed for n in notices]
    assert 144000.0 in claimed_amounts
    # 50000 精神损失费不应出现在 correction_notices（不是双倍工资超额项）
    assert 50000.0 not in claimed_amounts


# ═══════════════════════════════════════════════════════════════════════
# B3-8: 超出 1 元（132001 vs 132000）→ 灵敏度测试
# ═══════════════════════════════════════════════════════════════════════

def test_one_yuan_over_legal_limit_sensitivity() -> None:
    """
    用户问"我能要132001元二倍工资吗"，比法定上限多 1 元。
    1%容差：132001 > 132000 * 1.01 = 133320？不，132001 < 133320。
    所以 132001 在容差范围内（1% = 1320元），不触发。
    此测试记录 1% 容差的实际效果：单元超额被容差吸收，CorrectionNotice 不生成。
    """
    payload = build_claim_payload(
        CaseState.from_text(
            _BASE_TEXT,
            "公司没签合同，我能要二倍工资差额132001元吗？",
            "",
        )
    )
    # 132001 <= 132000 * 1.01 (=133320.0)，应被1%容差吸收，不触发
    assert payload.trace.correction_notices == []
