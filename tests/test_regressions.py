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
    fake_json_repair = ModuleType("json_repair")
    fake_json_repair.repair_json = lambda content: content
    sys.modules["json_repair"] = fake_json_repair

    fake_openai = ModuleType("openai")

    class FakeAsyncOpenAI:
        def __init__(self, *args, **kwargs) -> None:
            pass

    fake_openai.AsyncOpenAI = FakeAsyncOpenAI
    sys.modules["openai"] = fake_openai

    fake_langchain_community = ModuleType("langchain_community")
    fake_langchain_chat_models = ModuleType("langchain_community.chat_models")

    class FakeChatZhipuAI:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def ainvoke(self, *args, **kwargs):
            return type("Response", (), {"content": "{}"})()

    fake_langchain_chat_models.ChatZhipuAI = FakeChatZhipuAI
    fake_langchain_community.chat_models = fake_langchain_chat_models
    sys.modules["langchain_community"] = fake_langchain_community
    sys.modules["langchain_community.chat_models"] = fake_langchain_chat_models

    fake_langchain_core = ModuleType("langchain_core")
    fake_langchain_messages = ModuleType("langchain_core.messages")

    class FakeMessage:
        def __init__(self, content: str = "") -> None:
            self.content = content

    fake_langchain_messages.HumanMessage = FakeMessage
    fake_langchain_messages.SystemMessage = FakeMessage
    fake_langchain_core.messages = fake_langchain_messages
    sys.modules["langchain_core"] = fake_langchain_core
    sys.modules["langchain_core.messages"] = fake_langchain_messages

    fake_langgraph = ModuleType("langgraph")
    fake_langgraph_graph = ModuleType("langgraph.graph")

    class FakeCompiledGraph:
        async def ainvoke(self, state, config=None):
            return state

    class FakeStateGraph:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def add_node(self, *args, **kwargs) -> None:
            return None

        def set_entry_point(self, *args, **kwargs) -> None:
            return None

        def add_conditional_edges(self, *args, **kwargs) -> None:
            return None

        def add_edge(self, *args, **kwargs) -> None:
            return None

        def compile(self):
            return FakeCompiledGraph()

    fake_langgraph_graph.END = "__END__"
    fake_langgraph_graph.StateGraph = FakeStateGraph
    fake_langgraph.graph = fake_langgraph_graph
    sys.modules["langgraph"] = fake_langgraph
    sys.modules["langgraph.graph"] = fake_langgraph_graph

    fake_neo4j = ModuleType("neo4j")
    fake_neo4j.GraphDatabase = type("GraphDatabase", (), {"driver": staticmethod(lambda *args, **kwargs: None)})
    sys.modules["neo4j"] = fake_neo4j

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


def test_advisor_fallback_uses_case_summary_for_labor_analysis() -> None:
    install_backend_import_stubs()
    sys.modules.pop("app.services.legal_agent", None)
    from app.services import legal_agent

    answer = legal_agent._build_advisor_fallback(
        "员工于2024年1月入职，月工资12000元，工作了5个月，公司一直未签劳动合同，后公司准备注销。",
        "公司没签合同我能要什么赔偿？",
        "",
    )

    assert "二倍工资差额" in answer
    assert "48000" in answer
    assert "财产保全" in answer


def test_advisor_fallback_without_salary_still_gives_operational_guidance() -> None:
    install_backend_import_stubs()
    sys.modules.pop("app.services.legal_agent", None)
    from app.services import legal_agent

    answer = legal_agent._build_advisor_fallback(
        "员工已经入职但公司一直没签劳动合同，也没有提供纸质合同。",
        "没签合同怎么办？",
        "",
    )

    assert "二倍工资差额" in answer
    assert "月工资" in answer
    assert "考勤记录" in answer


