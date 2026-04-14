"""
Advisor node: layered legal analysis with LLM polishing.

The deterministic calculation pipeline lives in app/logic/calculators.py.
This module contains only the LLM interaction wrapper and the post-generation
hallucination guard (sanitize_llm_answer).

Architecture contract:
  - build_claim_payload() produces the authoritative answer skeleton.
  - The LLM's only job is to improve readability (role: text editor, not lawyer).
  - sanitize_llm_answer() acts as a last-mile firewall:
      * Any fabricated significant number → full revert to payload skeleton.
      * Any phantom "第N条" citation absent from RAG → surgical strip.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.agent.graph import AgentState
from app.schemas.case_state import CaseState
from app.logic.calculators import build_claim_payload
from app.agent.guards import sanitize_llm_answer

logger = logging.getLogger(__name__)


async def generate_final_answer_v3(self, state: AgentState) -> dict[str, Any]:
    tool_results = state.get("tool_results", [])
    candidate_sources = self._extract_candidate_sources(tool_results)
    retrieved_docs = self._build_context_blocks(tool_results)
    case_summary = (state.get("case_summary") or "").strip()
    graph_context = (
        (state.get("graph_context") or "").strip()
        or (state.get("graph_memory") or "").strip()
    )
    layered_payload = build_claim_payload(
        CaseState.from_text(case_summary, state["question"], graph_context)
    )
    rag_context = retrieved_docs or "无有效检索结果"

    # ── Prompt: three-pillar hallucination prevention ──────────────────────
    advisor_system_prompt = f"""
你是一名法律文本精校员（Legal Text Editor）。你的唯一职责是：
将下方已计算完毕的「骨架文本」改写得更温暖、更具说服力、更贴近真实律师的表达风格。

你不负责计算。你不负责引用法条。你不负责补充法律知识。

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
3. sources：只能从候选来源中选择，不能编造。
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
                    "请严格遵守 <number_lock> 和 <citation_lock>，"
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

    # ── Post-generation hallucination guard ───────────────────────────────
    answer = sanitize_llm_answer(raw_answer, layered_payload, rag_context)

    sources = self._normalize_sources(parsed.get("sources", []), candidate_sources)
    return {
        "response": answer.strip(),
        "sources": sources,
        "is_final": True,
    }
