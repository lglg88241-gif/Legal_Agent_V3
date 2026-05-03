from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path
from types import ModuleType

import pytest
from fastapi import HTTPException

from app.core.security import AuthenticatedUser
from app.models import Conversation, Message, User
from app.services.conversation_store import clear_conversation_messages

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _install_backend_import_stubs() -> None:
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

    async def _refresh_case_graph(*args, **kwargs):
        return {"actions_applied": 0, "actions": []}

    fake_graph_extractor.delete_case_graph = _delete_case_graph
    fake_graph_extractor.get_case_graph = _get_case_graph
    fake_graph_extractor.format_graph_memory = _format_graph_memory
    fake_graph_extractor.refresh_case_graph = _refresh_case_graph
    sys.modules["app.services.graph_extractor"] = fake_graph_extractor

    fake_legal_agent = ModuleType("app.services.legal_agent")

    class FakeLegalAgent:
        async def ask(self, *args, **kwargs):
            return {"response": "ok", "sources": [], "route_intent": "GENERAL_CONSULT"}

    fake_legal_agent.legal_agent = FakeLegalAgent()
    sys.modules["app.services.legal_agent"] = fake_legal_agent

    fake_redis_mgr = ModuleType("app.memory.redis_mgr")
    fake_redis_mgr.get_redis_saver_for_current_loop = lambda: None
    sys.modules["app.memory.redis_mgr"] = fake_redis_mgr


def _load_graph_api_module():
    sys.modules.pop("app.api.graph", None)
    fake_graph_extractor = ModuleType("app.services.graph_extractor")

    async def _get_cross_case_entities(*args, **kwargs):
        return []

    async def _get_user_cases_graph_overview(*args, **kwargs):
        return []

    fake_graph_extractor.get_cross_case_entities = _get_cross_case_entities
    fake_graph_extractor.get_user_cases_graph_overview = _get_user_cases_graph_overview
    sys.modules["app.services.graph_extractor"] = fake_graph_extractor
    return importlib.import_module("app.api.graph")


def _load_conversations_api_module():
    _install_backend_import_stubs()
    sys.modules.pop("app.api.conversations", None)
    return importlib.import_module("app.api.conversations")


def _load_celery_module():
    _install_backend_import_stubs()
    sys.modules.pop("app.celery_app", None)
    return importlib.import_module("app.celery_app")


def test_graph_user_route_forbids_cross_user_access() -> None:
    graph_api = _load_graph_api_module()

    async def _run() -> None:
        with pytest.raises(HTTPException) as exc_info:
            await graph_api.get_user_cases(
                user_id=2,
                current_user=AuthenticatedUser(user_id=1, username="alice"),
            )
        assert exc_info.value.status_code == 403

    asyncio.run(_run())


def test_graph_me_cases_uses_current_user(monkeypatch: pytest.MonkeyPatch) -> None:
    graph_api = _load_graph_api_module()
    captured: dict[str, int] = {}

    async def fake_get_user_cases_graph_overview(user_id: int):
        captured["user_id"] = user_id
        return [{"case_id": 11}]

    monkeypatch.setattr(graph_api, "get_user_cases_graph_overview", fake_get_user_cases_graph_overview)

    result = asyncio.run(
        graph_api.get_my_cases(
            current_user=AuthenticatedUser(user_id=7, username="tester"),
        )
    )

    assert captured["user_id"] == 7
    assert result["user_id"] == 7
    assert result["cases"] == [{"case_id": 11}]


def test_graph_subgraph_is_static_law_only() -> None:
    source = (PROJECT_ROOT / "app" / "api" / "graph.py").read_text(encoding="utf-8")

    assert "MATCH (n:LawArticle)-[r:REFERENCES]-(m:LawArticle)" in source
    assert "DynamicCaseEntity" not in source
    assert "current_user: AuthenticatedUser = Depends(get_current_user)" in source


def test_query_neo4j_graph_is_static_law_only() -> None:
    source = (PROJECT_ROOT / "app" / "agent" / "tools.py").read_text(encoding="utf-8")

    assert "MATCH (source:LawArticle)-[rel:REFERENCES]-(target:LawArticle)" in source
    assert 'session.run(GRAPH_QUERY, {"tokens": tokens})' in source
    assert "DynamicCaseEntity" not in source