def test_graph_prompts_can_be_formatted_without_keyerror() -> None:
    install_backend_import_stubs()
    sys.modules.pop("app.services.graph_extractor", None)
    from app.services import graph_extractor

    formatted_incremental = graph_extractor.INCREMENTAL_EXTRACTION_PROMPT.format(user_input="补充案情")
    formatted_gc = graph_extractor.SEMANTIC_GC_PROMPT.format(
        case_summary="公司更名",
        current_nodes='["旧公司", "新公司"]',
        current_graph="[]",
    )
    formatted_revision = graph_extractor.BELIEF_REVISION_PROMPT.format(
        current_graph="[]",
        user_input="把旧公司改成新公司",
    )
    formatted_revision_v2 = graph_extractor.BELIEF_REVISION_PROMPT_V2.format(
        existing_nodes='["旧公司", "新公司"]',
        current_graph="[]",
        user_input="删除旧公司",
    )

    assert '"actions"' in formatted_incremental
    assert '"obsolete_nodes"' in formatted_gc
    assert '"old_target"' in formatted_revision
    assert '"target_name"' in formatted_revision_v2


def test_delete_node_uses_exact_match_only() -> None:
    install_backend_import_stubs()
    sys.modules.pop("app.services.graph_extractor", None)
    from app.services import graph_extractor

    class FakeResult:
        def consume(self):
            return None

    class FakeSession:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        def run(self, query, params):
            self.calls.append((query, params))
            return FakeResult()

    session = FakeSession()
    graph_extractor._delete_node(session, 7, "机器")

    assert len(session.calls) == 2
    for query, params in session.calls:
        assert "CONTAINS" not in query
        assert params["target_name"] == "机器"


def test_precise_mode_forces_case_update_route() -> None:
    install_backend_import_stubs()
    sys.modules.pop("app.services.legal_agent", None)
    from app.services import legal_agent

    agent = legal_agent.LegalGraphAgent()

    decision = asyncio.run(
        agent._patched_route_decision(
            "怎么维权",
            chat_history="",
            case_summary="",
            is_precise_mode=True,
        )
    )

    assert decision["route_intent"] == "CASE_UPDATE"
    assert decision["tool_calls"]


def test_case_continuity_keeps_short_followup_on_case_update_route() -> None:
    install_backend_import_stubs()
    sys.modules.pop("app.services.legal_agent", None)
    from app.services import legal_agent

    agent = legal_agent.LegalGraphAgent()

    decision = asyncio.run(
        agent._patched_route_decision(
            "怎么办",
            chat_history="User: 公司一直没签劳动合同 Assistant: 你可以主张劳动关系和赔偿",
            case_summary="员工已入职，公司未签劳动合同，现咨询维权路径。",
            is_precise_mode=False,
        )
    )

    assert decision["route_intent"] == "CASE_UPDATE"
    assert decision["tool_calls"]


def test_layered_advisor_payload_calculates_before_asking_for_more() -> None:
    install_backend_import_stubs()
    sys.modules.pop("app.services.legal_agent", None)
    from app.services import legal_agent

    payload = legal_agent._build_layered_advisor_payload_v3(
        "员工2024年1月入职，月工资12000元，工作了5个月，公司一直未签劳动合同。",
        "现在能赔多少？",
        "",
    )

    assert "二倍工资差额" in payload["answer"]
    assert "48000" in payload["answer"]
    assert payload["needs_core_followup"] is False
    assert "劳动仲裁申请书" in payload["constructive_next_step"]


def test_layered_advisor_payload_outputs_enforcement_strategy_without_refusing() -> None:
    install_backend_import_stubs()
    sys.modules.pop("app.services.legal_agent", None)
    from app.services import legal_agent

    payload = legal_agent._build_layered_advisor_payload_v3(
        "原公司拖欠工资后注销，老板又用新公司继续安排原团队工作。",
        "我能不能查封新公司资产并一起追责？",
        "",
    )

    assert "人格混同" in payload["answer"]
    assert "材料" in payload["answer"]
    assert "依据不足" not in payload["answer"]


def test_clarifier_uses_sandwich_style_when_core_fields_missing() -> None:
    install_backend_import_stubs()
    sys.modules.pop("app.services.legal_agent", None)
    from app.services import legal_agent

    agent = legal_agent.LegalGraphAgent()
    question = asyncio.run(
        agent._generate_clarifier_question(
            {
                "question": "没签合同怎么办？",
                "case_summary": "员工已经入职，公司一直没有签劳动合同。",
                "graph_context": "",
                "graph_memory": "",
            }
        )
    )

    assert "有法律价值" in question
    assert "足以先帮你判断" in question
    assert "为了把金额和请求做得更准" in question
