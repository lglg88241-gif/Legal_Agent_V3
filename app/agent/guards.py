"""
Post-generation hallucination guards for the Advisor node.

All functions in this module are pure (no LLM, no IO, no network).
They operate on already-generated LLM text and deterministic pipeline
output, acting as a last-mile firewall before the response is returned.

Two categories of hallucination are intercepted:

1. Number discrepancy — the LLM rewrites or recalculates numbers that
   were already computed deterministically by build_claim_payload().
   Any significant number (>= 1000) in the LLM output that does not
   appear in the authoritative payload is treated as a fabrication.

2. Phantom article citations — the LLM invents article numbers
   ("第82条") that are absent from both the RAG context and the
   authoritative payload skeleton. Only the law name may be preserved.

Surgery 7: allowed-number whitelist extended to include numbers from
CalculationTrace.correction_notices, so verbatim-reproduced correction
text (user_claimed, legal_limit) is never mistakenly flagged.
"""
from __future__ import annotations

import re

from app.logic.calculators import PayloadResult


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

# Numbers below this threshold are too common (months, percentages, article
# numbers within a small range) to be reliably attributed to calculations.
_SIGNIFICANT_NUMBER_THRESHOLD = 1000

# Pattern for "第N条" style article citations (N is one or more digits).
_ARTICLE_PATTERN = re.compile(r"第\s*\d+\s*条")

# Pattern for extracting all integers / floats from a string.
_NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?")

# Surgery 8: exempt patterns ─────────────────────────────────────────────────
# Case-number pattern: (YYYY) + non-whitespace body (digits, Chinese chars,
# alphanumeric) + 号.  Examples:
#   (2024)京0105民初12345号   (2023)粤03劳仲案56789号
# The body intentionally allows digits so court codes like "0105" are covered.
_CASE_NUMBER_PATTERN = re.compile(
    r"\(\d{4}\)\S{1,30}号"
)

# Year pattern: bare four-digit year followed by 年
_YEAR_PATTERN = re.compile(r"(\d{4})年")


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def detect_number_discrepancy(llm_answer: str, payload: PayloadResult) -> bool:
    """
    Return True if the LLM answer contains a significant number that is
    NOT present in the authoritative payload text OR correction notices.

    Significant means >= _SIGNIFICANT_NUMBER_THRESHOLD (1000).  Small
    numbers (months, counts, percentages) are ignored because they appear
    legitimately in prose without being calculation results.

    If the payload itself contains no significant numbers (e.g. claim_total
    == 0, no calculations performed), always returns False — there is
    nothing to contradict.

    Surgery 8: numbers inside case-number spans and year tokens in the
    LLM answer are exempted so RAG-cited case numbers are never mistaken
    for fabricated monetary amounts.
    """
    payload_numbers = _extract_significant_numbers(payload.answer)
    # Surgery 7: also whitelist numbers from correction notices
    notice_numbers = _extract_notice_numbers(payload)
    all_authoritative = payload_numbers | notice_numbers

    if not payload_numbers:
        # No authoritative numbers to protect — nothing to flag.
        return False

    llm_numbers = _extract_significant_numbers(llm_answer)
    # Surgery 8: remove numbers that appear in exempt contexts
    exempt = _extract_exempt_numbers(llm_answer)
    fabricated = llm_numbers - all_authoritative - exempt
    return bool(fabricated)


def detect_phantom_article_numbers(llm_answer: str, rag_context: str) -> list[str]:
    """
    Return a list of "第N条" citations present in llm_answer that do NOT
    appear in rag_context.

    A citation is considered legitimate only if the exact same "第N条"
    string (normalised for whitespace) appears somewhere in the RAG context.
    Citations already present in the payload skeleton are handled by the
    caller (sanitize_llm_answer checks both rag_context and payload.answer).
    """
    llm_citations = _extract_article_citations(llm_answer)
    rag_citations = _extract_article_citations(rag_context)
    return [c for c in llm_citations if c not in rag_citations]


