from __future__ import annotations

import asyncio
import importlib
import sys
from datetime import datetime
from types import ModuleType

import pytest
from fastapi import HTTPException

from app.core.security import AuthenticatedUser
from app.models import Conversation, Dossier, Message


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


def _load_dossier_summary_module():
    _install_common_stubs()
    sys.modules.pop("app.services.dossier_summary", None)
    module = importlib.import_module("app.services.dossier_summary")
    sys.modules.pop("app.services.llm_service", None)
    return module


def _load_dossiers_api_module():
    _install_common_stubs()
    sys.modules.pop("app.api.dossiers", None)
    module = importlib.import_module("app.api.dossiers")
    sys.modules.pop("app.services.llm_service", None)
    return module


class DummyDB:
    def add(self, obj) -> None:
        return None

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def refresh(self, obj) -> None:
        return None


def test_generate_dossier_auto_summary_uses_bound_conversation_materials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossier_summary = _load_dossier_summary_module()
    captured: dict[str, str] = {}
    dossier = Dossier(
        id=21,
        user_id=7,
        title="劳动争议案卷",
        case_type="劳动争议",
        summary="用户手动摘要",
        status="active",
    )
    conversations = [
        Conversation(
            id=101,
            user_id=7,
            dossier_id=21,
            session_id="session-101",
            title="第一次咨询",
            case_summary="公司未签劳动合同并拟辞退",
            case_state_json={"monthly_salary": 12000, "work_months": 24},
            case_type="劳动争议",
            conversation_mode="TRACKED_CASE",
            persistence_consent="tracked",
        ),
        Conversation(
            id=102,
            user_id=7,
            dossier_id=21,
            session_id="session-102",
            title="补充证据",
            case_summary="",
            case_state_json=None,
            case_type="劳动争议",
            conversation_mode="CASE_ANALYSIS",
            persistence_consent="temporary",
        ),
    ]

    async def fake_list_dossier_conversations(db, user_id: int, dossier_id: int):
        assert user_id == 7
        assert dossier_id == 21
        return conversations

    async def fake_list_recent_conversation_messages(db, conversation_id: int, limit: int = 4):
        if conversation_id == 101:
            return [
                Message(role="user", content="我在公司工作两年，公司一直没签合同。"),
                Message(role="assistant", content="需要先固定工资流水和解除通知。"),
            ]
        return [
            Message(role="user", content="我还有聊天记录和工资流水。"),
        ]

    class FakeLLMService:
        async def async_generate(self, prompt: str, system_prompt: str = "") -> str:
            captured["prompt"] = prompt
            captured["system_prompt"] = system_prompt
            return (
                "案情概览\n劳动关系解除争议。\n\n"
                "已知事实\n存在未签劳动合同与辞退线索。\n\n"
                "争议焦点\n违法解除与赔偿。\n\n"
                "证据线索\n工资流水、聊天记录。\n\n"
                "已讨论法律问题\n未签合同二倍工资与违法解除赔偿。\n\n"
                "后续待确认事项\n解除时间与通知形式待补充。"
            )

    monkeypatch.setattr(dossier_summary, "list_dossier_conversations", fake_list_dossier_conversations)
    monkeypatch.setattr(
        dossier_summary,
        "list_recent_conversation_messages",
        fake_list_recent_conversation_messages,
    )
    monkeypatch.setattr(dossier_summary, "llm_service", FakeLLMService())

    result = asyncio.run(
        dossier_summary.generate_dossier_auto_summary(
            DummyDB(),
            user_id=7,
            dossier=dossier,
        )
    )

    assert result["auto_summary_source_count"] == 2
    assert "劳动争议案卷" in captured["prompt"]
    assert "用户手动摘要" in captured["prompt"]
    assert "结构化案情" in captured["prompt"]
    assert "最近用户消息" in captured["prompt"]
    assert "不能编造" in captured["system_prompt"] or "不得编造" in captured["system_prompt"]


def test_generate_dossier_auto_summary_requires_bound_conversation_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossier_summary = _load_dossier_summary_module()
    dossier = Dossier(
        id=22,
        user_id=7,
        title="空案卷",
        case_type="",
        summary="",
        status="active",
    )

    async def fake_list_dossier_conversations(db, user_id: int, dossier_id: int):
        return []

    monkeypatch.setattr(dossier_summary, "list_dossier_conversations", fake_list_dossier_conversations)

    with pytest.raises(ValueError) as exc_info:
        asyncio.run(
            dossier_summary.generate_dossier_auto_summary(
                DummyDB(),
                user_id=7,
                dossier=dossier,
            )
        )

    assert str(exc_info.value) == "暂无可用于生成摘要的绑定会话内容"


