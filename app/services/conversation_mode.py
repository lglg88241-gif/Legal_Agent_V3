from __future__ import annotations

import re

ALLOWED_CONVERSATION_MODES = {
    "UNKNOWN",
    "LEGAL_QA",
    "CASE_ANALYSIS",
    "TRACKED_CASE",
}
ALLOWED_PERSISTENCE_CONSENTS = {
    "unknown",
    "temporary",
    "tracked",
}

LEGAL_QA_KEYWORDS = (
    "最长多久",
    "最短多久",
    "怎么规定",
    "有哪些条文",
    "哪些条文",
    "第几条",
    "法条",
    "法律规定",
    "法律依据",
    "流程是什么",
    "流程怎么走",
    "怎么走",
    "需要什么材料",
    "赔偿标准",
    "标准是什么",
    "如何认定",
    "如何处理",
    "可以吗",
    "是否合法",
    "怎么办",
)

FIRST_PERSON_MARKERS = (
    "我",
    "本人",
    "我们",
    "朋友",
)

RELATION_MARKERS = (
    "公司",
    "老板",
    "同事",
    "单位",
    "学校",
    "房东",
    "租客",
    "前夫",
    "前妻",
    "男方",
    "女方",
    "孩子",
)

CASE_EVENT_MARKERS = (
    "月薪",
    "工资",
    "工作了",
    "工作",
    "上班",
    "被辞退",
    "辞退我",
    "辞退",
    "没签合同",
    "未签合同",
    "拖欠",
    "欠我",
    "借给",
    "工伤",
    "离婚",
    "起诉",
    "仲裁",
    "赔偿",
    "补偿",
    "流水",
    "聊天记录",
    "证据",
    "录音",
    "违法辞退",
    "解除劳动合同",
)

SUPPLEMENTAL_FACT_MARKERS = (
    "还有",
    "另外",
    "补充",
    "聊天记录",
    "工资流水",
    "证据",
    "录音",
    "劳动合同",
)

AMOUNT_PATTERN = re.compile(r"(?:¥|￥)?\d{2,}(?:\.\d+)?\s*(?:元|块|万)?")
TIME_PATTERN = re.compile(r"\d+\s*(?:年|个月|月|天)")


def normalize_conversation_mode(mode: str | None) -> str:
    normalized = str(mode or "").strip().upper()
    return normalized if normalized in ALLOWED_CONVERSATION_MODES else "UNKNOWN"


def normalize_persistence_consent(consent: str | None) -> str:
    normalized = str(consent or "").strip().lower()
    return normalized if normalized in ALLOWED_PERSISTENCE_CONSENTS else "unknown"


def validate_conversation_mode(mode: str | None) -> str:
    normalized = str(mode or "").strip().upper()
    if normalized not in ALLOWED_CONVERSATION_MODES:
        raise ValueError(f"Invalid conversation mode: {mode}")
    return normalized


def validate_persistence_consent(consent: str | None) -> str:
    normalized = str(consent or "").strip().lower()
    if normalized not in ALLOWED_PERSISTENCE_CONSENTS:
        raise ValueError(f"Invalid persistence consent: {consent}")
    return normalized


def detect_conversation_mode(
    user_input: str,
    current_mode: str | None = None,
    case_summary: str | None = None,
) -> dict[str, str | bool | None]:
    clean_input = " ".join((user_input or "").strip().split())
    normalized_mode = normalize_conversation_mode(current_mode)
    summary_text = " ".join((case_summary or "").strip().split())

    if not clean_input:
        return {
            "detected_mode": "UNKNOWN",
            "suggested_mode": None,
            "requires_persistence_consent": False,
            "reason": "empty_input",
        }

    has_legal_qa_keyword = any(keyword in clean_input for keyword in LEGAL_QA_KEYWORDS)
    has_first_person = any(marker in clean_input for marker in FIRST_PERSON_MARKERS)
    has_relation = any(marker in clean_input for marker in RELATION_MARKERS)
    has_case_event = any(marker in clean_input for marker in CASE_EVENT_MARKERS)
    has_supplemental_fact = any(marker in clean_input for marker in SUPPLEMENTAL_FACT_MARKERS)
    has_amount = bool(AMOUNT_PATTERN.search(clean_input))
    has_time = bool(TIME_PATTERN.search(clean_input))
    has_case_context = bool(summary_text)

    is_case_analysis = (
        (has_first_person and (has_case_event or has_relation or has_amount or has_time))
        or (has_relation and has_case_event)
        or (has_amount and has_case_event)
        or (has_case_context and has_supplemental_fact)
    )

    if is_case_analysis:
        requires_consent = normalized_mode != "TRACKED_CASE"
        return {
            "detected_mode": "CASE_ANALYSIS",
            "suggested_mode": "TRACKED_CASE" if requires_consent else None,
            "requires_persistence_consent": requires_consent,
            "reason": "fact_pattern_detected",
        }

    is_question_like = clean_input.endswith(("?", "？", "吗", "么", "呢"))
    if has_legal_qa_keyword or is_question_like:
        return {
            "detected_mode": "LEGAL_QA",
            "suggested_mode": None,
            "requires_persistence_consent": False,
            "reason": "legal_question_pattern_detected",
        }

    return {
        "detected_mode": "UNKNOWN",
        "suggested_mode": None,
        "requires_persistence_consent": False,
        "reason": "no_strong_signal",
    }
