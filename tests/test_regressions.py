from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import APIRouter

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services import conversation_store


def install_backend_import_stubs() -> None:
    fake_jwt = ModuleType("jwt")
    fake_jwt.encode = lambda *args, **kwargs: "token"
    fake_jwt.decode = lambda *args, **kwargs: {"sub": "1", "username": "tester"}
    sys.modules["jwt"] = fake_jwt

    fake_bcrypt = ModuleType("bcrypt")
    fake_bcrypt.hashpw = lambda password, salt: b"hashed"
    fake_bcrypt.gensalt = lambda: b"salt"
    fake_bcrypt.checkpw = lambda plain, hashed: True
    sys.modules["bcrypt"] = fake_bcrypt

    fake_graph_extractor = ModuleType("app.services.graph_extractor")
    fake_graph_extractor.delete_case_graph = lambda *args, **kwargs: None
    fake_graph_extractor.get_case_graph = lambda *args, **kwargs: {}

    async def _format_graph_memory(*args, **kwargs) -> str:
        return ""

    fake_graph_extractor.format_graph_memory = _format_graph_memory
    sys.modules["app.services.graph_extractor"] = fake_graph_extractor

    fake_tools = ModuleType("app.agent.tools")
    fake_tools.AGENT_TOOLS = [SimpleNamespace(name="search_law_articles")]
    sys.modules["app.agent.tools"] = fake_tools

    for module_name in (
        "app.api.auth",
        "app.api.conversations",
        "app.api.dialog",
        "app.api.feedback",
        "app.api.graph",
    ):
        if module_name not in sys.modules:
            stub = ModuleType(module_name)
            stub.router = APIRouter()
            sys.modules[module_name] = stub


def test_build_conversation_title_uses_fallback_for_blank_text() -> None:
    assert conversation_store.build_conversation_title("") == "新会话"
    assert conversation_store.build_conversation_title("   ", fallback="默认标题") == "默认标题"


def test_build_conversation_title_trims_and_limits_length() -> None:
    title = conversation_store.build_conversation_title("  第一行\n第二行  第三行  ")
    assert title == "第一行 第二行 第三行"

    long_title = conversation_store.build_conversation_title("a" * 40)
    assert long_title == "a" * 24


def test_warmup_local_llm_skips_remote_call_in_mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    install_backend_import_stubs()
    sys.modules.pop("main", None)
    import main

    monkeypatch.setattr(main.settings, "LLM_MODE", "mock")

    class FailingClient:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("AsyncOpenAI should not be constructed in mock mode")

    monkeypatch.setattr(main, "AsyncOpenAI", FailingClient)

    asyncio.run(main.warmup_local_llm())


def test_summarize_conversation_returns_empty_message_without_user_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_backend_import_stubs()
    sys.modules.pop("app.api.conversations", None)
    from app.api import conversations as conversations_api

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_conversation_for_user(db, user_id: int, conversation_id: int):
        return type("Conversation", (), {"id": conversation_id})()

    async def fake_list_conversation_messages(db, conversation_id: int):
        return [
            type("Message", (), {"role": "assistant", "content": "系统提示"})(),
            type("Message", (), {"role": "assistant", "content": "继续补充"})(),
        ]

    monkeypatch.setattr(conversations_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(conversations_api, "get_conversation_for_user", fake_get_conversation_for_user)
    monkeypatch.setattr(conversations_api, "list_conversation_messages", fake_list_conversation_messages)

    result = asyncio.run(
        conversations_api.summarize_conversation(
            conversation_id=1,
            current_user=type("User", (), {"user_id": 7})(),
            db=object(),
        )
    )

    assert result == {"status": "success", "summary": "当前会话暂无可总结的内容。"}


def test_importing_main_does_not_require_legacy_history_or_summary_routes() -> None:
    source = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
    assert "history_router" not in source
    assert "summary_router" not in source
