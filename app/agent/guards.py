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

import logging
import re

from app.logic.calculators import PayloadResult, _OVERCLAIM_TOLERANCE

logger = logging.getLogger(__name__)


LAW_MAX_ARTICLES = {
    "劳动合同法": 98,
    "劳动法": 107,
    "民法典": 1260,
    "刑法": 452,
    "民事诉讼法": 284,
    "刑事诉讼法": 308,
}

_FABRICATED_STATUTE_PATTERN = re.compile(
    r"《?(劳动合同法|劳动法|民法典|刑法|民事诉讼法|刑事诉讼法)》?"
    r"第([\u4e00-\u9fa5零一二三四五六七八九十百千两〇\d]+)条"
)
_LATEST_REVISION_PATTERN = re.compile(r"(20\d{2}年)?(?:最新修订|最新颁布)")


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

# Numbers below this threshold are too common (months, percentages, article
# numbers within a small range) to be reliably attributed to calculations.
_SIGNIFICANT_NUMBER_THRESHOLD = 1000

# Pattern for "第N条" style article citations, supporting both Arabic and Chinese numerals.
_ARTICLE_PATTERN = re.compile(r"第\s*(?:\d+|[零〇一二两三四五六七八九十百千]+)\s*条")

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
_LAW_NAME_PATTERN = re.compile(r"《[^》]{1,40}》")
_LAW_ARTICLE_REF_PATTERN = re.compile(
    r"(《[^》]{1,40}》)(?:第\s*([0-9零〇一二两三四五六七八九十百千]+)\s*条)?"
)
_AMOUNT_PATTERN = re.compile(r"\d+(?:[,，]\d{3})*(?:\.\d+)?")

_CLAIM_AMOUNT_CONTEXT_TOKENS = (
    "能要", "主张", "赔偿", "赔付", "补偿", "要求", "索赔",
    "金额", "双倍工资", "二倍工资", "总额", "赔多少", "能拿",
)
_AMOUNT_CONTEXT_WINDOW = 30
_AMOUNT_DENIAL_TOKENS = (
    "不对", "不是", "不能", "不准确", "超出", "超额", "法定上限",
    "未核验", "未经系统核算", "需要计算", "有误", "无法直接确认",
)
_AMOUNT_AFFIRM_TOKENS = (
    "有权主张", "可以主张", "可主张", "总额为", "赔偿金额为",
    "可以要", "能要", "申请", "金额为", "赔偿为", "双倍工资差额和违法解除劳动合同的赔偿金",
)
_ARTICLE_DESCRIPTION_TOKENS = (
    "规定", "应当", "可以", "必须", "有权", "承担", "不得", "支付",
    "后果", "内容", "属于", "享有", "需要", "支持",
)

_KNOWN_EFFECTIVE_LAWS = {
    "《民法典》",
    "《刑法》",
    "《劳动法》",
    "《劳动合同法》",
    "《劳动争议调解仲裁法》",
    "《社会保险法》",
    "《工伤保险条例》",
    "《个人信息保护法》",
    "《数据安全法》",
    "《消费者权益保护法》",
    "《公司法》",
    "《道路交通安全法》",
    "《治安管理处罚法》",
    "《民事诉讼法》",
    "《刑事诉讼法》",
    "《行政诉讼法》",
    "《行政处罚法》",
    "《劳动保障监察条例》",
    "《妇女权益保障法》",
    "《未成年人保护法》",
    "《反家庭暴力法》",
    "《著作权法》",
    "《商标法》",
    "《专利法》",
    "《反不正当竞争法》",
}

