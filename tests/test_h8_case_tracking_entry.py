from __future__ import annotations

import asyncio
import importlib
import sys
from types import ModuleType

from app.core.security import AuthenticatedUser
from app.models import Conversation
from app.services.case_tracking_entry_agent import decide_case_tracking_entry


def _run(coro):
    return asyncio.run(coro)


def _fact_understanding() -> dict:
    return {
        "intent": "fact_update",
        "route_hint": "CASE_UPDATE",
        "confidence": "high",
        "source": "test",
    }


def test_case_fact_prompts_mode_consent_decision() -> None:
    decision = _run(
        decide_case_tracking_entry(
            message_text="我和B公司签了劳动合同，但B公司一直没给我缴纳社保。",
            message_understanding=_fact_understanding(),
            conversation_mode="UNKNOWN",
            persistence_consent="unknown",
        )
    )

    assert decision["should_prompt"] is True
    assert decision["prompt_reason"] == "concrete_case_fact_detected"
    assert decision["suggested_mode"] == "tracked_case"
    assert decision["is_case_fact"] is True
    assert decision["source"] == "agentic_entry_decision"
    assert decision["fallback_used"] is False


def test_general_legal_advice_does_not_prompt() -> None:
    decision = _run(
        decide_case_tracking_entry(
            message_text="劳动者被违法辞退应该怎么办？",
            message_understanding={
                "intent": "advice_question",
                "route_hint": "GENERAL_CONSULT",
                "confidence": "high",
            },
            conversation_mode="UNKNOWN",
            persistence_consent="unknown",
        )
    )

    assert decision["should_prompt"] is False
    assert decision["is_legal_question"] is True
    assert decision["detected_mode"] == "LEGAL_QA"


def test_explicit_law_question_does_not_prompt() -> None:
    decision = _run(
        decide_case_tracking_entry(
            message_text="劳动合同法第八十七条规定了什么？",
            message_understanding={
                "intent": "legal_question",
                "route_hint": "LEGAL_QA",
                "confidence": "high",
            },
            conversation_mode="UNKNOWN",
            persistence_consent="unknown",
        )
    )

    assert decision["should_prompt"] is False
    assert decision["is_legal_question"] is True
    assert decision["route_hint"] == "LEGAL_QA"


def test_lawyer_retelling_can_prompt_without_user_self_anchor() -> None:
    decision = _run(
        decide_case_tracking_entry(
            message_text="我的委托人张三称，B公司未给其缴纳社保。",
            message_understanding=_fact_understanding(),
            conversation_mode="UNKNOWN",
            persistence_consent="unknown",
        )
    )

    assert decision["should_prompt"] is True
    assert decision["is_case_fact"] is True
    assert decision["is_third_party_retelling"] is True
    assert decision["has_explicit_party_name"] is True
    assert decision["party_naming_decision"]["requires_blank_confirmation"] is False
    assert decision["party_naming_decision"]["reason"] == "third_party_retelling_not_user_self_anchor"


def test_explicit_user_name_does_not_require_blank_party_naming() -> None:
    decision = _run(
        decide_case_tracking_entry(
            message_text="我叫张三，是这起劳动争议里的劳动者。",
            message_understanding=_fact_understanding(),
            conversation_mode="UNKNOWN",
            persistence_consent="unknown",
        )
    )

    assert decision["has_explicit_party_name"] is True
    assert decision["party_naming_decision"]["requires_blank_confirmation"] is False
    assert decision["party_naming_decision"]["reason"] == "explicit_party_name_available"


def test_tracked_conversation_does_not_repeat_prompt() -> None:
    decision = _run(
        decide_case_tracking_entry(
            message_text="B公司的人事王经理口头通知我不用来了。",
            message_understanding=_fact_understanding(),
            conversation_mode="TRACKED_CASE",
            persistence_consent="tracked",
            dossier_id=101,  # tracked requires bound dossier
        )
    )

    assert decision["should_prompt"] is False
    assert decision["suppressed_by"] == "tracked_conversation"


def test_temporary_replay_does_not_repeat_prompt() -> None:
    decision = _run(
        decide_case_tracking_entry(
            message_text="我和B公司签了劳动合同，但B公司一直没给我缴纳社保。",
            message_understanding=_fact_understanding(),
            conversation_mode="CASE_ANALYSIS",
            persistence_consent="temporary",
        )
    )

    assert decision["should_prompt"] is False
    assert decision["suppressed_by"] == "temporary_analysis"
    assert decision["party_naming_decision"]["requires_blank_confirmation"] is False


