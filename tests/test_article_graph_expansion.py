from __future__ import annotations

import json
import logging
import sys
from types import ModuleType

_PREVIOUS_JSON_REPAIR = sys.modules.get("json_repair")
_PREVIOUS_BM25 = sys.modules.get("app.core.bm25_retriever")

fake_json_repair = ModuleType("json_repair")
fake_json_repair.repair_json = lambda content: content
sys.modules["json_repair"] = fake_json_repair

fake_bm25 = ModuleType("app.core.bm25_retriever")

class FakeBM25Retriever:
    def search_records(self, *args, **kwargs):
        return []

fake_bm25.BM25Retriever = FakeBM25Retriever
sys.modules["app.core.bm25_retriever"] = fake_bm25

import app.agent.tools as tools

if _PREVIOUS_JSON_REPAIR is not None:
    sys.modules["json_repair"] = _PREVIOUS_JSON_REPAIR
else:
    sys.modules.pop("json_repair", None)

if _PREVIOUS_BM25 is not None:
    sys.modules["app.core.bm25_retriever"] = _PREVIOUS_BM25
else:
    sys.modules.pop("app.core.bm25_retriever", None)


def _candidate(
    law_title: str,
    article: str,
    *,
    candidate_id: str,
    retrieval: str,
) -> dict:
    return {
        "candidate_id": candidate_id,
        "content": f"{law_title}{article} 条文内容",
        "source": "law.docx",
        "metadata": {
            "law_title": law_title,
            "article": article,
            "source": "law.docx",
        },
        "score": 1.0,
        "retrieval": retrieval,
        "matched_query": f"{law_title} {article}",
    }


def _invoke_search(query: str = "没签合同被违法辞退") -> str:
    return tools.search_law_articles.invoke({"query": query, "chat_history": ""})


def test_expand_returns_neighbors(monkeypatch) -> None:
    base_candidates = [
        _candidate(
            "中华人民共和国劳动合同法",
            "第八十七条",
            candidate_id="base-87",
            retrieval="dense",
        )
    ]
    captured: dict[str, list[dict]] = {}

    class StubGraph:
        def expand(self, seeds, max_neighbors=20):
            assert ("中华人民共和国劳动合同法", 87) in seeds
            return [
                ("中华人民共和国劳动合同法", 47),
                ("中华人民共和国劳动合同法", 48),
            ]

    monkeypatch.setattr(tools, "_get_law_vectorstore", lambda: object())
    monkeypatch.setattr(tools, "_retrieve_candidates", lambda *args, **kwargs: (["query"], base_candidates))
    monkeypatch.setattr(tools, "boost_law_recall", lambda *args, **kwargs: [])
    monkeypatch.setattr(tools, "get_article_graph", lambda: StubGraph())
    monkeypatch.setattr(
        tools,
        "_fetch_articles_by_metadata",
        lambda refs: [
            _candidate("中华人民共和国劳动合同法", "第四十七条", candidate_id="boost-47", retrieval="boost"),
            _candidate("中华人民共和国劳动合同法", "第四十八条", candidate_id="boost-48", retrieval="boost"),
        ],
    )

    def fake_serialize(query, candidates, top_k, rerank_k=None, boosted_minimum=0):
        captured["candidates"] = candidates
        return json.dumps([], ensure_ascii=False)

    monkeypatch.setattr(tools, "_serialize_reranked_documents", fake_serialize)

    _invoke_search()

    merged_articles = {candidate["metadata"]["article"] for candidate in captured["candidates"]}
    assert "第四十七条" in merged_articles
    assert "第四十八条" in merged_articles


def test_expand_failure_falls_back_to_base(monkeypatch, caplog) -> None:
    base_candidates = [
        _candidate(
            "中华人民共和国劳动合同法",
            "第八十七条",
            candidate_id="base-87",
            retrieval="dense",
        )
    ]
    captured: dict[str, list[dict]] = {}

    class BrokenGraph:
        def expand(self, seeds, max_neighbors=20):
            raise RuntimeError("neo4j unavailable")

    monkeypatch.setattr(tools, "_get_law_vectorstore", lambda: object())
    monkeypatch.setattr(tools, "_retrieve_candidates", lambda *args, **kwargs: (["query"], base_candidates))
    monkeypatch.setattr(tools, "boost_law_recall", lambda *args, **kwargs: [])
    monkeypatch.setattr(tools, "get_article_graph", lambda: BrokenGraph())

    def fake_serialize(query, candidates, top_k, rerank_k=None, boosted_minimum=0):
        captured["candidates"] = candidates
        return json.dumps([{"metadata": candidate["metadata"]} for candidate in candidates], ensure_ascii=False)

    monkeypatch.setattr(tools, "_serialize_reranked_documents", fake_serialize)

    with caplog.at_level(logging.WARNING, logger="app.agent.tools"):
        result = _invoke_search("违法辞退")

    assert "article graph expansion failed" in caplog.text
    assert captured["candidates"] == base_candidates
    assert result


def test_dedupe_keeps_boosted_first() -> None:
    boosted = _candidate(
        "中华人民共和国劳动合同法",
        "第四十七条",
        candidate_id="boosted-47",
        retrieval="boost",
    )
    base = _candidate(
        "中华人民共和国劳动合同法",
        "第四十七条",
        candidate_id="base-47",
        retrieval="dense",
    )

    deduped = tools._dedupe_by_metadata([boosted, base])

    assert len(deduped) == 1
    assert deduped[0]["candidate_id"] == "boosted-47"
