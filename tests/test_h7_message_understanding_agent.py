from __future__ import annotations

import asyncio
import json
import sys
from types import ModuleType

import pytest

if "json_repair" not in sys.modules:
    fake_json_repair = ModuleType("json_repair")
    fake_json_repair.repair_json = lambda content: content
    sys.modules["json_repair"] = fake_json_repair

from app.services import message_understanding_agent


def _run(coro):
    return asyncio.run(coro)


def test_understand_message_with_agent_consumes_subject_reference_llm_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_message_understanding_llm(*, messages, provider_hint=None):
        assert messages[1]["content"]
        return {
            "provider": "openai",
            "model": "gpt-5.1",
            "content": json.dumps(
                {
                    "intent": "subject_reference",
                    "route_hint": "DOSSIER_MEMORY",
                    "needs_legal_rag": False,
                    "needs_dossier_graph_context": True,
                    "needs_structured_persistence": False,
                    "needs_graph_update": False,
                    "subject_reference": {
                        "enabled": True,
                        "target_span": "这里的公司",
                        "mention": "公司",
                        "target_type": "organization",
                        "question": "这里的公司指的是哪家公司？",
                        "resolution_strategy": "use_dossier_context",
                    },
                    "confidence": "high",
                    "reason": "This is a dossier subject reference question.",
                },
                ensure_ascii=False,
            ),
        }

    monkeypatch.setattr(
        message_understanding_agent,
        "_call_message_understanding_llm",
        fake_call_message_understanding_llm,
    )

    result = _run(
        message_understanding_agent.understand_message_with_agent(
            message="这里的公司指的是哪家公司？",
            dossier_id=88,
            recent_messages=[
                {"message_id": 11, "role": "user", "content": "我在B公司工作。"},
                {"message_id": 12, "role": "assistant", "content": "继续补充。"},
            ],
        )
    )

    assert result["intent"] == "subject_reference"
    assert result["route_hint"] == "DOSSIER_MEMORY"
    assert result["needs_legal_rag"] is False
    assert result["needs_dossier_graph_context"] is True
    assert result["needs_graph_update"] is False
    assert result["provider"] == "openai"
    assert result["fallback_reason"] is None
    assert result["subject_reference"]["mention"] == "公司"
    assert result["trace"]["steps"] == [
        "build_request",
        "build_prompt",
        "call_llm",
        "parse_llm_json",
        "normalize_result",
    ]


def test_understand_message_with_agent_consumes_fact_update_llm_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_message_understanding_llm(*, messages, provider_hint=None):
        return {
            "provider": "zhipu",
            "model": "glm-4-flash",
            "content": json.dumps(
                {
                    "intent": "fact_update",
                    "route_hint": "CASE_UPDATE",
                    "needs_legal_rag": False,
                    "needs_dossier_graph_context": False,
                    "needs_structured_persistence": True,
                    "needs_graph_update": True,
                    "fact_update": {
                        "enabled": True,
                        "fact_text": "B公司的人事通知我去签确认书。而且人事王经理口头通知我不用来了。",
                        "entities": [
                            {"label": "B公司", "type": "organization"},
                            {"label": "王经理", "type": "person"},
                            {"label": "确认书", "type": "document"},
                        ],
                        "events": [
                            {
                                "summary": "王经理口头通知当事人不用来了",
                                "participants": ["王经理", "当事人"],
                            }
                        ],
                    },
                    "confidence": "high",
                    "reason": "The message adds new case facts.",
                },
                ensure_ascii=False,
            ),
        }

    monkeypatch.setattr(
        message_understanding_agent,
        "_call_message_understanding_llm",
        fake_call_message_understanding_llm,
    )

    result = _run(
        message_understanding_agent.understand_message_with_agent(
            message="B公司的人事通知我去签确认书。而且人事王经理口头通知我不用来了。",
            dossier_id=88,
        )
    )

    assert result["intent"] == "fact_update"
    assert result["route_hint"] == "CASE_UPDATE"
    assert result["needs_structured_persistence"] is True
    assert result["needs_graph_update"] is True
    assert result["provider"] == "zhipu"
    assert any(entity["label"] == "王经理" for entity in result["fact_update"]["entities"])
    assert result["fact_update"]["events"][0]["summary"] == "王经理口头通知当事人不用来了"


