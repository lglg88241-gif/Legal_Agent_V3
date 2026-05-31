from __future__ import annotations

import importlib
import sys
from types import ModuleType


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


def test_graph_subgraph_query_uses_element_id() -> None:
    graph_api = _load_graph_api_module()

    assert "RETURN elementId(n) AS source_id" in graph_api.GRAPH_SUBGRAPH_QUERY
    assert "elementId(m) AS target_id" in graph_api.GRAPH_SUBGRAPH_QUERY
    assert "RETURN id(n) AS source_id" not in graph_api.GRAPH_SUBGRAPH_QUERY
    assert "       id(m) AS target_id" not in graph_api.GRAPH_SUBGRAPH_QUERY


def test_fetch_subgraph_keeps_response_shape_with_string_element_ids(monkeypatch) -> None:
    graph_api = _load_graph_api_module()

    class FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def data(self):
            return self._rows

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def run(self, query: str, params: dict[str, object]):
            assert "elementId(n) AS source_id" in query
            assert "elementId(m) AS target_id" in query
            assert "tokens" in params
            return FakeResult(
                [
                    {
                        "source_id": "4:law-87",
                        "source_labels": ["LawArticle"],
                        "source_properties": {
                            "law_title": "中华人民共和国劳动合同法",
                            "article_label": "第八十七条",
                        },
                        "relation": "REFERENCES",
                        "target_id": "4:law-47",
                        "target_labels": ["LawArticle"],
                        "target_properties": {
                            "law_title": "中华人民共和国劳动合同法",
                            "article_label": "第四十七条",
                        },
                    }
                ]
            )

    class FakeDriver:
        def session(self):
            return FakeSession()

        def close(self):
            return None

    monkeypatch.setattr(graph_api.GraphDatabase, "driver", lambda *args, **kwargs: FakeDriver())

    result = graph_api._fetch_subgraph("劳动合同法第八十七条")

    assert result["query"] == "劳动合同法第八十七条"
    assert result["nodes"] == [
        {
            "id": "4:law-87",
            "label": "中华人民共和国劳动合同法第八十七条",
            "type": "law",
            "labels": ["LawArticle"],
            "properties": {
                "law_title": "中华人民共和国劳动合同法",
                "article_label": "第八十七条",
            },
        },
        {
            "id": "4:law-47",
            "label": "中华人民共和国劳动合同法第四十七条",
            "type": "law",
            "labels": ["LawArticle"],
            "properties": {
                "law_title": "中华人民共和国劳动合同法",
                "article_label": "第四十七条",
            },
        },
    ]
    assert result["edges"] == [
        {
            "id": "4:law-87-4:law-47-REFERENCES-0",
            "source": "4:law-87",
            "target": "4:law-47",
            "label": "REFERENCES",
        }
    ]
    assert isinstance(result["nodes"][0]["id"], str)
    assert isinstance(result["edges"][0]["source"], str)
    assert isinstance(result["edges"][0]["target"], str)
