from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.agent.nodes.advisor import (
    _DOSSIER_QUESTION_MEMORY,
    build_advisor_system_prompt,
)
from app.agent.nodes.researcher import vector_researcher_node
from app.agent.nodes.router import route_decision
from app.logic.calculators import build_claim_payload
from app.schemas.case_state import CaseState
from app.services import dossier_graph_tool


def _sample_payload():
    return build_claim_payload(
        CaseState.from_text(
            "我在B公司工作两年，公司一直没有给我缴纳社保。",
            "这里的当事人指的是谁？",
            "",
        )
    )


def test_classify_subject_reference_query_as_memory() -> None:
    assert dossier_graph_tool.classify_dossier_graph_query("这里的当事人指的是谁？") == "memory"
    assert dossier_graph_tool.classify_dossier_graph_query("这个公司指的是谁？") == "memory"


def test_should_query_dossier_graph_context_for_subject_reference_query() -> None:
    assert dossier_graph_tool.should_query_dossier_graph_context(
        conversation_mode="TRACKED_CASE",
        dossier_id=12,
        user_message="这里的当事人指的是谁？",
        persistence_consent="tracked",
    ) is True


def test_route_decision_uses_case_update_without_legal_rag_for_subject_reference() -> None:
    agent = SimpleNamespace()

    result = asyncio.run(
        route_decision(
            agent,
            "这里的当事人指的是谁？",
            chat_history="",
            case_summary="",
            has_dossier_context=True,
        )
    )

    assert result == {
        "route_intent": "CASE_UPDATE",
        "tool_calls": [],
    }


def test_route_decision_without_dossier_context_keeps_general_consult_path() -> None:
    provider = SimpleNamespace(value="mock")
    llm_router = SimpleNamespace(get_provider=lambda *_args, **_kwargs: provider)
    agent = SimpleNamespace(
        _patched_is_case_update=MagicMock(return_value=False),
        _patched_is_general_consult=MagicMock(return_value=True),
        _looks_like_chitchat=MagicMock(return_value=False),
        _chat_model=AsyncMock(return_value='{"route_intent": "GENERAL_CONSULT"}'),
        _safe_json_load=MagicMock(return_value={"route_intent": "GENERAL_CONSULT"}),
        _patched_build_tool_calls_for_intent=MagicMock(
            return_value=[{"name": "search_law_articles", "arguments": {"query": "这里的当事人指的是谁？"}}]
        ),
        llm_router=llm_router,
        router_llm=object(),
    )

    result = asyncio.run(
        route_decision(
            agent,
            "这里的当事人指的是谁？",
            chat_history="",
            case_summary="",
            has_dossier_context=False,
        )
    )

    assert result["route_intent"] == "GENERAL_CONSULT"
    agent._patched_build_tool_calls_for_intent.assert_called_once_with(
        "GENERAL_CONSULT",
        "这里的当事人指的是谁？",
    )


def test_vector_researcher_keeps_empty_tool_calls_for_dossier_subject_reference() -> None:
    class DummyAgent:
        def _patched_build_tool_calls_for_intent(self, route_intent, query):
            raise AssertionError("subject-reference dossier memory should not rebuild legal RAG tool calls")

        async def _execute_tool_calls(self, tool_calls):
            return []

        def _merge_tool_results(self, _existing, tool_results):
            return tool_results

    result = asyncio.run(
        vector_researcher_node(
            DummyAgent(),
            {
                "question": "这里的当事人指的是谁？",
                "current_query": "这里的当事人指的是谁？",
                "routed_query": "这里的当事人指的是谁？",
                "route_intent": "CASE_UPDATE",
                "tool_calls": [],
                "dossier_context": {"id": 12},
                "dossier_graph_context": None,
            },
        )
    )

    assert result["tool_calls"] == []
    assert result["tool_results"] == []


def test_build_advisor_system_prompt_memory_mode_adds_subject_reference_constraints() -> None:
    prompt = build_advisor_system_prompt(
        case_summary="B公司一直没有给我缴纳社保。",
        graph_context="",
        rag_context="《劳动法》第七十二条：用人单位应当依法缴纳社会保险费。",
        payload=_sample_payload(),
        question="这里的当事人指的是谁？",
        laws_block="《劳动法》第七十二条：用人单位应当依法缴纳社会保险费。",
        cases_block="某劳动争议案：裁判要点……",
        dossier_graph_context="<dossier_graph_context>已确认主体：张三</dossier_graph_context>",
        question_mode=_DOSSIER_QUESTION_MEMORY,
    )

    assert "主体指代" in prompt
    assert "先直接说明主体是谁" in prompt
    assert "尚未确认，不能自动视为同一主体" in prompt
    assert "控制在 3 到 6 句话内" in prompt
    assert "不要主动引用法条编号、案例名称" in prompt
    assert "某劳动争议案" not in prompt
