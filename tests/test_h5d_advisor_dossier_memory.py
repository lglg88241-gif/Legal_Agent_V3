from __future__ import annotations

import asyncio
import json

from app.agent.nodes.advisor import (
    _DOSSIER_QUESTION_DEFAULT,
    _DOSSIER_QUESTION_MEMORY,
    build_advisor_system_prompt,
)
from app.logic.calculators import build_claim_payload
from app.schemas.case_state import CaseState
from app.services import dossier_graph_tool, legal_agent


def _sample_payload():
    return build_claim_payload(
        CaseState.from_text(
            "我在A公司工作两年，公司未签劳动合同，后来通知解除。",
            "这个案卷里之前提到的公司是谁？",
            "",
        )
    )


def _sample_dossier_graph_context() -> dict[str, object]:
    return {
        "status": "success",
        "source": "graph_context",
        "legal_basis": False,
        "query_type": "keyword",
        "query": "这个案卷里之前提到的公司是谁？",
        "scope": {"user_id": 7, "dossier_id": 12, "conversation_id": 88},
        "stats": {"conversation_count": 2, "node_count": 3, "edge_count": 1},
        "participants": [
            {
                "label": "A公司",
                "role": "用人单位",
                "party_side": "opponent",
                "entity_type": "organization",
            }
        ],
        "participant_mentions": [
            {"raw_label": "公司", "participant_label": "A公司", "source": "graph"}
        ],
        "matched_nodes": [
            {
                "id": "node-company",
                "display_label": "A公司",
                "raw_label": "公司",
                "label": "公司",
                "type": "organization",
                "conversation_id": 88,
            }
        ],
        "matched_edges": [],
        "warnings": [],
        "notes": ["graph_context 仅代表案卷结构化背景，不构成法律依据。"],
        "truncated": False,
    }


def test_should_query_dossier_graph_context_returns_true_for_dossier_advice_query() -> None:
    assert dossier_graph_tool.should_query_dossier_graph_context(
        conversation_mode="TRACKED_CASE",
        dossier_id=12,
        user_message="根据这个案子我下一步准备什么证据？",
        persistence_consent="tracked",
    ) is True


def test_build_advisor_system_prompt_memory_mode_drops_irrelevant_rag_blocks() -> None:
    prompt = build_advisor_system_prompt(
        case_summary="A公司通知解除劳动合同。",
        graph_context="",
        rag_context="某刑法案例：污染环境罪的量刑规则。",
        payload=_sample_payload(),
        question="这个案卷里之前提到的公司是谁？",
        laws_block="《刑法》第三百三十八条：污染环境罪。",
        cases_block="某化工公司污染环境案：裁判要点……",
        dossier_graph_context="<dossier_graph_context>已确认主体：A公司</dossier_graph_context>",
        question_mode=_DOSSIER_QUESTION_MEMORY,
    )

    assert "当前问题属于案卷记忆查询，本轮不强制使用法律检索结果。" in prompt
    assert "不要主动引用法条编号、案例名称或展开法律 RAG 内容" in prompt
    assert "某化工公司污染环境案" not in prompt
    assert "污染环境罪" not in prompt
    assert "必须明确引用法条编号" not in prompt


def test_build_advisor_system_prompt_default_mode_keeps_law_rag_contract() -> None:
    prompt = build_advisor_system_prompt(
        case_summary="公司违法解除劳动合同。",
        graph_context="",
        rag_context="《劳动合同法》第八十七条：违法解除劳动合同的赔偿规则。",
        payload=_sample_payload(),
        question="劳动合同法第八十七条规定了什么？",
        laws_block="《劳动合同法》第八十七条：用人单位违法解除劳动合同的，应当支付赔偿金。",
        question_mode=_DOSSIER_QUESTION_DEFAULT,
    )

    assert "必须明确引用法条编号" in prompt
    assert "《劳动合同法》第八十七条" in prompt


def test_generate_final_answer_uses_memory_mode_prompt_for_dossier_memory_question(
    monkeypatch,
) -> None:
    agent = legal_agent.LegalGraphAgent()
    captured: dict[str, str] = {}

    async def fake_chat_model(*args, **kwargs):
        messages = args[1]
        captured["system"] = messages[0]["content"]
        captured["user"] = messages[1]["content"]
        return '{"answer":"根据当前案卷已记录的信息，对方主体是 A公司。","sources":[]}'

    monkeypatch.setattr(agent, "_chat_model", fake_chat_model)

    tool_results = [
        {
            "result": json.dumps(
                [
                    {
                        "content": "《刑法》第三百三十八条：污染环境罪。",
                        "source": "law-db",
                        "metadata": {"law_title": "《刑法》", "article": "第三百三十八条"},
                    },
                    {
                        "content": "某化工公司污染环境案：裁判要点……",
                        "source": "case-db",
                        "metadata": {"case_name": "某化工公司污染环境案"},
                    },
                ],
                ensure_ascii=False,
            )
        }
    ]

    result = asyncio.run(
        agent._generate_final_answer(
            {
                "question": "这个案卷里之前提到的公司是谁？",
                "case_summary": "我在A公司工作两年，公司未签劳动合同，后来通知解除。",
                "graph_context": "",
                "graph_memory": "",
                "dossier_context": {"id": 12, "title": "劳动争议案卷"},
                "dossier_graph_context": _sample_dossier_graph_context(),
                "tool_results": tool_results,
                "confidence_score": 0.0,
            }
        )
    )

    assert result["response"].startswith("根据当前案卷已记录的信息")
    assert "当前问题属于案卷记忆查询" in captured["user"]
    assert "某化工公司污染环境案" not in captured["system"]
    assert "污染环境罪" not in captured["system"]
