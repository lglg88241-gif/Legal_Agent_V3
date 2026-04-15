"""
Advisor node: layered legal analysis with LLM polishing.

The deterministic calculation pipeline lives in app/logic/calculators.py.
This module contains only the LLM interaction wrapper and the post-generation
hallucination guard (sanitize_llm_answer).

Architecture contract:
  - build_claim_payload() produces the authoritative answer skeleton + trace.
  - The LLM's only job is to improve readability (role: text editor, not lawyer).
  - build_advisor_system_prompt() is extracted as a pure function for testability.
  - sanitize_llm_answer() acts as a last-mile firewall:
      * Any fabricated significant number → full revert to payload skeleton.
      * Any phantom "第N条" citation absent from RAG → surgical strip.

Surgery 7: VERBATIM correction_notice block injected when overclaim detected.
"""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agent.graph import AgentState

from app.schemas.case_state import CaseState
from app.logic.calculators import PayloadResult, build_claim_payload
from app.agent.guards import (
    sanitize_hallucinated_statutes,
    sanitize_llm_answer,
    sanitize_unverified_claims,
)

logger = logging.getLogger(__name__)

_QUESTION_AMOUNT_PATTERN = re.compile(r"(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元")
_USER_STATUTE_PATTERN = re.compile(r"(《[^》]{1,40}》)(?:第\s*(\d+)\s*条)?")


def _normalize_number_token(raw: str) -> str:
    cleaned = raw.replace(",", "").replace("，", "")
    try:
        value = float(cleaned)
    except ValueError:
        return cleaned
    return str(int(value)) if value.is_integer() else cleaned


def _extract_question_amounts(question: str) -> list[str]:
    amounts: list[str] = []
    seen: set[str] = set()
    for match in _QUESTION_AMOUNT_PATTERN.finditer(question or ""):
        normalized = _normalize_number_token(match.group(1))
        if normalized in seen:
            continue
        seen.add(normalized)
        amounts.append(normalized)
    return amounts


def _build_amount_verification_block(question: str, payload: PayloadResult) -> str:
    question_amounts = _extract_question_amounts(question)
    if not question_amounts:
        return ""

    authoritative_numbers: set[str] = set()
    for step in payload.trace.steps:
        authoritative_numbers.add(_normalize_number_token(str(step.result)))
    authoritative_numbers.add(_normalize_number_token(str(payload.trace.total)))
    for notice in payload.trace.correction_notices:
        authoritative_numbers.update(
            {
                _normalize_number_token(str(notice.user_claimed)),
                _normalize_number_token(str(notice.legal_limit)),
                _normalize_number_token(str(notice.discrepancy)),
            }
        )
        authoritative_numbers.update(
            amount for amount in question_amounts if amount in notice.explanation
        )

    unverified_amounts = [amount for amount in question_amounts if amount not in authoritative_numbers]
    if not unverified_amounts:
        return ""

    joined_amounts = "、".join(f"{amount}元" for amount in unverified_amounts)
    return f"""

<amount_verification_notice priority="VERBATIM">
用户提出了具体金额主张（{joined_amounts}），但系统当前尚未产出对应的核验结果。
你必须明确说明这些金额未经系统验证，不能直接认同，也不能围绕这些未经核验的数字展开维权策略推演。
你应当把回答重心放在“还需要哪些事实才能计算”，例如月工资、工作时间、欠薪月数、赔偿项目等。
</amount_verification_notice>"""


def _extract_user_statute_references(question: str) -> list[tuple[str, str | None]]:
    references: list[tuple[str, str | None]] = []
    seen: set[tuple[str, str | None]] = set()
    for match in _USER_STATUTE_PATTERN.finditer(question or ""):
        law_name = match.group(1)
        article = f"第{match.group(2)}条" if match.group(2) else None
        item = (law_name, article)
        if item in seen:
            continue
        seen.add(item)
        references.append(item)
    return references


