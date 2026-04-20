from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


_STUB_MODULE_NAMES = (
    "json_repair",
    "langchain_chroma",
    "langchain_core",
    "langchain_core.messages",
    "langchain_core.tools",
    "neo4j",
    "app.core.bm25_retriever",
    "app.core.config",
    "app.core.llm_factory",
    "app.core.reranker",
    "app.database.chroma_utils",
    "app.agent.tools",
)


def _install_tools_import_stubs() -> None:
    fake_json_repair = ModuleType("json_repair")
    fake_json_repair.repair_json = lambda content: content
    sys.modules["json_repair"] = fake_json_repair

    fake_langchain_chroma = ModuleType("langchain_chroma")
    fake_langchain_chroma.Chroma = type("Chroma", (), {})
    sys.modules["langchain_chroma"] = fake_langchain_chroma

    fake_langchain_core = ModuleType("langchain_core")
    fake_messages = ModuleType("langchain_core.messages")

    class FakeMessage:
        def __init__(self, content: str = "") -> None:
            self.content = content

    fake_messages.HumanMessage = FakeMessage
    fake_messages.SystemMessage = FakeMessage
    fake_langchain_core.messages = fake_messages
    sys.modules["langchain_core"] = fake_langchain_core
    sys.modules["langchain_core.messages"] = fake_messages

    fake_tools = ModuleType("langchain_core.tools")
    fake_tools.tool = lambda fn: fn
    sys.modules["langchain_core.tools"] = fake_tools

    fake_neo4j = ModuleType("neo4j")
    fake_neo4j.GraphDatabase = type("GraphDatabase", (), {"driver": staticmethod(lambda *args, **kwargs: None)})
    sys.modules["neo4j"] = fake_neo4j

    fake_bm25 = ModuleType("app.core.bm25_retriever")
    fake_bm25.BM25Retriever = type("BM25Retriever", (), {})
    sys.modules["app.core.bm25_retriever"] = fake_bm25

    fake_config = ModuleType("app.core.config")
    fake_config.settings = SimpleNamespace(
        CHROMA_CASES_COLLECTION="cases",
        CHROMA_PERSIST_DIR=".",
        NEO4J_URI="",
        NEO4J_USER="",
        NEO4J_PASSWORD="",
    )
    sys.modules["app.core.config"] = fake_config

    fake_llm_factory = ModuleType("app.core.llm_factory")
    fake_llm_factory.create_cloud_llm = lambda *args, **kwargs: None
    fake_llm_factory.create_query_expansion_llm = lambda *args, **kwargs: None
    sys.modules["app.core.llm_factory"] = fake_llm_factory

    fake_reranker = ModuleType("app.core.reranker")
    fake_reranker.BGEReranker = type("BGEReranker", (), {})
    sys.modules["app.core.reranker"] = fake_reranker

    fake_chroma_utils = ModuleType("app.database.chroma_utils")
    fake_chroma_utils.resolve_persist_dir = lambda path: path
    sys.modules["app.database.chroma_utils"] = fake_chroma_utils


def _load_tools_module():
    _install_tools_import_stubs()
    sys.modules.pop("app.agent.tools", None)
    return importlib.import_module("app.agent.tools")


@pytest.fixture(autouse=True)
def _restore_stubbed_modules():
    previous = {name: sys.modules.get(name) for name in _STUB_MODULE_NAMES}
    yield
    for name in _STUB_MODULE_NAMES:
        sys.modules.pop(name, None)
    for name, module in previous.items():
        if module is not None:
            sys.modules[name] = module


def test_sanitize_expanded_queries_filters_article_numbers() -> None:
    tools = _load_tools_module()

    queries = [
        "《劳动合同法》第四十六条 公司拖欠工资的处理条款号",
        "拖欠工资 劳动者权利 维权途径",
        "《劳动法》第五十条 工资支付规定",
        "工资支付保障 劳动监察 投诉",
    ]

    sanitized = tools._sanitize_expanded_queries("公司拖欠工资请告诉我条款号", queries)

    assert len(sanitized) == 2
    assert all("第" not in query or "条" not in query for query in sanitized)


def test_expand_queries_falls_back_to_original_when_filtered_below_two() -> None:
    tools = _load_tools_module()

    class FakeLLM:
        def invoke(self, messages):
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "queries": [
                            "《劳动合同法》第四十六条 公司拖欠工资的处理条款号",
                            "《劳动合同法》第五十条 关于拖欠工资的赔偿条款号",
                        ]
                    },
                    ensure_ascii=False,
                )
            )

    tools._get_query_expansion_llm = lambda: FakeLLM()
    tools._get_query_expansion_fallback_llm = lambda: FakeLLM()

    expanded = tools._expand_queries("公司拖欠工资请告诉我条款号")

    assert expanded == ["公司拖欠工资请告诉我条款号"]
    assert all("第" not in query or "条" not in query for query in expanded)
