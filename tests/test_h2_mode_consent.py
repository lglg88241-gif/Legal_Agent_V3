from __future__ import annotations

import asyncio
import importlib
import sys
from types import ModuleType

import pytest
from fastapi import HTTPException

from app.core.security import AuthenticatedUser
from app.models import Conversation
from app.services.conversation_mode import detect_conversation_mode
from app.services.conversation_store import serialize_conversation


def _install_common_stubs() -> None:
    fake_openai = ModuleType("openai")

    class FakeAsyncOpenAI:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def close(self) -> None:
            return None

    fake_openai.AsyncOpenAI = FakeAsyncOpenAI
    sys.modules["openai"] = fake_openai

    fake_llm_service = ModuleType("app.services.llm_service")

    class FakeLLMService:
        async def async_generate(self, *args, **kwargs) -> str:
            return ""

    fake_llm_service.llm_service = FakeLLMService()
    sys.modules["app.services.llm_service"] = fake_llm_service

    fake_graph_extractor = ModuleType("app.services.graph_extractor")

    async def _delete_case_graph(*args, **kwargs):
        return {"success": True, "error": None}

    async def _get_case_graph(*args, **kwargs):
        return {}

    async def _format_graph_memory(*args, **kwargs) -> str:
        return ""

    fake_graph_extractor.delete_case_graph = _delete_case_graph
    fake_graph_extractor.get_case_graph = _get_case_graph
    fake_graph_extractor.format_graph_memory = _format_graph_memory
    sys.modules["app.services.graph_extractor"] = fake_graph_extractor


def _load_conversations_api_module():
    _install_common_stubs()
    sys.modules.pop("app.api.conversations", None)
    module = importlib.import_module("app.api.conversations")
    sys.modules.pop("app.services.graph_extractor", None)
    sys.modules.pop("app.services.llm_service", None)
    return module


def _load_dialog_api_module():
    _install_common_stubs()

    fake_case_boundary = ModuleType("app.services.case_boundary")

    async def _detect_case_boundary(*args, **kwargs) -> str:
        return "SAME_CASE"

    fake_case_boundary.detect_case_boundary = _detect_case_boundary
    sys.modules["app.services.case_boundary"] = fake_case_boundary

    fake_llm_provider = ModuleType("app.core.llm_provider")

    class LLMProvider:
        GPT_5_4 = "GPT_5_4"
        GPT_5_1 = "GPT_5_1"
        ZHIPU = "ZHIPU"
        LOCAL_VLLM = "LOCAL_VLLM"

    class FakeRouter:
        local_enabled = False
        vllm_base_url = "http://127.0.0.1:8001"

        def get_provider(self, scene: str) -> str:
            return LLMProvider.ZHIPU

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
            self.calls: list[list[object]] = []

        def apply_async(self, args):
            self.calls.append(args)
            return type("Task", (), {"id": "task-123"})()

    fake_celery.ask_agent_task = FakeAskAgentTask()
    sys.modules["app.celery_app"] = fake_celery

    sys.modules.pop("app.api.dialog", None)
    module = importlib.import_module("app.api.dialog")
    sys.modules.pop("app.services.case_boundary", None)
    sys.modules.pop("app.celery_app", None)
    return module


def test_detect_conversation_mode_for_legal_qa() -> None:
    result = detect_conversation_mode("劳动合同试用期最长多久？")

    assert result == {
        "detected_mode": "LEGAL_QA",
        "suggested_mode": None,
        "requires_persistence_consent": False,
        "reason": "legal_question_pattern_detected",
    }


def test_detect_conversation_mode_for_case_analysis() -> None:
    result = detect_conversation_mode("我工作5年，月薪12000，公司违法辞退我")

    assert result == {
        "detected_mode": "CASE_ANALYSIS",
        "suggested_mode": "TRACKED_CASE",
        "requires_persistence_consent": True,
        "reason": "fact_pattern_detected",
    }


def test_detect_conversation_mode_does_not_repeat_prompt_for_tracked_case() -> None:
    result = detect_conversation_mode(
        "我还有聊天记录和工资流水",
        current_mode="TRACKED_CASE",
        case_summary="劳动争议，主张违法解除赔偿",
    )

    assert result["detected_mode"] == "CASE_ANALYSIS"
    assert result["suggested_mode"] is None
    assert result["requires_persistence_consent"] is False