def test_understand_message_with_agent_consumes_confirm_previous_fact_llm_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_message_understanding_llm(*, messages, provider_hint=None):
        return {
            "provider": "openai",
            "model": "gpt-5.1",
            "content": json.dumps(
                {
                    "intent": "confirm_previous_fact",
                    "route_hint": "CASE_UPDATE",
                    "needs_legal_rag": False,
                    "needs_dossier_graph_context": False,
                    "needs_structured_persistence": True,
                    "needs_graph_update": True,
                    "previous_fact_confirmation": {
                        "enabled": True,
                        "action": "confirm",
                        "confirmation_text": "我确认这个事实",
                        "target_message_id": 123,
                        "target_text": "B公司的人事通知我去签确认书。而且人事王经理口头通知我不用来了。",
                        "target_fact_summary": "王经理口头通知当事人不用来了",
                    },
                    "confidence": "high",
                    "reason": "The user confirms the previous factual message.",
                },
                ensure_ascii=False,
            ),
        }

    monkeypatch.setattr(
        message_understanding_agent,
        "_call_message_understanding_llm",
        fake_call_message_understanding_llm,
    )

    result = _run(
        message_understanding_agent.understand_message_with_agent(
            message="我确认这个事实",
            recent_messages=[
                {
                    "message_id": 123,
                    "role": "user",
                    "content": "B公司的人事通知我去签确认书。而且人事王经理口头通知我不用来了。",
                }
            ],
            dossier_id=88,
        )
    )

    assert result["intent"] == "confirm_previous_fact"
    assert result["needs_legal_rag"] is False
    assert result["needs_graph_update"] is True
    assert "王经理" in result["previous_fact_confirmation"]["target_text"]
    assert result["previous_fact_confirmation"]["target_message_id"] == 123


def test_understand_message_with_agent_consumes_legal_question_llm_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_message_understanding_llm(*, messages, provider_hint=None):
        return {
            "provider": "openai",
            "model": "gpt-5.1",
            "content": json.dumps(
                {
                    "intent": "legal_question",
                    "route_hint": "LEGAL_QA",
                    "needs_legal_rag": True,
                    "needs_dossier_graph_context": False,
                    "needs_structured_persistence": False,
                    "needs_graph_update": False,
                    "legal_question": {
                        "enabled": True,
                        "question": "劳动合同法第八十七条规定了什么？",
                    },
                    "confidence": "high",
                    "reason": "The user asks for a law article explanation.",
                },
                ensure_ascii=False,
            ),
        }

    monkeypatch.setattr(
        message_understanding_agent,
        "_call_message_understanding_llm",
        fake_call_message_understanding_llm,
    )

    result = _run(
        message_understanding_agent.understand_message_with_agent(
            message="劳动合同法第八十七条规定了什么？",
            dossier_id=None,
        )
    )

    assert result["intent"] == "legal_question"
    assert result["route_hint"] == "LEGAL_QA"
    assert result["needs_legal_rag"] is True
    assert result["needs_graph_update"] is False
    assert result["legal_question"]["question"] == "劳动合同法第八十七条规定了什么？"


def test_understand_message_overrides_misread_labor_law_article_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_message_understanding_llm(*, messages, provider_hint=None):
        return {
            "provider": "openai",
            "model": "gpt-5.1",
            "content": json.dumps(
                {
                    "intent": "advice_question",
                    "route_hint": "GENERAL_CONSULT",
                    "needs_legal_rag": False,
                    "needs_dossier_graph_context": False,
                    "needs_structured_persistence": False,
                    "needs_graph_update": False,
                    "advice_question": {
                        "enabled": True,
                        "question": "劳动法是什么？",
                    },
                    "confidence": "medium",
                    "reason": "Misread the article number as a generic labor law question.",
                },
                ensure_ascii=False,
            ),
        }

    monkeypatch.setattr(
        message_understanding_agent,
        "_call_message_understanding_llm",
        fake_call_message_understanding_llm,
    )

    result = _run(
        message_understanding_agent.understand_message_with_agent(
            message="劳动法87条是什么？",
            dossier_id=None,
        )
    )

    assert result["intent"] == "legal_question"
    assert result["route_hint"] == "LEGAL_QA"
    assert result["needs_legal_rag"] is True
    assert result["needs_graph_update"] is False
    assert result["legal_question"]["question"] == "劳动法87条是什么？"
    assert "override_explicit_law_article_question" in result["trace"]["warnings"]


def test_understand_message_routes_foreign_law_article_out_of_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_message_understanding_llm(*, messages, provider_hint=None):
        return {
            "provider": "openai",
            "model": "gpt-5.1",
            "content": json.dumps(
                {
                    "intent": "legal_question",
                    "route_hint": "LEGAL_QA",
                    "needs_legal_rag": True,
                    "legal_question": {
                        "enabled": True,
                        "question": "美国联邦法第44条规定的什么？",
                    },
                    "confidence": "high",
                    "reason": "The user asks for a law article explanation.",
                },
                ensure_ascii=False,
            ),
        }

    monkeypatch.setattr(
        message_understanding_agent,
        "_call_message_understanding_llm",
        fake_call_message_understanding_llm,
    )

    result = _run(
        message_understanding_agent.understand_message_with_agent(
            message="美国联邦法第44条规定的什么？",
            dossier_id=None,
        )
    )

    assert result["intent"] == "unknown"
    assert result["route_hint"] == "OUT_OF_SCOPE"
    assert result["needs_legal_rag"] is False
    assert result["needs_graph_update"] is False
    assert result["legal_question"] is None
    assert "override_out_of_scope:FOREIGN_LAW" in result["trace"]["warnings"]


