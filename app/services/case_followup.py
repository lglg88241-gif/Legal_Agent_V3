from __future__ import annotations

import re

_FACT_UPDATE_PHRASES = (
    "我补充一下",
    "补充一下",
    "再补充",
    "更正一下",
    "纠正一下",
    "更新一下",
    "说明一下",
    "又发生了",
    "公司又通知",
    "对方又",
    "我找到了",
    "新增证据",
    "补充证据",
    "实际入职时间是",
    "工资其实是",
    "对方名称是",
    "补充事实",
    "补充情况",
)

_CONSULT_PHRASES = (
    "准备哪些证据",
    "哪些证据",
    "需要准备什么证据",
    "需要准备什么材料",
    "下一步怎么办",
    "接下来怎么办",
    "仲裁请求怎么写",
    "仲裁申请书怎么写",
    "仲裁申请怎么写",
    "能赔多少钱",
    "赔多少钱",
    "胜算",
    "风险",
    "流程",
    "法条怎么理解",
    "怎么理解",
    "帮我整理",
    "帮我起草",
    "帮我生成",
    "证据目录",
    "证据清单",
    "仲裁请求",
    "仲裁申请书",
)

_FACT_UPDATE_REGEXES = (
    re.compile(r"(今天|刚刚|后来).{0,12}(通知|发生|收到|回复|发来|签了|支付|转账|说了)"),
    re.compile(r"(今天|刚刚|后来).{0,12}(被辞退|辞退|解除|开除|调岗|停职|约谈)"),
    re.compile(r"(实际|真实).{0,8}(入职时间|工资|名称|身份证号|联系方式).{0,4}是"),
    re.compile(r"(找到|新增|补充).{0,8}(证据|聊天记录|工资流水|录音|合同|通知)"),
)

_QUESTION_HINTS = (
    "请问",
    "怎么办",
    "如何",
    "怎么",
    "哪些",
    "多少",
    "是否",
    "能否",
    "可否",
    "需不需要",
    "要不要",
)


def _normalize_text(text: str | None) -> str:
    return " ".join((text or "").strip().split())


def is_case_fact_update_message(text: str | None) -> bool:
    cleaned = _normalize_text(text)
    if not cleaned:
        return False

    if any(phrase in cleaned for phrase in _FACT_UPDATE_PHRASES):
        return True
    if any(pattern.search(cleaned) for pattern in _FACT_UPDATE_REGEXES):
        return True

    if is_dossier_followup_question(cleaned):
        return False

    return True


def is_dossier_followup_question(text: str | None) -> bool:
    cleaned = _normalize_text(text)
    if not cleaned:
        return False

    if any(phrase in cleaned for phrase in _FACT_UPDATE_PHRASES):
        return False
    if any(pattern.search(cleaned) for pattern in _FACT_UPDATE_REGEXES):
        return False

    if any(phrase in cleaned for phrase in _CONSULT_PHRASES):
        return True

    if "?" in cleaned or "？" in cleaned:
        return True

    return any(hint in cleaned for hint in _QUESTION_HINTS)