def test_generate_dossier_summary_api_rejects_other_users_dossier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        return None

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dossiers_api, "get_dossier_for_user", fake_get_dossier_for_user)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            dossiers_api.generate_dossier_summary(
                dossier_id=55,
                current_user=AuthenticatedUser(user_id=7, username="owner"),
                db=DummyDB(),
            )
        )

    assert exc_info.value.status_code == 404


def test_generate_dossier_summary_api_returns_clear_error_when_no_materials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()
    dossier = Dossier(
        id=56,
        user_id=7,
        title="劳动争议案卷",
        case_type="劳动争议",
        summary="手动摘要",
        status="active",
    )

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        return dossier

    async def fake_generate_dossier_auto_summary(db, *, user_id: int, dossier: Dossier):
        raise ValueError("暂无可用于生成摘要的绑定会话内容")

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dossiers_api, "get_dossier_for_user", fake_get_dossier_for_user)
    monkeypatch.setattr(dossiers_api, "generate_dossier_auto_summary", fake_generate_dossier_auto_summary)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            dossiers_api.generate_dossier_summary(
                dossier_id=56,
                current_user=AuthenticatedUser(user_id=7, username="owner"),
                db=DummyDB(),
            )
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "暂无可用于生成摘要的绑定会话内容"


def test_generate_dossier_summary_api_updates_auto_summary_without_overwriting_manual_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()
    dossier = Dossier(
        id=57,
        user_id=7,
        title="劳动争议案卷",
        case_type="劳动争议",
        summary="用户手动维护的摘要",
        status="active",
    )

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        return dossier

    async def fake_generate_dossier_auto_summary(db, *, user_id: int, dossier: Dossier):
        return {
            "auto_summary": "AI 自动整理出的摘要",
            "auto_summary_source_count": 2,
        }

    async def fake_update_dossier_auto_summary(
        db,
        dossier: Dossier,
        *,
        auto_summary: str,
        auto_summary_source_count: int,
    ):
        dossier.auto_summary = auto_summary
        dossier.auto_summary_source_count = auto_summary_source_count
        dossier.auto_summary_updated_at = datetime(2026, 4, 25, 12, 0, 0)
        return dossier

    async def fake_list_dossier_conversations(db, user_id: int, dossier_id: int):
        return [
            Conversation(
                id=201,
                user_id=7,
                dossier_id=57,
                session_id="session-201",
                title="第一次咨询",
                case_summary="公司未签劳动合同",
                case_type="劳动争议",
                conversation_mode="TRACKED_CASE",
                persistence_consent="tracked",
            )
        ]

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dossiers_api, "get_dossier_for_user", fake_get_dossier_for_user)
    monkeypatch.setattr(dossiers_api, "generate_dossier_auto_summary", fake_generate_dossier_auto_summary)
    monkeypatch.setattr(dossiers_api, "update_dossier_auto_summary", fake_update_dossier_auto_summary)
    monkeypatch.setattr(dossiers_api, "list_dossier_conversations", fake_list_dossier_conversations)

    result = asyncio.run(
        dossiers_api.generate_dossier_summary(
            dossier_id=57,
            current_user=AuthenticatedUser(user_id=7, username="owner"),
            db=DummyDB(),
        )
    )

    assert result["status"] == "success"
    assert result["dossier"]["summary"] == "用户手动维护的摘要"
    assert result["dossier"]["auto_summary"] == "AI 自动整理出的摘要"
    assert result["dossier"]["auto_summary_source_count"] == 2
    assert result["dossier"]["auto_summary_updated_at"] == "2026-04-25T12:00:00"