def _rag_contains_statute_reference(rag_context: str, law_name: str, article: str | None) -> bool:
    normalized_rag = re.sub(r"\s+", "", rag_context or "")
    if law_name not in normalized_rag:
        return False
    if not article:
        return True
    combined = f"{law_name}{article}"
    return combined in normalized_rag or article in normalized_rag


def _build_statute_verification_block(question: str, rag_context: str) -> str:
    missing_references: list[str] = []
    for law_name, article in _extract_user_statute_references(question):
        if _rag_contains_statute_reference(rag_context, law_name, article):
            continue
        missing_references.append(f"{law_name}{article or ''}")

    if not missing_references:
        return ""

    joined_references = "、".join(missing_references)
    return f"""

<statute_verification_notice priority="VERBATIM">
用户提到了以下具体法律/条文，但知识库当前未检索到对应原文：{joined_references}。
你必须明确告知知识库未收录或未检索到该法律/条文，并建议用户通过国家法律法规数据库等官方渠道核验。
严禁凭训练记忆补充条文内容，严禁把它替换映射成其他法律条文后继续作答。
</statute_verification_notice>"""


def _payload_has_authoritative_claims(payload: PayloadResult) -> bool:
    return bool(
        payload.trace.steps
        or payload.trace.correction_notices
        or payload.trace.total > 0
    )


def _build_statute_guard_rag_sources(
    tool_results: list[dict[str, Any]],
    candidate_sources: list[str],
) -> list[str]:
    """
    Build a clean RAG source list for statute guards.

    Important: do NOT use build_context_blocks() output here, because it embeds
    tool-call arguments (including the user's raw query) and would make the
    guard falsely believe a hallucinated law/article was retrieved by RAG.
    """
    fragments: list[str] = []
    seen: set[str] = set()

    for item in tool_results:
        result = item.get("result")
        if not isinstance(result, str):
            continue
        cleaned = result.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        fragments.append(cleaned)

    for source in candidate_sources:
        cleaned = source.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        fragments.append(cleaned)

    return fragments


# ─────────────────────────────────────────────────────────────────────────────
# Prompt builder (pure function — testable without LLM)
# ─────────────────────────────────────────────────────────────────────────────