def test_clear_conversation_messages_resets_case_state() -> None:
    async def _run() -> None:
        user = User(id=1, username="u1", hashed_password="hashed")
        conversation = Conversation(
            id=10,
            user_id=user.id,
            session_id="session-1",
            title="Case",
            case_summary="old summary",
            case_state_json={"monthly_salary": 12000},
            case_type="labor",
            conversation_mode="TRACKED_CASE",
            persistence_consent="tracked",
            mode_reason="previous_tracking_choice",
        )
        original_updated_at = conversation.updated_at
        messages = [
            Message(
                id=20,
                conversation_id=conversation.id,
                role="user",
                content="hello",
                message_metadata={},
            )
        ]

        class FakeDB:
            def __init__(self) -> None:
                self.messages = messages
                self.conversation = conversation

            async def execute(self, statement) -> None:
                self.messages = []

            async def get(self, model, conversation_id: int):
                if model is Conversation and conversation_id == self.conversation.id:
                    return self.conversation
                return None

            def add(self, obj) -> None:
                return None

            async def flush(self) -> None:
                return None

        db = FakeDB()
        await clear_conversation_messages(db, conversation.id)

        assert db.messages == []
        assert conversation.case_summary == ""
        assert conversation.case_state_json is None
        assert conversation.case_type is None
        assert conversation.conversation_mode == "UNKNOWN"
        assert conversation.persistence_consent == "unknown"
        assert conversation.mode_confirmed_at is None
        assert conversation.mode_reason is None
        assert conversation.updated_at is not None
        assert original_updated_at is None or conversation.updated_at >= original_updated_at

    asyncio.run(_run())