def test_serialize_conversation_includes_mode_fields() -> None:
    conversation = Conversation(
        id=11,
        user_id=7,
        session_id="session-11",
        title="mode test",
        case_summary="summary",
        case_type="labor",
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
        mode_reason="user_selected_tracked",
    )

    serialized = serialize_conversation(conversation)

    assert serialized["conversation_mode"] == "TRACKED_CASE"
    assert serialized["persistence_consent"] == "tracked"
    assert serialized["mode_reason"] == "user_selected_tracked"
    assert "mode_confirmed_at" in serialized


def test_dialog_keeps_legacy_behavior_when_mode_consent_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
            session_id="session-1",
            title="test",
            conversation_mode="UNKNOWN",
            persistence_consent="unknown",
        )

    async def fake_list_recent_conversation_messages(db, conversation_id: int, limit: int = 6):
        return []

    async def fake_detect_case_boundary(**kwargs):
        return "SAME_CASE"

    monkeypatch.setattr(dialog_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dialog_api, "get_conversation_for_user", fake_get_conversation_for_user)
    monkeypatch.setattr(dialog_api, "list_recent_conversation_messages", fake_list_recent_conversation_messages)
    monkeypatch.setattr(dialog_api, "detect_case_boundary", fake_detect_case_boundary)

    result = asyncio.run(
        dialog_api.process_dialog(
            request=dialog_api.DialogRequest(
                user_input="我工作5年，月薪12000，公司违法辞退我",
                conversation_id=1,
                mode_consent_enabled=False,
            ),
            current_user=AuthenticatedUser(user_id=3, username="alice"),
            db=DummyDB(),
        )
    )

    assert result["status"] == "processing"
    assert result["task_id"] == "task-123"
    assert result.get("action") is None


def test_dialog_returns_mode_consent_when_enabled_for_case_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
            session_id="session-2",
            title="test",
            conversation_mode="LEGAL_QA",
            persistence_consent="unknown",
        )

    async def fake_list_recent_conversation_messages(db, conversation_id: int, limit: int = 6):
        return []

    async def fake_detect_case_boundary(**kwargs):
        return "SAME_CASE"

    monkeypatch.setattr(dialog_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dialog_api, "get_conversation_for_user", fake_get_conversation_for_user)
    monkeypatch.setattr(dialog_api, "list_recent_conversation_messages", fake_list_recent_conversation_messages)
    monkeypatch.setattr(dialog_api, "detect_case_boundary", fake_detect_case_boundary)

    result = asyncio.run(
        dialog_api.process_dialog(
            request=dialog_api.DialogRequest(
                user_input="我工作5年，月薪12000，公司违法辞退我",
                conversation_id=2,
                mode_consent_enabled=True,
            ),
            current_user=AuthenticatedUser(user_id=5, username="alice"),
            db=DummyDB(),
        )
    )

    assert result["status"] == "needs_confirmation"
    assert result["action"] == "mode_consent"
    assert result["detected_mode"] == "CASE_ANALYSIS"
    assert result["conversation_mode"] == "LEGAL_QA"
    assert result["suggested_mode"] == "TRACKED_CASE"
    assert result["requires_persistence_consent"] is True
    assert result["case_tracking_decision"]["source"] == "legacy_fallback"
    assert result["case_tracking_decision"]["fallback_used"] is True


def test_dialog_prioritizes_suggest_new_case_over_mode_consent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
            session_id="session-3",
            title="test",
            conversation_mode="UNKNOWN",
            persistence_consent="unknown",
        )

    async def fake_list_recent_conversation_messages(db, conversation_id: int, limit: int = 6):
        return []

    async def fake_detect_case_boundary(**kwargs):
        return "NEW_CASE"

    monkeypatch.setattr(dialog_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dialog_api, "get_conversation_for_user", fake_get_conversation_for_user)
    monkeypatch.setattr(dialog_api, "list_recent_conversation_messages", fake_list_recent_conversation_messages)
    monkeypatch.setattr(dialog_api, "detect_case_boundary", fake_detect_case_boundary)

    result = asyncio.run(
        dialog_api.process_dialog(
            request=dialog_api.DialogRequest(
                user_input="我工作5年，月薪12000，公司违法辞退我",
                conversation_id=3,
                mode_consent_enabled=True,
            ),
            current_user=AuthenticatedUser(user_id=8, username="alice"),
            db=DummyDB(),
        )
    )

    assert result["status"] == "needs_confirmation"
    assert result["action"] == "suggest_new_case"
    assert "detected_mode" not in result


