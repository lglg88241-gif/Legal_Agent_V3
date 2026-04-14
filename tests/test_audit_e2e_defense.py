"""
Audit B4: 端到端防御补丁的纯确定性测试。

覆盖三类防御：
1. 路由层 OUT_OF_SCOPE 拦截
2. advisor prompt 的未验证金额防御
3. advisor prompt 的未收录法条前向防御
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.nodes.advisor import build_advisor_system_prompt
from app.logic.calculators import build_claim_payload
from app.schemas.case_state import CaseState


def install_router_import_stubs() -> None:
    fake_json_repair = ModuleType("json_repair")
    fake_json_repair.repair_json = lambda content: content
    sys.modules["json_repair"] = fake_json_repair

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
    fake_langgraph_graph.END = "__END__"

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

    fake_langgraph_graph.StateGraph = FakeStateGraph
    fake_langgraph.graph = fake_langgraph_graph
    sys.modules["langgraph"] = fake_langgraph
    sys.modules["langgraph.graph"] = fake_langgraph_graph

    fake_tools = ModuleType("app.agent.tools")
    fake_tools.AGENT_TOOLS = [
        SimpleNamespace(name="search_law_articles"),
        SimpleNamespace(name="search_case_text"),
        SimpleNamespace(name="query_neo4j_graph"),
    ]
    sys.modules["app.agent.tools"] = fake_tools


@pytest.fixture
def router_module():
    install_router_import_stubs()
    for module_name in (
        "app.agent.nodes._llm",
        "app.agent.graph",
        "app.agent.nodes.router",
    ):
        sys.modules.pop(module_name, None)
    return importlib.import_module("app.agent.nodes.router")


@pytest.mark.parametrize(
    "question",
    [
        "美国加州的劳动法怎么规定的",
        "帮我写一段Python代码",
        "你觉得明天股票会涨吗",
        "日本的交通法规和中国有什么区别",
        "帮我翻译一段英文",
    ],
)
def test_out_of_scope_questions_are_intercepted(router_module, question: str) -> None:
    assert router_module.detect_out_of_scope_reason(question) is not None


@pytest.mark.parametrize(
    "question",
    [
        "公司不签劳动合同算不算违法",
        "醉驾怎么判",
        "打架反击算正当防卫吗",
        "劳动仲裁需要什么材料",
        "个人信息泄露怎么维权",
    ],
)
def test_chinese_legal_questions_are_not_intercepted(router_module, question: str) -> None:
    assert router_module.detect_out_of_scope_reason(question) is None


def test_prompt_injects_unverified_amount_notice_when_amount_not_yet_calculated() -> None:
    payload = build_claim_payload(
        CaseState.from_text(
            "公司一直没签劳动合同，但还没有补充工资和工作时长。",
            "我能要999999元赔偿吗？",
            "",
        )
    )

    prompt = build_advisor_system_prompt(
        case_summary="公司一直没签劳动合同，但还没有补充工资和工作时长。",
        graph_context="",
        rag_context="无有效检索结果",
        payload=payload,
        question="我能要999999元赔偿吗？",
    )

    assert '<amount_verification_notice priority="VERBATIM">' in prompt
    assert "999999元" in prompt
    assert "金额未经系统验证" in prompt


def test_prompt_skips_unverified_amount_notice_when_amount_already_verified() -> None:
    payload = build_claim_payload(
        CaseState.from_text(
            "员工入职后公司一直未签劳动合同，月薪12000元，工作了12个月。",
            "我能要132000元二倍工资差额吗？",
            "",
        )
    )

    prompt = build_advisor_system_prompt(
        case_summary="员工入职后公司一直未签劳动合同，月薪12000元，工作了12个月。",
        graph_context="",
        rag_context="无有效检索结果",
        payload=payload,
        question="我能要132000元二倍工资差额吗？",
    )

    assert '<amount_verification_notice priority="VERBATIM">' not in prompt


def test_prompt_injects_missing_statute_notice_when_rag_has_no_such_law() -> None:
    payload = build_claim_payload(
        CaseState.from_text(
            "",
            "《劳动者权益保护法》第88条怎么规定？",
            "",
        )
    )

    prompt = build_advisor_system_prompt(
        case_summary="",
        graph_context="",
        rag_context="无有效检索结果",
        payload=payload,
        question="《劳动者权益保护法》第88条怎么规定？",
    )

    assert '<statute_verification_notice priority="VERBATIM">' in prompt
    assert "《劳动者权益保护法》第88条" in prompt
    assert "国家法律法规数据库" in prompt


def test_prompt_skips_missing_statute_notice_when_rag_contains_same_statute() -> None:
    payload = build_claim_payload(
        CaseState.from_text(
            "",
            "《劳动合同法》第82条怎么规定？",
            "",
        )
    )

    prompt = build_advisor_system_prompt(
        case_summary="",
        graph_context="",
        rag_context="《劳动合同法》第82条：用人单位自用工之日起超过一个月不满一年未与劳动者订立书面劳动合同的，应当向劳动者每月支付二倍工资。",
        payload=payload,
        question="《劳动合同法》第82条怎么规定？",
    )

    assert '<statute_verification_notice priority="VERBATIM">' not in prompt
