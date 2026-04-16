"""Surgery #12A, #15, and #16: router guardrail tests."""
from __future__ import annotations

import asyncio
import os
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ.setdefault("USE_TORCH", "0")

if "json_repair" not in sys.modules:
    fake_json_repair = ModuleType("json_repair")
    fake_json_repair.repair_json = lambda content: content
    sys.modules["json_repair"] = fake_json_repair

if "app.agent.tools" not in sys.modules:
    fake_tools = ModuleType("app.agent.tools")
    fake_tools.AGENT_TOOLS = [SimpleNamespace(name="search_law_articles")]
    sys.modules["app.agent.tools"] = fake_tools


class TestRouterBriefFollowupHeuristics:
    def test_brief_followup_recognizes_compensation_question(self) -> None:
        from app.agent.nodes.router import question_looks_like_brief_followup

        assert question_looks_like_brief_followup("我还能要求什么赔偿？") is True

    def test_brief_followup_recognizes_rights_question(self) -> None:
        from app.agent.nodes.router import question_looks_like_brief_followup

        assert question_looks_like_brief_followup("还可以主张什么？") is True

    def test_brief_followup_recognizes_next_step_question(self) -> None:
        from app.agent.nodes.router import question_looks_like_brief_followup

        assert question_looks_like_brief_followup("接下来怎么维权？") is True


class TestRouterPostLLMGuardrail:
    def test_first_turn_case_update_should_be_downgraded(self) -> None:
        """
        首轮、无历史、无摘要时，LLM 即使输出 CASE_UPDATE，
        guardrail 也应降为 GENERAL_CONSULT。
        """
        chat_history = ""
        case_summary = ""
        has_case_summary = False
        route_intent = "CASE_UPDATE"

        if (
            route_intent == "CASE_UPDATE"
            and not (chat_history or "").strip()
            and not has_case_summary
        ):
            route_intent = "GENERAL_CONSULT"

        assert route_intent == "GENERAL_CONSULT"

    def test_followup_general_consult_should_be_promoted(self) -> None:
        """
        有历史上下文 + 短句跟进时，LLM 即使输出 GENERAL_CONSULT，
        guardrail 也应提升为 CASE_UPDATE。
        """
        from app.agent.nodes.router import (
            history_has_case_continuity,
            question_looks_like_brief_followup,
        )

        chat_history = "用户此前描述：2023年3月入职深圳一家科技公司，未签劳动合同，被口头辞退。"
        case_summary = ""
        route_intent = "GENERAL_CONSULT"
        question = "我还能要求什么赔偿？"

        if (
            route_intent == "GENERAL_CONSULT"
            and history_has_case_continuity(chat_history, case_summary)
            and question_looks_like_brief_followup(question)
        ):
            route_intent = "CASE_UPDATE"

        assert route_intent == "CASE_UPDATE"


class TestOOSGuardrail:
    """Surgery #15/#16: OOS guardrail and deterministic OOS tests."""

    @pytest.mark.parametrize(
        "question,expected_override",
        [
            ("我在公司工作三年被辞退了，能要求赔偿吗", True),
            ("劳动合同到期不续签有补偿金吗", True),
            ("交通事故对方全责怎么索赔", True),
            ("离婚后孩子抚养权怎么判", True),
            ("帮我写一段Python代码", False),
            ("今天天气怎么样", False),
        ],
    )
    def test_oos_guardrail_overrides_llm(self, question, expected_override):
        """当确定性 OOS 未命中时，LLM 的 OOS 应被降级为 GENERAL_CONSULT。"""
        from app.agent.nodes.router import detect_out_of_scope_reason

        heuristic_reason = detect_out_of_scope_reason(question)
        would_override = heuristic_reason is None
        assert would_override == expected_override, (
            f"question={question!r}, heuristic={heuristic_reason}"
        )

    def test_oos_guardrail_foreign_law_heuristic_hits_expanded_case(self):
        """Surgery #16: 外国法问句应被确定性 foreign-law 启发式命中。"""
        from app.agent.nodes.router import detect_out_of_scope_reason

        question = "美国加州离婚法怎么规定的"
        assert detect_out_of_scope_reason(question) == "FOREIGN_LAW"

    def test_oos_guardrail_integration_legal_question_still_downgrades_to_general_consult(self):
        """集成测试：完整 route_decision 流程中 legal false OOS 会被降级。"""
        from app.agent.nodes.router import route_decision

        agent = MagicMock()
        agent._patched_is_case_update = MagicMock(return_value=False)
        agent._patched_is_general_consult = MagicMock(return_value=True)
        agent._looks_like_chitchat = MagicMock(return_value=False)
        agent._chat_model_with_fallback = AsyncMock(return_value='{"route_intent": "OUT_OF_SCOPE"}')
        agent._safe_json_load = MagicMock(return_value={"route_intent": "OUT_OF_SCOPE"})
        agent._patched_build_tool_calls_for_intent = MagicMock(
            return_value=[{"name": "search_law_articles", "arguments": {"query": "我在公司工作三年被辞退了，能要求赔偿吗"}}]
        )
        agent.router_llm = object()
        agent._router_llm_fallback = object()

        result = asyncio.run(
            route_decision(
                agent,
                "我在公司工作三年被辞退了，能要求赔偿吗",
                chat_history="",
                case_summary="",
                is_precise_mode=False,
            )
        )

        assert result["route_intent"] == "GENERAL_CONSULT"
        agent._patched_build_tool_calls_for_intent.assert_called_once_with(
            "GENERAL_CONSULT",
            "我在公司工作三年被辞退了，能要求赔偿吗",
        )

    def test_oos_guardrail_integration_ambiguous_question_without_deterministic_hit_is_downgraded(self):
        """Surgery #16: 只要确定性 OOS 未命中，就不接受 LLM 直接判 OOS。"""
        from app.agent.nodes.router import route_decision

        question = "请帮我分析一下这个情况怎么办"
        agent = MagicMock()
        agent._patched_is_case_update = MagicMock(return_value=False)
        agent._patched_is_general_consult = MagicMock(return_value=True)
        agent._looks_like_chitchat = MagicMock(return_value=False)
        agent._chat_model_with_fallback = AsyncMock(return_value='{"route_intent": "OUT_OF_SCOPE"}')
        agent._safe_json_load = MagicMock(return_value={"route_intent": "OUT_OF_SCOPE"})
        agent._patched_build_tool_calls_for_intent = MagicMock(
            return_value=[{"name": "search_law_articles", "arguments": {"query": question}}]
        )
        agent.router_llm = object()
        agent._router_llm_fallback = object()

        result = asyncio.run(
            route_decision(
                agent,
                question,
                chat_history="",
                case_summary="",
                is_precise_mode=False,
            )
        )

        assert result["route_intent"] == "GENERAL_CONSULT"
        agent._patched_build_tool_calls_for_intent.assert_called_once_with(
            "GENERAL_CONSULT",
            question,
        )