_LAW_ARTICLE_MAX = {
    "《劳动合同法》": 98,
    "《劳动法》": 107,
    "《劳动争议调解仲裁法》": 54,
    "《社会保险法》": 98,
    "《工伤保险条例》": 67,
    "《民法典》": 1260,
    "《刑法》": 452,
    "《民事诉讼法》": 284,
    "《刑事诉讼法》": 308,
    "《行政诉讼法》": 103,
    "《公司法》": 218,
    "《消费者权益保护法》": 64,
    "《个人信息保护法》": 74,
    "《道路交通安全法》": 124,
    "《治安管理处罚法》": 119,
}


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

    Current strategy:
    1. Strip markdown code fences from the final answer text.
    2. Check whether the LLM introduced significant numbers outside payload/RAG;
       if so, emit a warning but do not hard-reset the whole answer.
    3. Strip phantom "第N条" citations that are absent from strict law-header RAG,
       payload text, and correction notices.
    """
    result = _strip_markdown_code_fences(llm_answer)

    # Step 1: check numbers against payload, rag_context, AND correction notices.
    rag_numbers = _extract_significant_numbers(rag_context)
    payload_numbers = _extract_significant_numbers(payload.answer)
    notice_numbers = _extract_notice_numbers(payload)
    allowed_numbers = payload_numbers | rag_numbers | notice_numbers

    llm_numbers = _extract_significant_numbers(result)
    if payload_numbers:  # Only enforce when payload has authoritative numbers.
        # Surgery 8: subtract numbers that live inside case-number / year spans.
        exempt = _extract_exempt_numbers(result)
        fabricated_numbers = llm_numbers - allowed_numbers - exempt
        if fabricated_numbers:
            logger.warning(
                "sanitize_llm_answer detected numbers absent from payload/RAG: %s",
                sorted(fabricated_numbers),
            )

    # Step 2: strip phantom article citations.
    notice_citations: set[str] = set()
    for notice in payload.trace.correction_notices:
        notice_citations |= _extract_article_citations(notice.explanation)

    allowed_citations = (
        _extract_strict_law_citations(rag_context)
        | _extract_article_citations(payload.answer)
        | notice_citations
    )
    llm_citations = _extract_article_citations(result)
    phantom_citations = [c for c in llm_citations if c not in allowed_citations]
    if phantom_citations:
        logger.warning(
            "stripped phantom citations: %s, allowed: %s",
            phantom_citations,
            sorted(allowed_citations),
        )
    for phantom in phantom_citations:
        result = result.replace(phantom, "")

    result = re.sub(r"  +", " ", result).strip()
    return result


def sanitize_unverified_claims(
    llm_answer: str,
    user_input: str,
    payload: PayloadResult | None,
) -> str:
    """
    Guard against the model affirming user-provided claim amounts that have
    not been validated by the deterministic calculation pipeline.

    Rules:
    1. If no authoritative payload is available, prepend a warning and scrub
       affirmative lines that directly endorse the user's amount.
    2. If payload exists and the user's amount exceeds payload.trace.total by
       more than the configured tolerance, prepend a correction notice and
       scrub affirmative lines containing that amount.
    3. Underclaims / in-tolerance amounts pass through unchanged.
    """
    if not payload:
        return llm_answer

    claim_amounts = _extract_claim_amounts_from_user_input(user_input)
    if not claim_amounts:
        return llm_answer.strip()

    if payload is None:
        warning = (
            f"⚠️ **金额提示**：您提到的 {_format_amount_list(claim_amounts)} 尚未经过系统核算。"
            "具体可主张金额需要根据您的工资标准、工作起止时间等事实进行精确计算。"
            "请先提供这些信息，我来帮您核算准确数额。"
        )
        cleaned = _replace_claim_amount_lines(llm_answer, claim_amounts)
        return _prepend_notice(cleaned, warning)

    authoritative_total = round(float(payload.trace.total), 2)
    if authoritative_total <= 0:
        warning = (
            f"⚠️ **金额提示**：您提到的 {_format_amount_list(claim_amounts)} 尚未经过系统核算。"
            "具体可主张金额需要根据您的工资标准、工作起止时间等事实进行精确计算。"
            "请先提供这些信息，我来帮您核算准确数额。"
        )
        cleaned = _replace_claim_amount_lines(llm_answer, claim_amounts)
        return _prepend_notice(cleaned, warning)

    overclaimed = [
        amount for amount in claim_amounts
        if amount > authoritative_total * _OVERCLAIM_TOLERANCE
    ]
    if not overclaimed:
        return llm_answer.strip()

    corrected = _replace_claim_amount_lines(llm_answer, overclaimed)
    warning = (
        f"⚠️ **金额纠正**：经系统核算，您的可主张总额约为 {_format_money(authoritative_total)}。"
        f"您提到的 {_format_amount_list(overclaimed)} 超出了合法主张范围，仲裁委可能不会支持超出部分。"
    )
    return _prepend_notice(corrected, warning)


def sanitize_hallucinated_statutes(
    llm_answer: str,
    user_input: str,
    rag_sources: list[str],
) -> str:
    """
    Guard against hallucinated law names and unverified article-level content.

    1. If the user cites a law name absent from both the RAG context and the
       known-effective-law whitelist, prepend a warning that the law may not
       be a real current law.
    2. If the user asks about a specific article but the retrieved RAG text
       does not verify that law/article pair, append an explicit unverified
       note to the relevant paragraph when the answer appears to describe it.
    """
    result = llm_answer.strip()
    rag_fragments = [fragment for fragment in rag_sources if isinstance(fragment, str) and fragment.strip()]
    rag_text = "\n".join(rag_fragments)
    rag_laws = {_canonical_law_name(name) for name in _LAW_NAME_PATTERN.findall(rag_text)}

    warnings: list[str] = []
    impossible_refs = _detect_impossible_article_numbers(user_input)
    for law_name, article_num in impossible_refs:
        warnings.append(
            f"⚠️ **法条编号纠错**：您提到的{law_name}第{article_num}条不存在。"
            f"该法律现行有效版本共{_LAW_ARTICLE_MAX[law_name]}条，您引用的条号超出实际范围，"
            "可能是记忆有误或受到了误导。建议通过国家法律法规数据库 flk.npc.gov.cn 核验。"
        )

    for law_name in _extract_law_names(user_input):
        canonical = _canonical_law_name(law_name)
        if canonical in rag_laws:
            continue
        if canonical in _KNOWN_EFFECTIVE_LAWS:
            continue
        warnings.append(
            f"⚠️ **法律名称提示**：您提到的 {canonical} 在我的知识库中未收录，且可能并非现行有效法律。"
            "建议您通过官方渠道（如国家法律法规数据库 flk.npc.gov.cn）查证。"
        )

    for law_name, article in _extract_law_article_references(user_input):
        if not article:
            continue
        if _rag_verifies_law_article(rag_text, law_name, article):
            continue
        if not _answer_describes_unverified_article(result, law_name, article):
            continue
        note = "（注：以上内容未经 RAG 检索验证，仅供参考，请以官方法律文本为准。）"
        result = _append_note_to_relevant_paragraph(result, law_name, article, note)

    if warnings:
        result = _prepend_notice(result, "\n".join(warnings))
    return result.strip()


def _strip_markdown_code_fences(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json|markdown|text)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def detect_fabricated_statute(text: str) -> dict:
    """
    Detect a user-cited statute/article pair that exceeds the real article cap.
    """
    clean_text = text or ""
    match = _FABRICATED_STATUTE_PATTERN.search(clean_text)
    if not match:
        return {
            "is_fabricated": False,
            "reason": "",
            "suggested_correction": "",
        }

    law_name = match.group(1)
    article_num = _parse_chinese_or_arabic_number(match.group(2))
    max_articles = LAW_MAX_ARTICLES.get(law_name)
    if article_num is None or max_articles is None or article_num <= max_articles:
        return {
            "is_fabricated": False,
            "reason": "",
            "suggested_correction": "",
        }

    revision_hint = ""
    if _LATEST_REVISION_PATTERN.search(clean_text):
        revision_hint = "；同时“最新修订/最新颁布”的说法也需要通过官方数据库核验"

    suggested_correction = "建议通过国家法律法规数据库核对具体法条名称和条号。"
    if law_name == "劳动合同法" and any(
        token in clean_text for token in ("无固定期限劳动合同", "无限期劳动合同")
    ):
        suggested_correction = "如果您想了解的是无固定期限劳动合同，相关规定见《劳动合同法》第十四条。"

    return {
        "is_fabricated": True,
        "reason": (
            f"《{law_name}》现行有效版本共有{max_articles}条，"
            f"第{article_num}条超出实际条文范围{revision_hint}。"
        ),
        "suggested_correction": suggested_correction,
        "law_name": law_name,
        "article_num": article_num,
        "max_articles": max_articles,
    }


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


def _extract_strict_law_citations(text: str) -> set[str]:
    """
    Extract article citations only from law-header style RAG lines.

    This deliberately ignores citations mentioned inside case chunks, so
    citations are only whitelisted when they appear as retrieved law headers
    (e.g. "1. 中华人民共和国劳动合同法第八十七条：...").
    """
    citations: set[str] = set()
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        header = re.split(r"[：:]", line, maxsplit=1)[0]
        citations |= _extract_article_citations(header)
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


def _extract_claim_amounts_from_user_input(text: str) -> list[float]:
    """Extract claim-related user amounts while ignoring case numbers / years."""
    amounts: list[float] = []
    seen: set[str] = set()
    stripped_text = _strip_case_numbers(text or "")
    exempt = _extract_exempt_numbers(text)

    for match in _AMOUNT_PATTERN.finditer(stripped_text):
        raw = match.group()
        normalized = _normalize_numeric_token(raw)
        if normalized in exempt:
            continue
        try:
            value = float(_strip_number_separators(raw))
        except ValueError:
            continue
        if value < _SIGNIFICANT_NUMBER_THRESHOLD:
            continue
        start, end = match.start(), match.end()
        window = stripped_text[max(0, start - _AMOUNT_CONTEXT_WINDOW): end + _AMOUNT_CONTEXT_WINDOW]
        if not any(token in window for token in _CLAIM_AMOUNT_CONTEXT_TOKENS):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        amounts.append(value)
    return amounts


def _strip_case_numbers(text: str) -> str:
    """Remove case-number spans before numeric guard checks."""
    return _CASE_NUMBER_PATTERN.sub("", text or "")


def _replace_claim_amount_lines(text: str, amounts: list[float]) -> str:
    """Replace affirmative lines that directly endorse unverified claim amounts."""
    amount_tokens = {_normalize_numeric_token(str(amount)) for amount in amounts}
    lines = (text or "").splitlines()
    replaced: list[str] = []

    for line in lines:
        normalized_line = _normalize_text_for_match(line)
        if (
            any(token in normalized_line for token in amount_tokens)
            and any(token in line for token in _CLAIM_AMOUNT_CONTEXT_TOKENS)
            and not any(token in line for token in _AMOUNT_DENIAL_TOKENS)
        ):
            bullet = ""
            stripped = line.lstrip()
            if stripped.startswith("- "):
                bullet = "- "
            elif stripped.startswith("• "):
                bullet = "• "

            if any(token in line for token in _AMOUNT_AFFIRM_TOKENS) or "主张" in line or "赔偿" in line:
                replaced.append(f"{bullet}具体金额需经系统核算")
                continue
        replaced.append(line)

    return "\n".join(replaced).strip()


def _prepend_notice(text: str, notice: str) -> str:
    body = (text or "").strip()
    if notice and notice in body:
        return body
    if not body:
        return notice.strip()
    return f"{notice.strip()}\n\n{body}"


def _format_money(value: float) -> str:
    rounded = round(float(value), 2)
    if rounded.is_integer():
        return f"{int(rounded)} 元"
    return f"{rounded:.2f} 元"


def _format_amount_list(amounts: list[float]) -> str:
    return "、".join(_format_money(amount) for amount in amounts)


def _normalize_numeric_token(raw: str) -> str:
    cleaned = _strip_number_separators(raw)
    try:
        value = float(cleaned)
    except ValueError:
        return cleaned
    return str(int(value)) if value.is_integer() else cleaned


def _normalize_text_for_match(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _strip_number_separators(raw: str) -> str:
    return raw.replace(",", "").replace("，", "")


def _extract_law_names(text: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for law_name in _LAW_NAME_PATTERN.findall(text or ""):
        canonical = _canonical_law_name(law_name)
        if canonical in seen:
            continue
        seen.add(canonical)
        names.append(canonical)
    return names


def _extract_law_article_references(text: str) -> list[tuple[str, str | None]]:
    refs: list[tuple[str, str | None]] = []
    seen: set[tuple[str, str | None]] = set()
    for match in _LAW_ARTICLE_REF_PATTERN.finditer(text or ""):
        law_name = _canonical_law_name(match.group(1))
        article_num = _parse_article_number(match.group(2))
        article = f"第{article_num}条" if article_num is not None else None
        item = (law_name, article)
        if item in seen:
            continue
        seen.add(item)
        refs.append(item)
    return refs


def _parse_article_number(raw: str | None) -> int | None:
    if not raw:
        return None
    stripped = re.sub(r"\s+", "", raw)
    if not stripped:
        return None
    if stripped.isdigit():
        return int(stripped)
    return _chinese_to_arabic(stripped)


def _parse_chinese_or_arabic_number(raw: str | None) -> int | None:
    if not raw:
        return None
    stripped = re.sub(r"\s+", "", raw)
    if not stripped:
        return None
    if stripped.isdigit():
        return int(stripped)
    return _simple_chinese_to_arabic(stripped)


def _simple_chinese_to_arabic(raw: str) -> int | None:
    digits = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    units = {"十": 10, "百": 100, "千": 1000}

    total = 0
    current = 0
    has_token = False
    for char in raw:
        if char in digits:
            current = digits[char]
            has_token = True
            continue
        if char in units:
            total += (current or 1) * units[char]
            current = 0
            has_token = True
            continue
        return None

    if not has_token:
        return None
    return total + current


def _chinese_to_arabic(raw: str) -> int | None:
    digits = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    units = {"十": 10, "百": 100, "千": 1000}

    if not raw:
        return None

    total = 0
    current = 0
    has_digit = False
    for char in raw:
        if char in digits:
            current = digits[char]
            has_digit = True
            continue
        if char in units:
            unit = units[char]
            total += (current or 1) * unit
            current = 0
            has_digit = True
            continue
        return None

    if not has_digit:
        return None
    return total + current


def _detect_impossible_article_numbers(user_input: str) -> list[tuple[str, int]]:
    impossible: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for match in _LAW_ARTICLE_REF_PATTERN.finditer(user_input or ""):
        law_name = _canonical_law_name(match.group(1))
        article_num = _parse_article_number(match.group(2))
        if article_num is None:
            continue
        max_articles = _LAW_ARTICLE_MAX.get(law_name)
        if max_articles is None or article_num <= max_articles:
            continue
        item = (law_name, article_num)
        if item in seen:
            continue
        seen.add(item)
        impossible.append(item)
    return impossible


def _canonical_law_name(law_name: str) -> str:
    inner = (law_name or "").strip()
    if not inner:
        return inner
    if inner.startswith("《") and inner.endswith("》"):
        inner = inner[1:-1]
    inner = inner.replace("中华人民共和国", "")
    return f"《{inner}》"


def _rag_verifies_law_article(rag_text: str, law_name: str, article: str) -> bool:
    normalized = _normalize_text_for_match(rag_text)
    canonical_law = _normalize_text_for_match(_canonical_law_name(law_name))
    canonical_article = _normalize_text_for_match(article)
    combined = f"{canonical_law}{canonical_article}"
    return combined in normalized


def _answer_describes_unverified_article(answer: str, law_name: str, article: str) -> bool:
    lines = (answer or "").splitlines()
    for line in lines:
        if law_name in line and article in line and any(token in line for token in _ARTICLE_DESCRIPTION_TOKENS):
            return True
        if article in line and any(token in line for token in _ARTICLE_DESCRIPTION_TOKENS):
            return True
    return False


def _append_note_to_relevant_paragraph(answer: str, law_name: str, article: str, note: str) -> str:
    if note in answer:
        return answer

    lines = (answer or "").splitlines()
    for index, line in enumerate(lines):
        if law_name in line and article in line and any(token in line for token in _ARTICLE_DESCRIPTION_TOKENS):
            lines[index] = f"{line}{note}"
            return "\n".join(lines).strip()
        if article in line and any(token in line for token in _ARTICLE_DESCRIPTION_TOKENS):
            lines[index] = f"{line}{note}"
            return "\n".join(lines).strip()

    if not answer.strip():
        return note
    return f"{answer.strip()}\n\n{note}"
