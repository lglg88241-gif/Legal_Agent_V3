from __future__ import annotations

import importlib
import json
import sys
from enum import Enum
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
    "app.core.llm_provider",
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

    class FakeBM25Retriever:
        def search_records(self, *args, **kwargs):
            return []

    fake_bm25.BM25Retriever = FakeBM25Retriever
    sys.modules["app.core.bm25_retriever"] = fake_bm25

    fake_config = ModuleType("app.core.config")
    fake_config.settings = SimpleNamespace(
        CHROMA_CASES_COLLECTION="cases",
        CHROMA_PERSIST_DIR=".",
        NEO4J_URI="bolt://localhost:7687",
        NEO4J_USER="neo4j",
        NEO4J_PASSWORD="password",
    )
    sys.modules["app.core.config"] = fake_config

    fake_llm_factory = ModuleType("app.core.llm_factory")
    fake_llm_factory.create_cloud_llm = lambda *args, **kwargs: None
    fake_llm_factory.create_local_llm = lambda *args, **kwargs: None
    sys.modules["app.core.llm_factory"] = fake_llm_factory

    fake_llm_provider = ModuleType("app.core.llm_provider")

    class FakeProvider(Enum):
        LOCAL_VLLM = "local_vllm"
        GPT_5_4 = "gpt-5.4"
        GPT_5_1 = "gpt-5.1"
        ZHIPU = "zhipu"

    fake_llm_provider.LLMProvider = FakeProvider
    fake_llm_provider.get_llm_router = lambda: SimpleNamespace(
        get_provider=lambda scene: FakeProvider.GPT_5_4
    )
    sys.modules["app.core.llm_provider"] = fake_llm_provider

    fake_reranker = ModuleType("app.core.reranker")

    class FakeBGEReranker:
        def rerank_with_scores(self, query, documents, top_k=10):
            return [
                {"index": index, "score": float(top_k - index)}
                for index in range(min(len(documents), top_k))
            ]

    fake_reranker.BGEReranker = FakeBGEReranker
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


@pytest.mark.parametrize(
    ("query", "expected_law_title", "expected_number", "expected_label"),
    [
        ("劳动合同法第八十七条规定了什么？", "中华人民共和国劳动合同法", 87, "第八十七条"),
        ("劳动合同法第87条规定了什么？", "中华人民共和国劳动合同法", 87, "第八十七条"),
        ("中华人民共和国劳动合同法第八十二条怎么规定？", "中华人民共和国劳动合同法", 82, "第八十二条"),
    ],
)
def test_extract_explicit_law_article_reference(
    query: str,
    expected_law_title: str,
    expected_number: int,
    expected_label: str,
) -> None:
    tools = _load_tools_module()

    result = tools._extract_explicit_law_article_reference(query)

    assert result is not None
    assert result["law_title"] == expected_law_title
    assert result["article_number"] == expected_number
    assert result["article_label"] == expected_label


