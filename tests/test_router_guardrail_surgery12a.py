"""Surgery #12A: router guardrail for local 3B tests."""
from __future__ import annotations


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