def build_advisor_system_prompt(
    case_summary: str,
    graph_context: str,
    rag_context: str,
    payload: PayloadResult,
    question: str = "",
) -> str:
    """
    Build the full advisor system prompt.

    Extracted as a pure function so tests can assert on prompt content
    without invoking any LLM or async machinery.

    Surgery 7 additions:
    - <correction_notice priority="VERBATIM"> block when overclaim detected.
    - Legal-basis citation demotion guard (step.legal_basis with article
      numbers must be verified against rag_context before LLM uses them).

    Surgery 8 additions:
    - <amount_verification_notice priority="VERBATIM"> when the user quotes
      an amount the deterministic pipeline has not verified.
    - <statute_verification_notice priority="VERBATIM"> when the user cites
      a law/article absent from the retrieved RAG context.
    """
    # --- VERBATIM correction block (Surgery 7) ---
    correction_block = ""
    if payload.trace.correction_notices:
        notice_texts = "\n".join(
            f"【{n.item}纠错】{n.explanation}"
            for n in payload.trace.correction_notices
        )
        correction_block = f"""

<correction_notice priority="VERBATIM">
以下纠错说明由后端程序依据法定规则生成，数字经过精确计算，内容权威可靠。
你必须在回答的「算账与确权层」中原样转述以下内容，不得修改、删减、重新表述或重新计算：
{notice_texts}
转述后，继续完成其余层次的分析。
</correction_notice>"""

    # --- Legal basis citation demotion guard (Surgery 7) ---
    legal_basis_block = ""
    step_bases = [s.legal_basis for s in payload.trace.steps if s.legal_basis]
    if step_bases:
        legal_basis_block = """

<citation_demotion_guard>
计算轨迹中包含法律依据名称（如《劳动合同法》相关规定）。
规则：法律依据名称你可以直接使用；但如果你想补充具体条文编号（如"第82条"），
必须先确认该编号已在 <rag_context> 中原文出现。
若未出现，只写法律名称，不写条文编号。
</citation_demotion_guard>"""

    amount_verification_block = _build_amount_verification_block(question, payload)
    statute_verification_block = _build_statute_verification_block(question, rag_context)

    prompt = f"""
你是一名法律文本精校员（Legal Text Editor）。你的唯一职责是：
将下方已计算完毕的「骨架文本」改写得更温暖、更具说服力、更贴近真实律师的表达风格。

你不负责计算。你不负责引用法条。你不负责补充法律知识。
{correction_block}
<number_lock>
【数字锁定 — 最高优先级，任何其他规则均不得推翻此条】
骨架文本中的所有数字（元、月数、总额）是经过程序精确计算后写入的权威值。
你唯一允许的操作是：原样保留这些数字，在其前后调整连接文字和语气。
严禁行为：
- 重新计算任何数字
- 验证或"纠正"任何数字
- 添加乘法公式或推导过程（如"8000×6=48000"）
- 修改任何数值，哪怕你认为更精确
即使骨架文本中的数字与你的训练知识不一致，也必须原样保留。
</number_lock>

<citation_lock>
【引用锁定 — 次高优先级】
法条条文编号（"第X条"格式）只允许出现以下两种情况之一：
  A. 该编号已在下方 <rag_context> 中原文出现。
  B. 该编号已在骨架文本中存在。
任何其他情况：严禁补写任何"第X条"。
若你想引用某部法律，只写法律名称（如《劳动合同法》），不写条文编号。
原因：条文编号必须由检索系统提供，你的训练记忆不足以保证准确性。
</citation_lock>
{legal_basis_block}
{amount_verification_block}
{statute_verification_block}
<amount_guard>
当用户主动提出具体赔偿金额（如“我能要999999元吗”）时，只有该金额已经被骨架文本或 <correction_notice> 验证，你才能正面讨论它。
如果系统尚未产出对应的验证结果，你不得认同、背书或围绕该金额展开策略推演。
此时必须明确说明金额尚未核验，并引导用户补充月工资、工作时间、欠薪月数、赔偿项目等计算事实。
</amount_guard>

<statute_guard>
当用户引用具体法律名称或条文编号时，只有在 <rag_context> 中检索到同一法律或条文后才可引用。
如果 <rag_context> 未检索到用户提到的法律或条文，必须明确告知知识库未收录或未检索到，并建议用户通过国家法律法规数据库等官方渠道查证。
严禁凭训练记忆回答用户提问的具体法条内容，严禁拿其他法律条文“代答”。
</statute_guard>
<editor_role>
你被允许做的事：
1. 调整语气——让文本更温暖、更有信心、更像真正的律师在说话。
2. 优化连接词——让各层之间过渡更自然。
3. 适当扩展行动建议的表达——但不能添加新的法律主张或新的金额。
4. 保持原有的四层结构：已知事实 → 算账与确权层 → 高风险执行层 → 下一步落地动作。

你被严格禁止做的事：
1. 修改任何数字。
2. 添加骨架中没有的法条编号。
3. 删除骨架中的任何一层。
4. 添加骨架中没有的新法律主张（如新的赔偿项目）。
</editor_role>

<rag_binding>
下方 <rag_context> 是你唯一可以引用法条名称、案例名称、来源文件名的外部依据。
若 <rag_context> 中出现了具体法律名称或案例名，可在改写时自然穿插，增加说服力。
若 <rag_context> 没有提供编号，绝对不要自行补写编号。
</rag_binding>

<output_contract>
1. 只返回 JSON，格式：{{"answer": "...", "sources": [...]}}
2. answer：对骨架文本的精校版本，必须保留四层结构和全部数字。
3. 若存在 <correction_notice>，其内容必须出现在 answer 的「算账与确权层」中。
4. 若存在 <amount_verification_notice>，answer 必须明确说明金额尚未核验，并引导用户补充计算事实。
5. 若存在 <statute_verification_notice>，answer 必须明确说明知识库未检索到对应法律/条文，并建议用户通过官方渠道核验。
6. sources：只能从候选来源中选择，不能编造。
</output_contract>

<context>
案件摘要：{case_summary or "无案件摘要"}
图谱上下文：{graph_context or "无图谱上下文"}
<rag_context>
{rag_context}
</rag_context>
</context>

<examples>
【示例：正确改写】
骨架片段：二倍工资差额约 48000 元（8000 × 6 个月）
正确输出：您有权主张约 48000 元的二倍工资差额
说明：数字原样保留，仅改变表达方式，没有重新推导。

【示例：错误改写（被严格禁止）】
骨架片段：二倍工资差额约 48000 元
错误输出：依据《劳动合同法》第82条，月薪8000 × 6 = 48000 元
错误原因：① 脑补了"第82条"（RAG 无此编号）；② 添加了乘法公式推导过程。
</examples>
""".strip()

    return prompt