def test_search_law_articles_injects_exact_article_match_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _load_tools_module()

    class FakeLawVectorstore:
        def get(self, where=None, include=None):
            assert include == ["documents", "metadatas"]
            if where == {"article": "第八十七条"}:
                return {
                    "documents": [
                        (
                            "中华人民共和国劳动合同法\n\n"
                            "第八十七条 用人单位违反本法规定解除或者终止劳动合同的，"
                            "应当依照本法第四十七条规定的经济补偿标准的二倍向劳动者支付赔偿金。"
                        )
                    ],
                    "metadatas": [
                        {
                            "source": "劳动合同法.docx",
                            "article": "第八十七条",
                            "law_title": "中华人民共和国劳动合同法",
                        }
                    ],
                }
            if where == {"article": "第四十七条"}:
                return {
                    "documents": [
                        (
                            "中华人民共和国劳动合同法\n\n"
                            "第四十七条 经济补偿按劳动者在本单位工作的年限，"
                            "每满一年支付一个月工资的标准向劳动者支付。"
                        )
                    ],
                    "metadatas": [
                        {
                            "source": "劳动合同法.docx",
                            "article": "第四十七条",
                            "law_title": "中华人民共和国劳动合同法",
                        }
                    ],
                }
            return {"documents": [], "metadatas": []}

    def fake_retrieve_candidates(query, vectorstore, corpus, chat_history=""):
        assert corpus == "law"
        return (
            ["劳动合同解除赔偿标准", "违法解除劳动合同补偿规则"],
            [
                tools._build_candidate(
                    content="中华人民共和国劳动合同法\n\n第四十八条 ...",
                    source="劳动合同法.docx",
                    metadata={
                        "source": "劳动合同法.docx",
                        "article": "第四十八条",
                        "law_title": "中华人民共和国劳动合同法",
                    },
                    score=0.12,
                    retrieval="dense",
                    matched_query="劳动合同解除赔偿标准",
                ),
                tools._build_candidate(
                    content="中华人民共和国劳动合同法\n\n第四十七条 ...",
                    source="劳动合同法.docx",
                    metadata={
                        "source": "劳动合同法.docx",
                        "article": "第四十七条",
                        "law_title": "中华人民共和国劳动合同法",
                    },
                    score=0.11,
                    retrieval="dense",
                    matched_query="违法解除劳动合同补偿规则",
                ),
            ],
        )

    def fake_rerank(queries, candidates, top_k, rrf_k=60):
        ranked_candidates = list(reversed(candidates))
        return [
            {
                "index": index,
                "candidate": candidate,
                "fused_score": 1.0 / (index + 1),
                "rerank_score": float(top_k - index),
            }
            for index, candidate in enumerate(ranked_candidates[:top_k])
        ]

    monkeypatch.setattr(tools, "_get_law_vectorstore", lambda: FakeLawVectorstore())
    monkeypatch.setattr(tools, "_retrieve_candidates", fake_retrieve_candidates)
    monkeypatch.setattr(tools, "_multi_query_rerank_with_rrf", fake_rerank)

    payload = json.loads(tools.search_law_articles("劳动合同法第八十七条规定了什么？"))

    assert payload
    assert payload[0]["retrieval"] == "exact"
    assert payload[0]["metadata"]["article"] == "第八十七条"
    assert "支付赔偿金" in payload[0]["content"]
    assert payload[1]["retrieval"] == "exact_reference"
    assert payload[1]["metadata"]["article"] == "第四十七条"
    assert "经济补偿" in payload[1]["content"]
    assert any(item["metadata"]["article"] == "第四十八条" for item in payload[1:])


def test_expand_queries_skips_llm_for_explicit_law_article(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _load_tools_module()

    def fail_if_called(*args, **kwargs):
        raise AssertionError("query expansion model should not be called for explicit law citations")

    monkeypatch.setattr(tools, "_invoke_query_expansion_model", fail_if_called)

    expanded = tools._expand_queries("劳动合同法第82条规定了什么？")

    assert expanded == ["劳动合同法第82条规定了什么？"]


def test_search_law_articles_keeps_exact_article_first_for_article_82(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _load_tools_module()

    class FakeLawVectorstore:
        def get(self, where=None, include=None):
            assert include == ["documents", "metadatas"]
            if where == {"article": "第八十二条"}:
                return {
                    "documents": [
                        (
                            "中华人民共和国劳动合同法\n\n"
                            "第八十二条 用人单位自用工之日起超过一个月不满一年未与劳动者订立书面劳动合同的，"
                            "应当向劳动者每月支付二倍的工资。"
                        )
                    ],
                    "metadatas": [
                        {
                            "source": "劳动合同法.docx",
                            "article": "第八十二条",
                            "law_title": "中华人民共和国劳动合同法",
                        }
                    ],
                }
            return {"documents": [], "metadatas": []}

    def fake_retrieve_candidates(query, vectorstore, corpus, chat_history=""):
        assert corpus == "law"
        return (
            ["劳动合同法第82条规定了什么？"],
            [
                tools._build_candidate(
                    content="加班工资计算标准 ...",
                    source="噪声材料.docx",
                    metadata={"source": "噪声材料.docx"},
                    score=0.9,
                    retrieval="dense",
                    matched_query="加班工资计算标准",
                )
            ],
        )

    def fake_rerank(queries, candidates, top_k, rrf_k=60):
        return [
            {
                "index": index,
                "candidate": candidate,
                "fused_score": 1.0 / (index + 1),
                "rerank_score": float(top_k - index),
            }
            for index, candidate in enumerate(candidates[:top_k])
        ]

    monkeypatch.setattr(tools, "_get_law_vectorstore", lambda: FakeLawVectorstore())
    monkeypatch.setattr(tools, "_retrieve_candidates", fake_retrieve_candidates)
    monkeypatch.setattr(tools, "_multi_query_rerank_with_rrf", fake_rerank)

    payload = json.loads(tools.search_law_articles("劳动合同法第82条规定了什么？"))

    assert payload
    assert payload[0]["retrieval"] == "exact"
    assert payload[0]["metadata"]["article"] == "第八十二条"
    assert "二倍的工资" in payload[0]["content"]