def test_understand_message_with_agent_returns_honest_fallback_for_invalid_llm_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_message_understanding_llm(*, messages, provider_hint=None):
        return {
            "provider": "openai",
            "model": "gpt-5.1",
            "content": "not-json-at-all",
        }

    monkeypatch.setattr(
        message_understanding_agent,
        "_call_message_understanding_llm",
        fake_call_message_understanding_llm,
    )

    result = _run(
        message_understanding_agent.understand_message_with_agent(
            message="我确认这个事实",
            recent_messages=[],
        )
    )

    assert isinstance(result, dict)
    assert result["provider"] == "fallback"
    assert result["fallback_reason"] == "invalid_llm_json"
    assert result["intent"] == "unknown"
    assert "invalid_llm_json" in result["trace"]["warnings"]
    assert result["trace"]["steps"][-1] == "apply_fallback"


def test_understand_message_with_agent_does_not_fabricate_previous_fact_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_message_understanding_llm(*, messages, provider_hint=None):
        return {
            "provider": "openai",
            "model": "gpt-5.1",
            "content": json.dumps(
                {
                    "intent": "confirm_previous_fact",
                    "route_hint": "CASE_UPDATE",
                    "needs_legal_rag": False,
                    "needs_dossier_graph_context": False,
                    "needs_structured_persistence": True,
                    "needs_graph_update": True,
                    "previous_fact_confirmation": {
                        "enabled": True,
                        "action": "unclear",
                        "confirmation_text": "我确认这个事实",
                        "target_message_id": None,
                        "target_text": None,
                        "target_fact_summary": None,
                    },
                    "confidence": "low",
                    "reason": "no_previous_fact_found",
                },
                ensure_ascii=False,
            ),
        }

    monkeypatch.setattr(
        message_understanding_agent,
        "_call_message_understanding_llm",
        fake_call_message_understanding_llm,
    )

    result = _run(
        message_understanding_agent.understand_message_with_agent(
            message="我确认这个事实",
            recent_messages=[],
            dossier_id=88,
        )
    )

    assert result["intent"] == "confirm_previous_fact"
    assert result["previous_fact_confirmation"]["target_text"] is None
    assert result["previous_fact_confirmation"]["target_fact_summary"] is None
    assert result["reason"] == "no_previous_fact_found"
    assert "no_previous_fact_found" in result["trace"]["warnings"]


def test_understand_message_with_agent_detects_pending_subject_reference_clarification_without_llm() -> None:
    result = _run(
        message_understanding_agent.understand_message_with_agent(
            message="是B公司",
            recent_messages=[
                {
                    "message_id": 12,
                    "role": "assistant",
                    "content": "当前还不能稳定确定“公司”具体指向哪个主体。",
                    "metadata": {
                        "pending_clarification": {
                            "type": "subject_reference_resolution",
                            "target_mention": "公司",
                            "reference_type": "organization",
                            "previous_question": "这里的公司指的是哪家公司？",
                            "scope_attempted": ["current_conversation_graph"],
                            "expected_response": "entity_name_or_context",
                        }
                    },
                }
            ],
            use_llm=False,
        )
    )

    assert result["intent"] == "clarification_response"
    assert result["route_hint"] == "DOSSIER_MEMORY"
    assert result["provider"] == "contract"
    assert result["model"] == "pending-clarification-contract-v1"
    assert result["fallback_reason"] is None
    assert result["clarification_response"]["target_mention"] == "公司"
    assert result["trace"]["steps"] == ["build_request", "detect_pending_clarification"]


def test_pending_subject_reference_does_not_swallow_fact_memory_question_without_llm() -> None:
    result = _run(
        message_understanding_agent.understand_message_with_agent(
            message="李四是谁",
            recent_messages=[
                {
                    "message_id": 12,
                    "role": "assistant",
                    "content": "当前还不能稳定确定“公司”具体指向哪个主体。",
                    "metadata": {
                        "pending_clarification": {
                            "type": "subject_reference_resolution",
                            "target_mention": "蓝海科技",
                            "reference_type": "organization",
                            "previous_question": "这里的蓝海科技具体指谁？",
                            "scope_attempted": ["current_conversation_graph"],
                            "expected_response": "entity_name_or_context",
                            "pending_graph_update_input_text": "蓝海科技的老板叫李四。",
                        }
                    },
                }
            ],
            use_llm=False,
        )
    )

    assert result["intent"] == "unknown"
    assert result["provider"] == "fallback"
    assert result["fallback_reason"] == "llm_disabled"
    assert "detect_pending_clarification" not in result["trace"]["steps"]