def test_clear_messages_returns_warning_when_graph_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    conversations_api = _load_conversations_api_module()

    class DummyDB:
        async def commit(self) -> None:
            return None

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_conversation_for_user(db, user_id: int, conversation_id: int):
        return type("Conversation", (), {"id": conversation_id})()

    async def fake_clear_conversation_messages(db, conversation_id: int) -> None:
        return None

    async def fake_delete_case_graph(conversation_id: int):
        return {"success": False, "error": "neo4j down"}

    monkeypatch.setattr(conversations_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(conversations_api, "get_conversation_for_user", fake_get_conversation_for_user)
    monkeypatch.setattr(conversations_api, "clear_conversation_messages", fake_clear_conversation_messages)
    monkeypatch.setattr(conversations_api, "delete_case_graph", fake_delete_case_graph)

    caplog.set_level("WARNING")
    result = asyncio.run(
        conversations_api.clear_messages(
            conversation_id=9,
            current_user=AuthenticatedUser(user_id=3, username="alice"),
            db=DummyDB(),
        )
    )

    assert result == {
        "status": "success",
        "success": True,
        "graph_cleanup_success": False,
        "warning": "SQL data cleared but graph cleanup failed",
    }
    assert "conversation_id=9" in caplog.text
    assert "user_id=3" in caplog.text
    assert "neo4j down" in caplog.text


def test_delete_conversation_returns_warning_when_graph_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    conversations_api = _load_conversations_api_module()

    class DummyDB:
        async def commit(self) -> None:
            return None

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_conversation_for_user(db, user_id: int, conversation_id: int):
        return type("Conversation", (), {"id": conversation_id})()

    async def fake_delete_conversation_record(db, conversation_id: int) -> None:
        return None

    async def fake_delete_case_graph(conversation_id: int):
        return {"success": False, "error": "neo4j delete failed"}

    monkeypatch.setattr(conversations_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(conversations_api, "get_conversation_for_user", fake_get_conversation_for_user)
    monkeypatch.setattr(conversations_api, "delete_conversation_record", fake_delete_conversation_record)
    monkeypatch.setattr(conversations_api, "delete_case_graph", fake_delete_case_graph)

    caplog.set_level("WARNING")
    result = asyncio.run(
        conversations_api.delete_conversation(
            conversation_id=12,
            current_user=AuthenticatedUser(user_id=5, username="bob"),
            db=DummyDB(),
        )
    )

    assert result == {
        "status": "success",
        "success": True,
        "graph_cleanup_success": False,
        "warning": "SQL data cleared but graph cleanup failed",
    }
    assert "conversation_id=12" in caplog.text
    assert "user_id=5" in caplog.text
    assert "neo4j delete failed" in caplog.text


def test_update_graph_task_uses_bound_message_id(monkeypatch: pytest.MonkeyPatch) -> None:
    celery_module = _load_celery_module()
    captured: dict[str, object] = {}

    class DummyEngine:
        async def dispose(self) -> None:
            return None

    class DummySessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def fake_session_factory():
        return DummySessionContext()

    async def fake_ensure_storage_schema(bind_engine=None) -> None:
        return None

    async def fake_get_conversation_for_user(db, user_id: int, conversation_id: int):
        return type("Conversation", (), {"id": conversation_id, "user_id": user_id})()

    async def fake_load_conversation_case_summary(db, conversation_id: int) -> str:
        return "summary"

    async def fake_get_message_for_conversation(db, conversation_id: int, message_id: int):
        captured["conversation_id"] = conversation_id
        captured["message_id"] = message_id
        return type("Message", (), {"role": "user", "content": "first message"})()

    async def fail_if_latest_message_lookup(*args, **kwargs):
        raise AssertionError("latest message lookup should not be used")

    async def fake_refresh_case_graph(
        conversation_id: int,
        user_input: str,
        case_summary: str,
        user_id: int,
        prior_reference_guard=None,
        conversation_context=None,
    ):
        captured["user_input"] = user_input
        captured["case_summary"] = case_summary
        captured["user_id"] = user_id
        captured["prior_reference_guard"] = prior_reference_guard
        captured["conversation_context"] = conversation_context
        return {"actions_applied": 2, "actions": [{"action": "ADD"}], "deleted_count": 0}

    async def fake_load_graph_update_context_async(**kwargs):
        captured["graph_context_kwargs"] = kwargs
        return {"recent_messages": [], "dossier_context": {}, "dossier_graph_context": {}}

    monkeypatch.setattr(celery_module, "_create_celery_session_factory", lambda: (DummyEngine(), fake_session_factory))
    monkeypatch.setattr(celery_module, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(celery_module, "get_conversation_for_user", fake_get_conversation_for_user)
    monkeypatch.setattr(celery_module, "load_conversation_case_summary", fake_load_conversation_case_summary)
    monkeypatch.setattr(celery_module, "get_message_for_conversation", fake_get_message_for_conversation)
    monkeypatch.setattr(celery_module, "list_recent_conversation_messages", fail_if_latest_message_lookup)
    monkeypatch.setattr(celery_module, "_load_graph_update_context_async", fake_load_graph_update_context_async)
    monkeypatch.setattr(celery_module, "refresh_case_graph", fake_refresh_case_graph)

    result = celery_module.update_graph_task.run(
        conversation_id=8,
        user_id=4,
        message_id=21,
    )

    assert result["status"] == "success"
    assert result["message_id"] == 21
    assert captured["conversation_id"] == 8
    assert captured["message_id"] == 21
    assert captured["user_input"] == "first message"
    assert captured["case_summary"] == "summary"
    assert captured["user_id"] == 4
    assert captured["prior_reference_guard"] is None
    assert captured["conversation_context"] == {"recent_messages": [], "dossier_context": {}, "dossier_graph_context": {}}
    assert captured["graph_context_kwargs"]["conversation_id"] == 8


def test_update_graph_task_passes_prior_reference_guard_from_user_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    celery_module = _load_celery_module()
    captured: dict[str, object] = {}
    reference_guard = {
        "source": "graph_reference_guard",
        "decision": "allow",
        "confidence": "high",
        "needs_clarification": False,
        "reference_resolutions": [
            {"mention": "我", "resolved_label": "张三", "reference_type": "self", "confidence": "high"},
            {"mention": "公司", "resolved_label": "蓝海科技", "reference_type": "organization", "confidence": "high"},
        ],
    }

    class DummyEngine:
        async def dispose(self) -> None:
            return None

    class DummySessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def fake_session_factory():
        return DummySessionContext()

    async def fake_ensure_storage_schema(bind_engine=None) -> None:
        return None

    async def fake_get_conversation_for_user(db, user_id: int, conversation_id: int):
        return type("Conversation", (), {"id": conversation_id, "user_id": user_id})()

    async def fake_load_conversation_case_summary(db, conversation_id: int) -> str:
        return "summary"

    async def fake_get_message_for_conversation(db, conversation_id: int, message_id: int):
        return type(
            "Message",
            (),
            {
                "role": "user",
                "content": "原始消息",
                "message_metadata": {
                    "graph_update_input_text": "我叫张三，在一家叫蓝海科技的公司做后端开发。公司拖欠我工资。",
                    "reference_guard": reference_guard,
                },
            },
        )()

    async def fake_refresh_case_graph(
        conversation_id: int,
        user_input: str,
        case_summary: str,
        user_id: int,
        prior_reference_guard=None,
        conversation_context=None,
    ):
        captured["user_input"] = user_input
        captured["prior_reference_guard"] = prior_reference_guard
        captured["conversation_context"] = conversation_context
        return {"actions_applied": 2, "actions": [{"action": "ADD"}], "deleted_count": 0}

    async def fake_load_graph_update_context_async(**kwargs):
        return {"recent_messages": [], "dossier_context": {}, "dossier_graph_context": {}}

    monkeypatch.setattr(celery_module, "_create_celery_session_factory", lambda: (DummyEngine(), fake_session_factory))
    monkeypatch.setattr(celery_module, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(celery_module, "get_conversation_for_user", fake_get_conversation_for_user)
    monkeypatch.setattr(celery_module, "load_conversation_case_summary", fake_load_conversation_case_summary)
    monkeypatch.setattr(celery_module, "get_message_for_conversation", fake_get_message_for_conversation)
    monkeypatch.setattr(celery_module, "_load_graph_update_context_async", fake_load_graph_update_context_async)
    monkeypatch.setattr(celery_module, "refresh_case_graph", fake_refresh_case_graph)

    result = celery_module.update_graph_task.run(
        conversation_id=8,
        user_id=4,
        message_id=21,
    )

    assert result["status"] == "success"
    assert captured["user_input"] == "我叫张三，在一家叫蓝海科技的公司做后端开发。公司拖欠我工资。"
    assert captured["prior_reference_guard"] == reference_guard
    assert captured["conversation_context"] == {"recent_messages": [], "dossier_context": {}, "dossier_graph_context": {}}


def test_update_graph_task_attaches_graph_clarification_to_matching_assistant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    celery_module = _load_celery_module()
    pending = {
        "type": "subject_reference_resolution",
        "source": "graph_reference_guard",
        "target_mention": "他",
        "reference_type": "person",
        "previous_question": "为避免把案卷图谱写错，这里的“他”具体指谁？请直接回复主体名称。",
        "scope_attempted": ["current_conversation_graph"],
        "expected_response": "entity_name_or_context",
        "pending_graph_update_input_text": "他一直在微信上说等融资到账就发工资。",
    }
    reference_guard = {
        "source": "graph_reference_guard",
        "decision": "clarify",
        "needs_clarification": True,
        "pending_clarification": pending,
        "clarification_questions": [pending["previous_question"]],
    }

    class DummyEngine:
        async def dispose(self) -> None:
            return None

    class DummySession:
        def __init__(self):
            self.added = []
            self.committed = False

        def add(self, item):
            self.added.append(item)

        async def commit(self):
            self.committed = True

    session = DummySession()

    class DummySessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def fake_session_factory():
        return DummySessionContext()

    async def fake_ensure_storage_schema(bind_engine=None) -> None:
        return None

    async def fake_get_conversation_for_user(db, user_id: int, conversation_id: int):
        return type("Conversation", (), {"id": conversation_id, "user_id": user_id})()

    async def fake_load_conversation_case_summary(db, conversation_id: int) -> str:
        return "summary"

    user_message = type(
        "Message",
        (),
        {
            "id": 21,
            "role": "user",
            "content": "他一直在微信上说等融资到账就发工资。",
            "message_metadata": {},
        },
    )()
    assistant_message = type(
        "Message",
        (),
        {
            "id": 22,
            "role": "assistant",
            "content": "已记录这条新的案情事实。",
            "message_metadata": {"route_intent": "CASE_UPDATE"},
        },
    )()
    later_user_message = type(
        "Message",
        (),
        {
            "id": 23,
            "role": "user",
            "content": "下一条消息",
            "message_metadata": {},
        },
    )()

    async def fake_get_message_for_conversation(db, conversation_id: int, message_id: int):
        if message_id == 21:
            return user_message
        if message_id == 22:
            return assistant_message
        return None

    async def fake_list_recent_conversation_messages(db, conversation_id: int, limit: int = 8):
        return [user_message, assistant_message, later_user_message]

    async def fake_refresh_case_graph(
        conversation_id: int,
        user_input: str,
        case_summary: str,
        user_id: int,
        prior_reference_guard=None,
        conversation_context=None,
    ):
        return {
            "conversation_id": conversation_id,
            "actions_applied": 0,
            "actions": [],
            "deleted_count": 0,
            "needs_clarification": True,
            "clarification_questions": [pending["previous_question"]],
            "pending_clarification": pending,
            "reference_guard": reference_guard,
        }

    async def fake_load_graph_update_context_async(**kwargs):
        return {"recent_messages": [], "dossier_context": {}, "dossier_graph_context": {}}

    monkeypatch.setattr(celery_module, "_create_celery_session_factory", lambda: (DummyEngine(), fake_session_factory))
    monkeypatch.setattr(celery_module, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(celery_module, "get_conversation_for_user", fake_get_conversation_for_user)
    monkeypatch.setattr(celery_module, "load_conversation_case_summary", fake_load_conversation_case_summary)
    monkeypatch.setattr(celery_module, "get_message_for_conversation", fake_get_message_for_conversation)
    monkeypatch.setattr(celery_module, "list_recent_conversation_messages", fake_list_recent_conversation_messages)
    monkeypatch.setattr(celery_module, "_load_graph_update_context_async", fake_load_graph_update_context_async)
    monkeypatch.setattr(celery_module, "refresh_case_graph", fake_refresh_case_graph)

    result = celery_module.update_graph_task.run(
        conversation_id=8,
        user_id=4,
        message_id=21,
    )

    assert result["status"] == "success"
    assert result["needs_clarification"] is True
    assert assistant_message.message_metadata["pending_clarification"] == pending
    assert assistant_message.message_metadata["reference_guard"] == reference_guard
    assert assistant_message.message_metadata["graph_update_needs_clarification"] is True
    assert session.added == [assistant_message]
    assert session.committed is True


def test_update_graph_task_falls_back_to_user_message_for_graph_clarification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    celery_module = _load_celery_module()
    pending = {
        "type": "subject_reference_resolution",
        "source": "graph_write_guard",
        "target_mention": "公司",
        "reference_type": "organization",
        "previous_question": "这里的“公司”具体是哪一家？",
        "scope_attempted": ["current_conversation_graph", "graph_write_guard"],
        "expected_response": "entity_name_or_context",
        "pending_graph_update_input_text": "公司没有签劳动合同。",
    }
    reference_guard = {
        "source": "graph_write_guard",
        "decision": "clarify",
        "needs_clarification": True,
        "pending_clarification": pending,
        "clarification_questions": [pending["previous_question"]],
    }

    class DummyEngine:
        async def dispose(self) -> None:
            return None

    class DummySession:
        def __init__(self):
            self.added = []
            self.committed = False

        def add(self, item):
            self.added.append(item)

        async def commit(self):
            self.committed = True

    session = DummySession()

    class DummySessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def fake_session_factory():
        return DummySessionContext()

    async def fake_ensure_storage_schema(bind_engine=None) -> None:
        return None

    async def fake_get_conversation_for_user(db, user_id: int, conversation_id: int):
        return type("Conversation", (), {"id": conversation_id, "user_id": user_id})()

    async def fake_load_conversation_case_summary(db, conversation_id: int) -> str:
        return "summary"

    user_message = type(
        "Message",
        (),
        {
            "id": 31,
            "role": "user",
            "content": "公司没有签劳动合同。",
            "message_metadata": {},
        },
    )()

    async def fake_get_message_for_conversation(db, conversation_id: int, message_id: int):
        if message_id == 31:
            return user_message
        return None

    async def fake_list_recent_conversation_messages(db, conversation_id: int, limit: int = 8):
        return [user_message]

    async def fake_refresh_case_graph(
        conversation_id: int,
        user_input: str,
        case_summary: str,
        user_id: int,
        prior_reference_guard=None,
        conversation_context=None,
    ):
        return {
            "conversation_id": conversation_id,
            "actions_applied": 0,
            "actions": [],
            "deleted_count": 0,
            "needs_clarification": True,
            "clarification_questions": [pending["previous_question"]],
            "pending_clarification": pending,
            "reference_guard": reference_guard,
        }

    async def fake_load_graph_update_context_async(**kwargs):
        return {"recent_messages": [], "dossier_context": {}, "dossier_graph_context": {}}

    monkeypatch.setattr(celery_module, "_create_celery_session_factory", lambda: (DummyEngine(), fake_session_factory))
    monkeypatch.setattr(celery_module, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(celery_module, "get_conversation_for_user", fake_get_conversation_for_user)
    monkeypatch.setattr(celery_module, "load_conversation_case_summary", fake_load_conversation_case_summary)
    monkeypatch.setattr(celery_module, "get_message_for_conversation", fake_get_message_for_conversation)
    monkeypatch.setattr(celery_module, "list_recent_conversation_messages", fake_list_recent_conversation_messages)
    monkeypatch.setattr(celery_module, "_load_graph_update_context_async", fake_load_graph_update_context_async)
    monkeypatch.setattr(celery_module, "refresh_case_graph", fake_refresh_case_graph)

    result = celery_module.update_graph_task.run(
        conversation_id=8,
        user_id=4,
        message_id=31,
    )

    assert result["status"] == "success"
    assert result["needs_clarification"] is True
    assert user_message.message_metadata["pending_clarification"] == pending
    assert user_message.message_metadata["reference_guard"] == reference_guard
    assert user_message.message_metadata["graph_update_needs_clarification"] is True
    assert session.added == [user_message]
    assert session.committed is True
