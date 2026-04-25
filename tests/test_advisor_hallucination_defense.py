"""
Hallucination defense tests for the Advisor node.

All 15 tests are deterministic — zero LLM calls, zero IO, zero network.
They verify that the three guard functions in app/agent/guards.py
correctly intercept:
  - Number discrepancy hallucinations (T1-x)
  - Phantom article citation hallucinations (T2-x)
  - End-to-end sanitization behaviour (T3-x)

Guard under test:
    detect_number_discrepancy(llm_answer, payload) -> bool
    detect_phantom_article_numbers(llm_answer, rag_context) -> list[str]
    sanitize_llm_answer(llm_answer, payload, rag_context) -> str
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.guards import (
    detect_number_discrepancy,
    detect_phantom_article_numbers,
    sanitize_llm_answer,
)
from app.schemas.case_state import CaseState
from app.logic.calculators import build_claim_payload, PayloadResult


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

# Authoritative payload: claim_total=74000, double_salary_gap=48000,
# owed_wages=16000, deposit=10000
_PAYLOAD: PayloadResult = build_claim_payload(
    CaseState.from_text(_CANONICAL_CASE, _CANONICAL_Q, "")
)

_CLEAN_LLM_ANSWER = (
    "您可以主张二倍工资差额约 48000 元，加上拖欠工资 16000 元"
    "和押金退还 10000 元，合计约 74000 元。建议尽快申请劳动仲裁。"
)

_RAG_WITH_ARTICLE = "根据《劳动合同法》第82条，用人单位自用工之日起超过一个月不签书面劳动合同，应当每月支付二倍工资。"
_RAG_WITHOUT_ARTICLE = "根据《劳动合同法》相关规定，未签劳动合同可以主张二倍工资差额。"


# ═══════════════════════════════════════════════════════════════════════════
# T1 — detect_number_discrepancy
# ═══════════════════════════════════════════════════════════════════════════

class TestDetectNumberDiscrepancy:

    def test_wrong_number_triggers_discrepancy(self) -> None:
        """LLM invents 56000 (8000×7 wrong formula); payload has 48000."""
        llm = "您可以主张二倍工资差额约 56000 元（月薪8000元乘以7个月）。"
        assert detect_number_discrepancy(llm, _PAYLOAD) is True

    def test_correct_numbers_pass(self) -> None:
        """LLM repeats all numbers verbatim from payload — no discrepancy."""
        assert detect_number_discrepancy(_CLEAN_LLM_ANSWER, _PAYLOAD) is False

    def test_arithmetic_expression_triggers_discrepancy(self) -> None:
        """LLM writes '8000×7=56000' — the result 56000 is not in payload."""
        llm = "计算公式：8000×7=56000元。"
        assert detect_number_discrepancy(llm, _PAYLOAD) is True

    def test_small_numbers_below_threshold_not_flagged(self) -> None:
        """
        Numbers < 1000 (months, percentages, small counts) must not trigger
        the guard even if absent from payload.
        """
        payload_empty = build_claim_payload(CaseState.from_text("", "咨询", ""))
        llm = "您的工龄是7个月，仲裁时效为1年（12个月）。"
        assert detect_number_discrepancy(llm, payload_empty) is False

    def test_sanitize_reverts_to_payload_on_discrepancy(self, caplog) -> None:
        """sanitize_llm_answer must warn when number is wrong without replacing the answer."""
        llm = "您可以主张约 56000 元的赔偿。"
        with caplog.at_level(logging.WARNING, logger="app.agent.guards"):
            result = sanitize_llm_answer(llm, _PAYLOAD, "")
        assert "detected numbers absent from payload/RAG" in caplog.text
        assert result.strip() != _PAYLOAD.answer.strip()

    def test_sanitize_keeps_llm_answer_when_numbers_match(self) -> None:
        """Clean LLM answer with correct numbers must pass through unchanged."""
        result = sanitize_llm_answer(_CLEAN_LLM_ANSWER, _PAYLOAD, "")
        assert result == _CLEAN_LLM_ANSWER

    def test_no_significant_numbers_in_payload_never_triggers(self) -> None:
        """
        When payload has no significant numbers (all-zero case), the guard
        must never flag anything — there is nothing authoritative to protect.
        """
        empty_payload = build_claim_payload(CaseState.from_text("", "问题", ""))
        llm = "您可以先整理证据，目前无法估算具体金额。"
        assert detect_number_discrepancy(llm, empty_payload) is False


# ═══════════════════════════════════════════════════════════════════════════
# T2 — detect_phantom_article_numbers
# ═══════════════════════════════════════════════════════════════════════════

class TestDetectPhantomArticleNumbers:

    def test_article_absent_from_rag_is_phantom(self) -> None:
        """LLM cites 第82条 but RAG has no article numbers at all."""
        llm = "根据《劳动合同法》第82条，您可以主张二倍工资。"
        phantoms = detect_phantom_article_numbers(llm, _RAG_WITHOUT_ARTICLE)
        assert "第82条" in phantoms

    def test_article_present_in_rag_is_clean(self) -> None:
        """LLM cites 第82条 and RAG explicitly contains 第82条 — legitimate."""
        llm = "根据《劳动合同法》第82条，您可以主张二倍工资。"
        phantoms = detect_phantom_article_numbers(llm, _RAG_WITH_ARTICLE)
        assert phantoms == []

    def test_multiple_citations_only_phantom_one_flagged(self) -> None:
        """RAG has 第9条 but not 第82条 — only 第82条 should be flagged."""
        rag = "《劳动合同法》第9条禁止用人单位向劳动者收取押金。"
        llm = "根据第9条，押金应退还。另依据第82条，可主张二倍工资。"
        phantoms = detect_phantom_article_numbers(llm, rag)
        assert "第82条" in phantoms
        assert "第9条" not in phantoms

    def test_no_article_number_in_llm_output_is_clean(self) -> None:
        """LLM answer references law names only, no article numbers — clean."""
        llm = "根据《劳动合同法》和《劳动法》相关规定，您有权主张赔偿。"
        phantoms = detect_phantom_article_numbers(llm, _RAG_WITHOUT_ARTICLE)
        assert phantoms == []

    def test_sanitize_strips_phantom_article_number(self) -> None:
        """
        sanitize_llm_answer must remove phantom '第82条' from the answer
        but preserve the surrounding text including law name.
        """
        llm = "根据《劳动合同法》第82条，您可以主张二倍工资差额约 48000 元。"
        result = sanitize_llm_answer(llm, _PAYLOAD, _RAG_WITHOUT_ARTICLE)
        assert "第82条" not in result
        assert "《劳动合同法》" in result
        assert "48000" in result


# ═══════════════════════════════════════════════════════════════════════════
# T3 — sanitize_llm_answer end-to-end integration
# ═══════════════════════════════════════════════════════════════════════════

class TestSanitizeLlmAnswerIntegration:

    def test_wrong_number_causes_full_payload_fallback(self, caplog) -> None:
        """
        A fabricated number (56000) in an otherwise well-structured answer
        must trigger a warning while preserving the raw answer text.
        """
        llm = (
            "根据《劳动合同法》相关规定，"
            "您可以主张二倍工资差额约 56000 元，"
            "加上拖欠工资 16000 元，合计约 72000 元。"
        )
        with caplog.at_level(logging.WARNING, logger="app.agent.guards"):
            result = sanitize_llm_answer(llm, _PAYLOAD, _RAG_WITHOUT_ARTICLE)
        assert "detected numbers absent from payload/RAG" in caplog.text
        assert result.strip() != _PAYLOAD.answer.strip()
        assert "56000" in result

    def test_phantom_citation_stripped_numbers_preserved(self) -> None:
        """
        When only a phantom citation is present (numbers are correct),
        the answer must NOT fall back to payload — only the citation is stripped.
        """
        llm = (
            "根据《劳动合同法》第82条，"
            "可主张二倍工资差额 48000 元，"
            "拖欠工资 16000 元，押金 10000 元，共计 74000 元。"
        )
        result = sanitize_llm_answer(llm, _PAYLOAD, _RAG_WITHOUT_ARTICLE)
        # Citation stripped
        assert "第82条" not in result
        # Law name preserved
        assert "《劳动合同法》" in result
        # Numbers preserved (not a number discrepancy case)
        assert "48000" in result
        assert "74000" in result
        # Not the raw payload fallback
        assert result != _PAYLOAD.answer

    def test_clean_llm_answer_passes_through_unchanged(self) -> None:
        """
        An LLM answer with correct numbers and no phantom citations must be
        returned verbatim — the guard must be truly transparent when clean.
        """
        result = sanitize_llm_answer(_CLEAN_LLM_ANSWER, _PAYLOAD, _RAG_WITHOUT_ARTICLE)
        assert result == _CLEAN_LLM_ANSWER

    def test_same_law_referenced_article_remains_allowed_when_present_in_rag(self) -> None:
        rag_context = (
            "1. 中华人民共和国劳动合同法第八十七条：用人单位违反本法规定解除或者终止劳动合同的，"
            "应当依照本法第四十七条规定的经济补偿标准的二倍向劳动者支付赔偿金。\n"
            "2. 中华人民共和国劳动合同法第四十七条：经济补偿按劳动者在本单位工作的年限，"
            "每满一年支付一个月工资的标准向劳动者支付。"
        )
        llm = (
            "根据《劳动合同法》第八十七条，用人单位违法解除或者终止劳动合同的，"
            "应当依照本法第四十七条规定的经济补偿标准的二倍向劳动者支付赔偿金。"
        )

        result = sanitize_llm_answer(llm, _PAYLOAD, rag_context)

        assert "第八十七条" in result
        assert "第四十七条" in result
