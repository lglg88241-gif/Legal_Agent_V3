from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.services.agentic_tool_executor import execute_agentic_plan_tools


def _tool_names(result: dict[str, Any]) -> list[str]:
    return [
        str(item.get("tool_name") or "")
        for item in result.get("tool_results", [])
        if isinstance(item, dict)
    ]


def _assert_native_tool_result_shape(tool_result: dict[str, Any]) -> None:
    assert {
        "tool_name",
        "status",
        "input_summary",
        "output_summary",
        "latency_ms",
        "error",
        "side_effect",
        "registry_category",
    }.issubset(tool_result)
    assert isinstance(tool_result["latency_ms"], int)
    assert tool_result["latency_ms"] >= 0


def test_execute_subject_reference_plan_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.agentic_tool_executor as tool_executor_module

    async def fake_build_subject_reference_response_text(**kwargs):
        assert kwargs["question"] == "这里的公司指的是哪家公司？"
        assert kwargs["current_conversation_id"] == 9
        return (
            "你这里说的“公司”指 B公司。",
            0.93,
            {
                "selected_scope": "current_conversation_graph",
                "used_evidence": [{"graph_node_label": "B公司"}],
            },
        )

    monkeypatch.setattr(
        tool_executor_module,
        "build_subject_reference_response_text_async",
        fake_build_subject_reference_response_text,
    )

    plan = {
        "tool_sequence": [
            {
                "tool_name": "current_conversation_graph_query",
                "arguments": {"conversation_id": 9},
                "dispatch_mode": "planner_owned",
            },
            {
                "tool_name": "current_conversation_messages_query",
                "arguments": {"conversation_id": 9, "limit": 12},
                "dispatch_mode": "planned_contract",
            },
            {
                "tool_name": "dossier_graph_context_query",
                "arguments": {"conversation_id": 9, "dossier_id": 12, "query": "这里的公司指的是哪家公司？"},
                "dispatch_mode": "planner_owned",
            },
            {
                "tool_name": "subject_reference_resolve",
                "arguments": {"question": "这里的公司指的是哪家公司？"},
                "dispatch_mode": "planner_owned",
            },
            {
                "tool_name": "memory_answer_compose",
                "arguments": {"question": "这里的公司指的是哪家公司？"},
                "dispatch_mode": "specialist_owned",
            },
        ]
    }
    context = {
        "question": "这里的公司指的是哪家公司？",
        "conversation_id": 9,
        "message_understanding": {
            "intent": "subject_reference",
            "route_hint": "DOSSIER_MEMORY",
            "subject_reference": {
                "enabled": True,
                "mention": "公司",
                "target_type": "organization",
            },
        },
        "current_conversation_graph": {
            "nodes": [{"id": "org-b", "label": "B公司"}],
            "edges": [],
        },
        "dossier_graph_context": {"graph_nodes": [], "matched_edges": []},
        "recent_messages": [
            {"message_id": 71, "role": "user", "content": "我在 B公司 上班。"},
        ],
        "case_summary": "旧摘要",
    }

    result = asyncio.run(execute_agentic_plan_tools(plan, context))

    assert _tool_names(result) == [
        "current_conversation_graph_query",
        "current_conversation_messages_query",
        "dossier_graph_context_query",
        "subject_reference_resolve",
        "memory_answer_compose",
    ]
    assert result["final_result"]["response"] == "你这里说的“公司”指 B公司。"
    assert result["memory_resolution"]["selected_scope"] == "current_conversation_graph"
    assert result["recent_messages"] == context["recent_messages"]
    for tool_result in result["tool_results"]:
        assert isinstance(tool_result, dict)
        _assert_native_tool_result_shape(tool_result)
        assert tool_result["status"] == "success"


def test_execute_out_of_scope_plan_skips_retrieval() -> None:
    plan = {
        "route": "OUT_OF_SCOPE",
        "intent": "unknown",
        "final_response_strategy": "fixed_out_of_scope_answer",
        "tool_sequence": [
            {
                "tool_name": "out_of_scope_answer_compose",
                "arguments": {"question": "美国联邦法第44条规定的什么？"},
                "dispatch_mode": "specialist_owned",
            }
        ],
    }
    context = {
        "question": "美国联邦法第44条规定的什么？",
        "message_understanding": {
            "intent": "unknown",
            "route_hint": "OUT_OF_SCOPE",
        },
    }

    result = asyncio.run(execute_agentic_plan_tools(plan, context))

    assert _tool_names(result) == ["out_of_scope_answer_compose"]
    assert result["final_result"]["route_intent"] == "OUT_OF_SCOPE"
    assert "中国法律领域" in result["final_result"]["response"]
    assert result["tool_results"][0]["details"]["retrieval_skipped"] is True