def sanitize_llm_answer(
    llm_answer: str,
    payload: PayloadResult,
    rag_context: str,
) -> str:
    """
    Apply all hallucination guards and return a safe answer string.

    Strategy:
    1. Number discrepancy → revert entirely to payload.answer (hard reset).
       Rationale: a wrong number anywhere poisons the whole response.
       Surgery 7: correction_notice numbers are whitelisted — verbatim
       reproductions of the Python-generated correction text are allowed.
       Surgery 8: case-number spans and year tokens in the LLM answer are
       exempted so RAG-cited case numbers are never misidentified as
       fabricated monetary amounts.
    2. Phantom article citations → strip only the fabricated "第N条" tokens,
       preserving the surrounding prose and the law name.
       Rationale: removing one citation is surgical; the rest of the answer
       may still be valuable.

    Returns the (possibly modified) answer string.
    """
    # Step 1: check numbers against payload, rag_context, AND correction notices.
    rag_numbers = _extract_significant_numbers(rag_context)
    payload_numbers = _extract_significant_numbers(payload.answer)
    notice_numbers = _extract_notice_numbers(payload)
    allowed_numbers = payload_numbers | rag_numbers | notice_numbers

    llm_numbers = _extract_significant_numbers(llm_answer)
    if payload_numbers:  # Only enforce when payload has authoritative numbers.
        # Surgery 8: subtract numbers that live inside case-number / year spans.
        exempt = _extract_exempt_numbers(llm_answer)
        fabricated_numbers = llm_numbers - allowed_numbers - exempt
        if fabricated_numbers:
            return payload.answer

    # Step 2: strip phantom article citations.
    # A citation is allowed if it appears in RAG context OR in payload answer
    # OR in any correction notice explanation.
    notice_citations: set[str] = set()
    for notice in payload.trace.correction_notices:
        notice_citations |= _extract_article_citations(notice.explanation)

    allowed_citations = (
        _extract_article_citations(rag_context)
        | _extract_article_citations(payload.answer)
        | notice_citations
    )
    llm_citations = _extract_article_citations(llm_answer)
    phantom_citations = [c for c in llm_citations if c not in allowed_citations]

    result = llm_answer
    for phantom in phantom_citations:
        result = result.replace(phantom, "")

    # Clean up any double spaces left by removal.
    result = re.sub(r"  +", " ", result).strip()
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────

def _extract_significant_numbers(text: str) -> set[str]:
    """Extract all number tokens >= threshold as normalised strings."""
    results: set[str] = set()
    for match in _NUMBER_PATTERN.finditer(text):
        raw = match.group()
        try:
            value = float(raw)
        except ValueError:
            continue
        if value >= _SIGNIFICANT_NUMBER_THRESHOLD:
            # Normalise: strip trailing .0 for integers
            if value == int(value):
                results.add(str(int(value)))
            else:
                results.add(raw)
    return results


def _extract_notice_numbers(payload: PayloadResult) -> set[str]:
    """
    Extract significant numbers from all correction_notice explanations
    and numeric fields so they are never flagged as hallucinations when
    the LLM faithfully reproduces the verbatim correction text.
    """
    numbers: set[str] = set()
    for notice in payload.trace.correction_notices:
        numbers |= _extract_significant_numbers(notice.explanation)
        # Also add the raw numeric fields directly.
        for val in (notice.user_claimed, notice.legal_limit, notice.discrepancy):
            if val >= _SIGNIFICANT_NUMBER_THRESHOLD:
                numbers.add(str(int(val)) if val == int(val) else str(val))
    return numbers


def _extract_article_citations(text: str) -> set[str]:
    """Extract all '第N条' style citations, normalised (no whitespace)."""
    citations: set[str] = set()
    for match in _ARTICLE_PATTERN.finditer(text):
        normalised = re.sub(r"\s+", "", match.group())
        citations.add(normalised)
    return citations


def _extract_exempt_numbers(text: str) -> set[str]:
    """
    Extract numbers that should be exempted from hallucination detection
    because they appear in well-known non-monetary contexts:

    1. Case numbers: (2024)京0105民初12345号 — every digit token inside
       the matched span (e.g. 2024, 105, 12345) is exempt.
    2. Years: 2024年 — the four-digit year is exempt.

    Returns a set of normalised number strings (same format as
    _extract_significant_numbers) so callers can subtract them from the
    fabricated-numbers set.

    Surgery 8: resolves P1-B where RAG-cited case numbers were mistakenly
    treated as hallucinated monetary amounts.
    """
    exempt: set[str] = set()

    # 1. Case-number spans
    for m in _CASE_NUMBER_PATTERN.finditer(text):
        for raw in re.findall(r"\d+", m.group()):
            try:
                val = float(raw)
                if val >= _SIGNIFICANT_NUMBER_THRESHOLD:
                    exempt.add(str(int(val)) if val == int(val) else raw)
            except ValueError:
                pass

    # 2. Year tokens (e.g. 2024年)
    for m in _YEAR_PATTERN.finditer(text):
        raw = m.group(1)
        try:
            val = float(raw)
            if val >= _SIGNIFICANT_NUMBER_THRESHOLD:
                exempt.add(str(int(val)) if val == int(val) else raw)
        except ValueError:
            pass

    return exempt