# ─────────────────────────────────────────────────────────────────────────────
# Node function
# ─────────────────────────────────────────────────────────────────────────────

async def generate_final_answer_v3(self, state: AgentState) -> dict[str, Any]:
    tool_results = state.get("tool_results", [])
    candidate_sources = self._extract_candidate_sources(tool_results)
    retrieved_docs = self._build_context_blocks(tool_results)
    statute_guard_rag_sources = _build_statute_guard_rag_sources(tool_results, candidate_sources)
    case_summary = (state.get("case_summary") or "").strip()
    graph_context = (
        (state.get("graph_context") or "").strip()
        or (state.get("graph_memory") or "").strip()
    )
    layered_payload = build_claim_payload(
        CaseState.from_text(case_summary, state["question"], graph_context)
    )
    rag_context = retrieved_docs or "无有效检索结果"

    advisor_system_prompt = build_advisor_system_prompt(
        question=state["question"],
        case_summary=case_summary,
        graph_context=graph_context,
        rag_context=rag_context,
        payload=layered_payload,
    )

    fallback = {"answer": layered_payload.answer, "sources": []}

    content = await self._chat_model(
        self.expert_llm,
        [
            {"role": "system", "content": advisor_system_prompt},
            {
                "role": "user",
                "content": (
                    f"用户问题：{state['question']}\n\n"
                    f"候选来源：{json.dumps(candidate_sources, ensure_ascii=False)}\n\n"
                    f"<rag_context>\n{rag_context}\n</rag_context>\n\n"
                    "以下是已经完成的基础回答骨架。"
                    "请严格遵守 <number_lock>、<citation_lock> 以及所有 verification notice，"
                    "只改措辞，不改数字，不补法条编号：\n"
                    f"{layered_payload.answer}"
                ),
            },
        ],
    )

    parsed = self._safe_json_load(content, fallback)
    raw_answer = parsed.get("answer") or parsed.get("response") or fallback["answer"]

    if not isinstance(raw_answer, str) or not raw_answer.strip():
        raw_answer = fallback["answer"]
    if ("未找到依据" in raw_answer) or ("依据不足" in raw_answer):
        raw_answer = fallback["answer"]

    # ── Post-generation hallucination guard (Surgery 6+7) ─────────────────
    answer = sanitize_llm_answer(raw_answer, layered_payload, rag_context)
    payload_for_claim_guard = layered_payload if _payload_has_authoritative_claims(layered_payload) else None
    answer = sanitize_unverified_claims(answer, state["question"], payload_for_claim_guard)
    answer = sanitize_hallucinated_statutes(
        answer,
        state["question"],
        statute_guard_rag_sources,
    )

    sources = self._normalize_sources(parsed.get("sources", []), candidate_sources)
    return {
        "response": answer.strip(),
        "sources": sources,
        "is_final": True,
    }