def test_execute_legal_question_plan_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.agentic_tool_executor as tool_executor_module

    async def fake_search_law_articles(*, question: str, chat_history: str | None):
        assert "第八十七条" in question
        return [
            {
                "page_content": "用人单位违法解除劳动合同的，应当支付赔偿金。",
                "metadata": {"_exact_lookup": True, "article": "第八十七条"},
            }
        ]

    async def fake_legal_answer_handler(user_id, session_id, question, **kwargs):
        assert user_id == 1
        assert session_id == "session-1"
        assert "第八十七条" in question
        return {
            "response": "劳动合同法第八十七条主要规定违法解除或终止劳动合同的赔偿责任。",
            "sources": [],
            "route_intent": "GENERAL_CONSULT",
            "case_summary": "",
            "confidence_score": 0.91,
        }

    monkeypatch.setattr(
        tool_executor_module,
        "_execute_search_law_articles",
        fake_search_law_articles,
    )

    plan = {
        "tool_sequence": [
            {
                "tool_name": "exact_law_lookup",
                "arguments": {"query": "劳动合同法第八十七条规定了什么？"},
                "dispatch_mode": "planner_owned",
            },
            {
                "tool_name": "legal_answer_compose",
                "arguments": {"question": "劳动合同法第八十七条规定了什么？"},
                "dispatch_mode": "specialist_owned",
            },
        ]
    }
    context = {
        "user_id": 1,
        "session_id": "session-1",
        "question": "劳动合同法第八十七条规定了什么？",
        "chat_history": "",
        "case_summary": "",
        "graph_memory": "",
        "conversation_id": 9,
        "saved_case_state": None,
        "is_precise_mode": True,
        "proactive_graph_mode": True,
        "advisor_dossier_context": None,
        "advisor_dossier_graph_summary": None,
        "advisor_dossier_graph_context": None,
        "legal_answer_handler": fake_legal_answer_handler,
    }

    result = asyncio.run(execute_agentic_plan_tools(plan, context))

    assert _tool_names(result) == ["exact_law_lookup", "legal_answer_compose"]
    assert result["final_result"]["response"].startswith("劳动合同法第八十七条")
    for tool_result in result["tool_results"]:
        assert isinstance(tool_result, dict)
        _assert_native_tool_result_shape(tool_result)
        assert tool_result["status"] == "success"
    compose_result = result["tool_results"][1]
    assert compose_result["registry_category"] == "composition"
    assert compose_result["output_summary"]


def test_execute_fact_update_plan_blocks_write_tools_but_runs_acknowledgement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.agentic_tool_executor as tool_executor_module

    def fake_build_case_update_acknowledgement(**kwargs):
        assert kwargs["intent"] == "fact_update"
        assert kwargs["route_intent"] == "CASE_UPDATE"
        return {
            "text": "已记录这条新事实：王经理口头通知你不用来了。",
            "trace": {"mode": "rules"},
            "fallback_used": False,
            "provider": "rules",
            "model": "case-update-ack-rules-v1",
        }

    monkeypatch.setattr(
        tool_executor_module,
        "build_case_update_acknowledgement",
        fake_build_case_update_acknowledgement,
    )

    plan = {
        "tool_sequence": [
            {
                "tool_name": "case_fact_extract",
                "arguments": {"text": "B公司的人事王经理口头通知我不用来了。"},
                "dispatch_mode": "planned_contract",
            },
            {
                "tool_name": "case_summary_update",
                "arguments": {"conversation_id": 9, "case_summary": "B公司的人事王经理口头通知我不用来了。"},
                "dispatch_mode": "planned_contract",
            },
            {
                "tool_name": "schedule_graph_update",
                "arguments": {"conversation_id": 9, "user_id": 1},
                "dispatch_mode": "planned_contract",
            },
            {
                "tool_name": "acknowledgement_compose",
                "arguments": {"intent": "fact_update"},
                "dispatch_mode": "specialist_owned",
            },
        ]
    }
    context = {
        "question": "B公司的人事王经理口头通知我不用来了。",
        "conversation_id": 9,
        "dossier_id": 12,
        "message_understanding": {"intent": "fact_update"},
        "execution_plan": {"primary_route": "CASE_UPDATE"},
        "graph_update_scheduled": False,
        "graph_update_actions": None,
        "persisted_fact_text": "B公司的人事王经理口头通知我不用来了。",
        "persistence_consent": "tracked",
    }

    result = asyncio.run(execute_agentic_plan_tools(plan, context))
    tool_results = result["tool_results"]

    assert _tool_names(result) == [
        "case_fact_extract",
        "case_summary_update",
        "schedule_graph_update",
        "acknowledgement_compose",
    ]
    assert tool_results[0]["status"] == "skipped"
    assert tool_results[0]["blocked_reason"] == "tool_not_planner_callable"
    assert tool_results[1]["status"] == "blocked"
    assert tool_results[1]["side_effect"] == "db_write:conversation_case_summary"
    assert tool_results[2]["status"] == "blocked"
    assert tool_results[2]["side_effect"] == "async_write:neo4j_case_graph"
    assert tool_results[3]["status"] == "success"
    assert result["final_result"]["text"].startswith("已记录这条新事实")
    for tool_result in tool_results:
        assert isinstance(tool_result, dict)
        _assert_native_tool_result_shape(tool_result)