def test_generate_dossier_auto_summary_falls_back_when_llm_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossier_summary = _load_dossier_summary_module()
    dossier = Dossier(
        id=58,
        user_id=7,
        title="劳动争议案卷",
        case_type="劳动争议",
        summary="用户手动摘要",
        status="active",
    )

    async def fake_list_dossier_conversations(db, user_id: int, dossier_id: int):
        return [
            Conversation(
                id=301,
                user_id=7,
                dossier_id=58,
                session_id="session-301",
                title="首次咨询",
                case_summary="公司未签劳动合同并通知解除",
                case_state_json={
                    "monthly_salary": 12000,
                    "work_months": 24,
                    "has_unsigned_contract": True,
                    "is_illegal_dismissal": True,
                },
                case_type="劳动争议",
                conversation_mode="TRACKED_CASE",
                persistence_consent="tracked",
            )
        ]

    async def fake_list_recent_conversation_messages(db, conversation_id: int, limit: int = 4):
        return [
            Message(role="user", content="我在公司工作两年，公司一直没签合同。"),
            Message(role="assistant", content="需要先固定工资流水和解除通知。"),
        ]

    class FakeLLMService:
        async def async_generate(self, prompt: str, system_prompt: str = "") -> str:
            return "系统繁忙，请稍后再试。(Error: Connection error)"

    monkeypatch.setattr(dossier_summary, "list_dossier_conversations", fake_list_dossier_conversations)
    monkeypatch.setattr(
        dossier_summary,
        "list_recent_conversation_messages",
        fake_list_recent_conversation_messages,
    )
    monkeypatch.setattr(dossier_summary, "llm_service", FakeLLMService())

    result = asyncio.run(
        dossier_summary.generate_dossier_auto_summary(
            DummyDB(),
            user_id=7,
            dossier=dossier,
        )
    )

    assert result["auto_summary_source_count"] == 1
    assert result["warning"] == "LLM unavailable, used fallback summary"
    assert "以下为系统根据已有会话内容自动整理的简要摘要" in result["auto_summary"]
    assert "公司未签劳动合同并通知解除" in result["auto_summary"]
    assert "用户手动摘要" in result["auto_summary"]
    assert "月工资约为 12000 元" in result["auto_summary"]
    assert "存在未签订书面劳动合同线索" in result["auto_summary"]
    assert "存在违法解除或辞退线索" in result["auto_summary"]
    assert "{\"monthly_salary\"" not in result["auto_summary"]
    assert "mode=" not in result["auto_summary"]
    assert "consent=" not in result["auto_summary"]
    assert "claim_total" not in result["auto_summary"]
    assert "graph_context" not in result["auto_summary"]


def test_generate_dossier_summary_api_returns_warning_when_fallback_is_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dossiers_api = _load_dossiers_api_module()
    dossier = Dossier(
        id=59,
        user_id=7,
        title="劳动争议案卷",
        case_type="劳动争议",
        summary="用户手动摘要",
        status="active",
    )

    async def fake_ensure_storage_schema() -> None:
        return None

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        return dossier

    async def fake_generate_dossier_auto_summary(db, *, user_id: int, dossier: Dossier):
        return {
            "auto_summary": "以下为系统根据已有会话内容自动整理的简要摘要，未经过大模型深度归纳。\n\n案情概览\n- 用户手动摘要",
            "auto_summary_source_count": 1,
            "warning": "LLM unavailable, used fallback summary",
        }

    async def fake_update_dossier_auto_summary(
        db,
        dossier: Dossier,
        *,
        auto_summary: str,
        auto_summary_source_count: int,
    ):
        dossier.auto_summary = auto_summary
        dossier.auto_summary_source_count = auto_summary_source_count
        dossier.auto_summary_updated_at = datetime(2026, 4, 26, 8, 0, 0)
        return dossier

    async def fake_list_dossier_conversations(db, user_id: int, dossier_id: int):
        return [
            Conversation(
                id=302,
                user_id=7,
                dossier_id=59,
                session_id="session-302",
                title="首次咨询",
                case_summary="公司未签劳动合同",
                case_type="劳动争议",
                conversation_mode="TRACKED_CASE",
                persistence_consent="tracked",
            )
        ]

    monkeypatch.setattr(dossiers_api, "ensure_storage_schema", fake_ensure_storage_schema)
    monkeypatch.setattr(dossiers_api, "get_dossier_for_user", fake_get_dossier_for_user)
    monkeypatch.setattr(dossiers_api, "generate_dossier_auto_summary", fake_generate_dossier_auto_summary)
    monkeypatch.setattr(dossiers_api, "update_dossier_auto_summary", fake_update_dossier_auto_summary)
    monkeypatch.setattr(dossiers_api, "list_dossier_conversations", fake_list_dossier_conversations)

    result = asyncio.run(
        dossiers_api.generate_dossier_summary(
            dossier_id=59,
            current_user=AuthenticatedUser(user_id=7, username="owner"),
            db=DummyDB(),
        )
    )

    assert result["status"] == "success"
    assert result["warning"] == "LLM unavailable, used fallback summary"
    assert result["dossier"]["summary"] == "用户手动摘要"
    assert result["dossier"]["auto_summary_source_count"] == 1