def _install_dialog_stubs() -> None:
    fake_openai = ModuleType("openai")

    class FakeAsyncOpenAI:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def close(self) -> None:
            return None

    fake_openai.AsyncOpenAI = FakeAsyncOpenAI
    sys.modules["openai"] = fake_openai

    fake_case_boundary = ModuleType("app.services.case_boundary")

    async def _detect_case_boundary(*args, **kwargs) -> str:
        return "SAME_CASE"

    fake_case_boundary.detect_case_boundary = _detect_case_boundary
    sys.modules["app.services.case_boundary"] = fake_case_boundary

    fake_graph_extractor = ModuleType("app.services.graph_extractor")

    async def _format_graph_memory(*args, **kwargs) -> str:
        return ""

    fake_graph_extractor.format_graph_memory = _format_graph_memory
    sys.modules["app.services.graph_extractor"] = fake_graph_extractor

    fake_llm_provider = ModuleType("app.core.llm_provider")

    class LLMProvider:
        GPT_5_4 = "GPT_5_4"
        GPT_5_1 = "GPT_5_1"
        ZHIPU = "ZHIPU"
        LOCAL_VLLM = "LOCAL_VLLM"

    class FakeRouter:
        local_enabled = False
        vllm_base_url = "http://127.0.0.1:8001"
        openai_api_key = None
        zhipu_api_key = None

        async def check_vllm_health(self) -> bool:
            return False

        def set_local_enabled(self, enabled: bool) -> None:
            self.local_enabled = enabled

    fake_llm_provider.LLMProvider = LLMProvider
    fake_llm_provider.get_llm_router = lambda: FakeRouter()
    sys.modules["app.core.llm_provider"] = fake_llm_provider

    fake_celery = ModuleType("app.celery_app")

    class FakeAskAgentTask:
        def __init__(self) -> None:
            self.calls = []

        def apply_async(self, args):
            self.calls.append(args)
            return type("Task", (), {"id": "task-123"})()

    fake_celery.ask_agent_task = FakeAskAgentTask()
    sys.modules["app.celery_app"] = fake_celery


def _load_dialog_api_module():
    _install_dialog_stubs()
    sys.modules.pop("app.api.dialog", None)
    return importlib.import_module("app.api.dialog")


def test_dialog_returns_mode_consent_with_entry_decision_metadata(monkeypatch) -> None:
    dialog_api = _load_dialog_api_module()

    class DummyDB:
        async def commit(self) -> None:
            return None

        async def rollback(self) -> None:
            return None

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_conversation_for_user(db, user_id: int, conversation_id: int):
        return Conversation(
            id=conversation_id,
            user_id=user_id,
            session_id="session-h8",
            title="test",
            conversation_mode="UNKNOWN",
            persistence_consent="unknown",
        )

    async def fake_list_recent_conversation_messages(db, conversation_id: int, limit: int = 6):
        return []

    async def fake_decide_case_tracking_entry(**kwargs):
        return {
            "should_prompt": True,
            "prompt_reason": "concrete_case_fact_detected",
            "suggested_mode": "tracked_case",
            "is_case_fact": True,
            "is_legal_question": False,
            "is_third_party_retelling": False,
            "has_explicit_party_name": False,
            "source": "agentic_entry_decision",
            "fallback_used": False,
            "detected_mode": "CASE_ANALYSIS",
            "intent": "fact_update",
            "route_hint": "CASE_UPDATE",
            "confidence": "high",
        }

    monkeypatch.setattr(dialog_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dialog_api, "get_conversation_for_user", fake_get_conversation_for_user)
    monkeypatch.setattr(dialog_api, "list_recent_conversation_messages", fake_list_recent_conversation_messages)
    monkeypatch.setattr(dialog_api, "decide_case_tracking_entry", fake_decide_case_tracking_entry)

    result = _run(
        dialog_api.process_dialog(
            request=dialog_api.DialogRequest(
                user_input="我和B公司签了劳动合同，但B公司一直没给我缴纳社保。",
                conversation_id=10,
                mode_consent_enabled=True,
            ),
            current_user=AuthenticatedUser(user_id=3, username="alice"),
            db=DummyDB(),
        )
    )

    assert result["status"] == "needs_confirmation"
    assert result["action"] == "mode_consent"
    assert result["detected_mode"] == "CASE_ANALYSIS"
    assert result["suggested_mode"] == "TRACKED_CASE"
    assert result["case_tracking_decision"]["source"] == "agentic_entry_decision"
    assert result["case_tracking_decision"]["intent"] == "fact_update"
