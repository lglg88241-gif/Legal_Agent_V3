"""
Surgery #9 guards — code-level deterministic defense tests.

Verifies:
1. sanitize_unverified_claims() blocks or annotates user-quoted amounts
2. sanitize_hallucinated_statutes() warns on non-existent law names and
   annotates unverified article-level content

All tests are pure Python: zero LLM, zero IO, zero network.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.guards import (
    sanitize_hallucinated_statutes,
    sanitize_unverified_claims,
)
from app.logic.calculators import build_claim_payload
from app.schemas.case_state import CaseState


_PAYLOAD_132000 = build_claim_payload(
    CaseState.from_text(
        "员工入职后公司一直未签劳动合同，月薪12000元，工作了12个月。",
        "我能要132000元吗？",
        "",
    )
)


# ═══════════════════════════════════════════════════════════════════════════
# T1 — sanitize_unverified_claims
# ═══════════════════════════════════════════════════════════════════════════


def test_unverified_claim_none_payload_inserts_warning_for_999999() -> None:
    result = sanitize_unverified_claims(
        "您有权主张999999元赔偿。",
        "我能要999999元吗？",
        None,
    )
    assert "金额提示" in result
    assert "999999 元" in result or "999999元" in result
    assert "具体金额需经系统核算" in result


def test_unverified_claim_none_payload_inserts_warning_for_200000() -> None:
    result = sanitize_unverified_claims(
        "赔偿200000元是可以考虑的。",
        "赔偿200000元可以吗？",
        None,
    )
    assert "金额提示" in result
    assert "200000 元" in result or "200000元" in result
    assert "具体金额需经系统核算" in result


def test_exact_claim_does_not_trigger_correction() -> None:
    llm_answer = "您有权主张132000元的二倍工资差额。"
    result = sanitize_unverified_claims(
        llm_answer,
        "我能要132000元吗？",
        _PAYLOAD_132000,
    )
    assert result == llm_answer


def test_overclaim_144000_inserts_correction() -> None:
    result = sanitize_unverified_claims(
        "您有权主张144000元。",
        "我能要144000元吗？",
        _PAYLOAD_132000,
    )
    assert "金额纠正" in result
    assert "132000 元" in result or "132000元" in result
    assert "144000 元" in result or "144000元" in result
    assert "具体金额需经系统核算" in result


def test_amount_within_one_percent_tolerance_does_not_trigger() -> None:
    llm_answer = "您可以先按132500元准备主张。"
    result = sanitize_unverified_claims(
        llm_answer,
        "我能要132500元吗？",
        _PAYLOAD_132000,
    )
    assert result == llm_answer


def test_underclaim_does_not_trigger() -> None:
    llm_answer = "您可以主张100000元。"
    result = sanitize_unverified_claims(
        llm_answer,
        "我能要100000元吗？",
        _PAYLOAD_132000,
    )
    assert result == llm_answer


def test_no_amount_question_does_not_trigger() -> None:
    llm_answer = "正当防卫需要结合侵害紧迫性和反击限度判断。"
    result = sanitize_unverified_claims(
        llm_answer,
        "打架反击算正当防卫吗？",
        None,
    )
    assert result == llm_answer


def test_case_number_amount_does_not_trigger() -> None:
    llm_answer = "该案号本身不代表可主张金额。"
    result = sanitize_unverified_claims(
        llm_answer,
        "案号(2024)京0105民初999999号",
        None,
    )
    assert result == llm_answer


# ═══════════════════════════════════════════════════════════════════════════
# T2 — sanitize_hallucinated_statutes
# ═══════════════════════════════════════════════════════════════════════════


def test_nonexistent_labor_rights_law_triggers_warning() -> None:
    result = sanitize_hallucinated_statutes(
        "《劳动者权益保护法》第88条规定用人单位必须支付赔偿。",
        "《劳动者权益保护法》第88条怎么规定的？",
        [],
    )
    assert "法律名称提示" in result
    assert "《劳动者权益保护法》" in result


def test_nonexistent_labor_security_law_triggers_warning() -> None:
    result = sanitize_hallucinated_statutes(
        "《中华人民共和国劳动保障法》规定了相关责任。",
        "《中华人民共和国劳动保障法》怎么规定的？",
        [],
    )
    assert "法律名称提示" in result
    assert "《劳动保障法》" in result


def test_real_labor_contract_law_does_not_trigger_name_warning() -> None:
    llm_answer = "《劳动合同法》对未签合同后果有明确规定。"
    result = sanitize_hallucinated_statutes(
        llm_answer,
        "《劳动合同法》规定了什么？",
        [],
    )
    assert "法律名称提示" not in result


def test_real_civil_code_does_not_trigger_name_warning() -> None:
    llm_answer = "《民法典》是现行有效法律。"
    result = sanitize_hallucinated_statutes(
        llm_answer,
        "《民法典》有相关规定吗？",
        [],
    )
    assert "法律名称提示" not in result


def test_real_criminal_law_does_not_trigger_name_warning() -> None:
    llm_answer = "《刑法》属于现行有效法律。"
    result = sanitize_hallucinated_statutes(
        llm_answer,
        "《刑法》怎么规定的？",
        [],
    )
    assert "法律名称提示" not in result


def test_no_law_name_does_not_trigger() -> None:
    llm_answer = "未签合同的后果需要结合劳动法体系判断。"
    result = sanitize_hallucinated_statutes(
        llm_answer,
        "没签合同怎么办？",
        [],
    )
    assert result == llm_answer


def test_unverified_article_999_adds_disclaimer() -> None:
    result = sanitize_hallucinated_statutes(
        "《劳动合同法》第999条规定，用人单位应当向劳动者支付赔偿。",
        "《劳动合同法》第999条规定了什么？",
        ["《劳动合同法》关于劳动合同订立的规定"],
    )
    assert "未经 RAG 检索验证" in result
    assert "官方法律文本" in result


def test_verified_article_82_does_not_add_disclaimer() -> None:
    llm_answer = "《劳动合同法》第82条规定，用人单位应当向劳动者每月支付二倍工资。"
    result = sanitize_hallucinated_statutes(
        llm_answer,
        "《劳动合同法》第82条规定了什么？",
        ["《劳动合同法》第82条：用人单位自用工之日起超过一个月不满一年未与劳动者订立书面劳动合同的，应当向劳动者每月支付二倍工资。"],
    )
    assert "未经 RAG 检索验证" not in result
