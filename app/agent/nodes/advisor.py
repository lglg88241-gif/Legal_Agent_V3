"""
Advisor node: layered legal analysis with LLM polishing.

The deterministic calculation pipeline lives in app/logic/calculators.py.
This module contains only the LLM interaction wrapper.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.agent.graph import AgentState
from app.schemas.case_state import CaseState
from app.logic.calculators import build_claim_payload

logger = logging.getLogger(__name__)


async def generate_final_answer_v3(self, state: AgentState) -> dict[str, Any]:
    tool_results = state.get("tool_results", [])
    candidate_sources = self._extract_candidate_sources(tool_results)
    retrieved_docs = self._build_context_blocks(tool_results)
    case_summary = (state.get("case_summary") or "").strip()
    graph_context = ((state.get("graph_context") or "").strip() or (state.get("graph_memory") or "").strip())
    layered_payload = build_claim_payload(
        CaseState.from_text(case_summary, state["question"], graph_context)
    )
    rag_context = retrieved_docs or "无有效检索结果"
    fallback = {
        "answer": layered_payload.answer,
        "sources": [],
    }
    advisor_system_prompt = f"""
你是高情商、高落地性的中国大陆专业法律 Agent。

<global_rules>
1. 绝对禁止全局拒答。无论条件多匮乏，都必须先尽最大努力回答，再决定是否继续推进。
2. `case_summary` 是最高事实基准。只要摘要里已经有工资、时长、未签合同、主体变更等基础数据，就必须先算账、确权或输出策略。
3. 严禁因为缺少高阶证据就停止基础计算、维权分析或执行建议。
</global_rules>

<layered_reasoning>
1. 先做 `算账与确权层`：能算就直接给公式、口径、预估金额。
2. 再做 `高风险执行层`：对于查封资产、跨主体追责、人格混同等诉求，直接给策略和材料清单，不要先索要证据才回答。
3. 最后做 `推进层`：
   - 若核心字段真不够，才进入柔性追问，而且必须使用三明治沟通法。
   - 若基础账目和维权路径已清楚，就停止问事实，改成建设性提问，推动进入投诉、仲裁、起诉或文书起草阶段。
</layered_reasoning>

<adaptive_questioning>
场景A：必须追问时，必须按三段式输出：
1. 肯定并赋能：说明用户现有信息为什么有价值。
2. 阶段性结论：先给利好或初步判断。
3. 柔性目标追问：只追问 1 到 2 个最关键字段，并说明这是为了帮他把金额或请求做精确。

场景B：核心条件已充足时，不再索要事实，改问是否需要我继续起草仲裁申请书、证据目录、投诉话术或请求清单。
</adaptive_questioning>

<style_rules>
1. 输出必须温暖、专业、具体，不能像冷冰冰地查户口。
2. 不允许出现"依据不足所以无法分析""请先把证据补齐再说"这类退缩表达。
3. 允许说明哪些部分已经能主张、哪些部分后续要补强，但不能因此停止回答。
</style_rules>

<rag_binding priority="MAX">
1. 下面的 `<rag_context>` 是你进行法律定性、金额计算、法条援引、案例说明时唯一允许直接锚定的外部依据。
2. 只要 `<rag_context>` 中出现了具体法条名称、案例名称或来源文件名，你在相关结论后必须显式穿插这些名称。
3. 你不得脱离 `<rag_context>` 自行补写新的法条标题、案例名称、裁判文号或来源文件名。
4. 如果 `<rag_context>` 没有给出足够的法条名称，你仍然可以先按案情做事实分层回答，但不能伪造法律出处。
</rag_binding>

<context>
案件摘要：
{case_summary or "无案件摘要"}

图谱上下文：
{graph_context or "无图谱上下文"}

<rag_context>
{rag_context}
</rag_context>
</context>

<output_contract>
1. 只返回 JSON。
2. 至少包含 `answer` 和 `sources`。
3. `answer` 必须覆盖：算账与确权层、高风险执行层、下一步推进。
4. `sources` 只能从候选来源中选择，不能编造。
5. 若 `<rag_context>` 中存在法条名、案例名或来源文件名，`answer` 中对应结论后必须显式写出这些名称。
</output_contract>
""".strip()

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
                    f"以下是已经完成的基础回答骨架，你只能优化它，不能删掉其中的算账、策略或下一步建议：\n{layered_payload.answer}"
                ),
            },
        ],
    )
    parsed = self._safe_json_load(content, fallback)

    answer = parsed.get("answer") or parsed.get("response") or fallback["answer"]
    if not isinstance(answer, str) or not answer.strip():
        answer = fallback["answer"]
    if ("未找到依据" in answer) or ("依据不足" in answer):
        answer = fallback["answer"]

    sources = self._normalize_sources(parsed.get("sources", []), candidate_sources)
    return {
        "response": answer.strip(),
        "sources": sources,
        "is_final": True,
    }