def test_mode_consent_api_updates_tracked_case(monkeypatch: pytest.MonkeyPatch) -> None:
    conversations_api = _load_conversations_api_module()

    class DummyDB:
        def add(self, obj) -> None:
            return None

        async def flush(self) -> None:
            return None

        async def commit(self) -> None:
            return None

        async def refresh(self, conversation) -> None:
            return None

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_conversation_for_user(db, user_id: int, conversation_id: int):
        return Conversation(
            id=conversation_id,
            user_id=user_id,
            session_id="session-4",
            title="tracked",
            conversation_mode="UNKNOWN",
            persistence_consent="unknown",
        )

    monkeypatch.setattr(conversations_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(conversations_api, "get_conversation_for_user", fake_get_conversation_for_user)

    result = asyncio.run(
        conversations_api.set_conversation_mode_consent(
            conversation_id=4,
            payload=conversations_api.ConversationModeConsentRequest(
                mode="TRACKED_CASE",
                consent="tracked",
                reason="user_selected_tracked",
            ),
            current_user=AuthenticatedUser(user_id=6, username="owner"),
            db=DummyDB(),
        )
    )

    assert result["status"] == "success"
    assert result["conversation"]["conversation_mode"] == "TRACKED_CASE"
    assert result["conversation"]["persistence_consent"] == "tracked"
    assert result["conversation"]["mode_reason"] == "user_selected_tracked"
    assert result["conversation"]["mode_confirmed_at"] is not None


def test_mode_consent_api_updates_temporary_case_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversations_api = _load_conversations_api_module()

    class DummyDB:
        def add(self, obj) -> None:
            return None

        async def flush(self) -> None:
            return None

        async def commit(self) -> None:
            return None

        async def refresh(self, conversation) -> None:
            return None

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_conversation_for_user(db, user_id: int, conversation_id: int):
        return Conversation(
            id=conversation_id,
            user_id=user_id,
            session_id="session-5",
            title="temporary",
            conversation_mode="UNKNOWN",
            persistence_consent="unknown",
        )

    monkeypatch.setattr(conversations_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(conversations_api, "get_conversation_for_user", fake_get_conversation_for_user)

    result = asyncio.run(
        conversations_api.set_conversation_mode_consent(
            conversation_id=5,
            payload=conversations_api.ConversationModeConsentRequest(
                mode="CASE_ANALYSIS",
                consent="temporary",
                reason="user_selected_temporary",
            ),
            current_user=AuthenticatedUser(user_id=6, username="owner"),
            db=DummyDB(),
        )
    )

    assert result["conversation"]["conversation_mode"] == "CASE_ANALYSIS"
    assert result["conversation"]["persistence_consent"] == "temporary"


def test_mode_consent_api_rejects_other_users_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversations_api = _load_conversations_api_module()

    class DummyDB:
        def add(self, obj) -> None:
            return None

        async def flush(self) -> None:
            return None

        async def commit(self) -> None:
            return None

        async def refresh(self, conversation) -> None:
            return None

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_conversation_for_user(db, user_id: int, conversation_id: int):
        return None

    monkeypatch.setattr(conversations_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(conversations_api, "get_conversation_for_user", fake_get_conversation_for_user)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            conversations_api.set_conversation_mode_consent(
                conversation_id=6,
                payload=conversations_api.ConversationModeConsentRequest(
                    mode="TRACKED_CASE",
                    consent="tracked",
                ),
                current_user=AuthenticatedUser(user_id=2, username="owner"),
                db=DummyDB(),
            )
        )

    assert exc_info.value.status_code == 404


def test_mode_consent_api_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversations_api = _load_conversations_api_module()

    class DummyDB:
        def add(self, obj) -> None:
            return None

        async def flush(self) -> None:
            return None

        async def commit(self) -> None:
            return None

        async def refresh(self, conversation) -> None:
            return None

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_conversation_for_user(db, user_id: int, conversation_id: int):
        return Conversation(
            id=conversation_id,
            user_id=user_id,
            session_id="session-7",
            title="invalid",
            conversation_mode="UNKNOWN",
            persistence_consent="unknown",
        )

    monkeypatch.setattr(conversations_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(conversations_api, "get_conversation_for_user", fake_get_conversation_for_user)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            conversations_api.set_conversation_mode_consent(
                conversation_id=7,
                payload=conversations_api.ConversationModeConsentRequest(
                    mode="TRACKED_CASE",
                    consent="temporary",
                ),
                current_user=AuthenticatedUser(user_id=2, username="owner"),
                db=DummyDB(),
            )
        )

    assert exc_info.value.status_code == 400
