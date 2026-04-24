import importlib
import sys
from unittest.mock import MagicMock, patch


def _fresh_tools_module():
    """Drop any stubbed app.agent.tools and reload the real module."""
    sys.modules.pop("app.agent.tools", None)
    return importlib.import_module("app.agent.tools")


_tools = _fresh_tools_module()
_multi_query_rerank_with_rrf = _tools._multi_query_rerank_with_rrf


class TestRRFRerank:
    def _fake_reranker(self, score_map):
        def _predict_side_effect(query, documents, top_k):
            scored = [
                {"index": i, "document": d, "score": score_map.get((query, i), 0.0)}
                for i, d in enumerate(documents)
            ]
            scored.sort(key=lambda x: x["score"], reverse=True)
            return scored[:top_k]

        mock = MagicMock()
        mock.rerank_with_scores.side_effect = _predict_side_effect
        return mock

    def test_empty_returns_empty(self):
        assert _multi_query_rerank_with_rrf([], [{"content": "x"}], top_k=3) == []
        assert _multi_query_rerank_with_rrf(["q"], [], top_k=3) == []

    def test_single_query_preserves_order(self):
        candidates = [{"content": f"doc{i}"} for i in range(3)]
        score_map = {("q1", 0): 0.9, ("q1", 1): 0.5, ("q1", 2): 0.1}
        with patch.object(_tools, "BGEReranker", return_value=self._fake_reranker(score_map)):
            result = _multi_query_rerank_with_rrf(["q1"], candidates, top_k=3)
        assert [r["index"] for r in result] == [0, 1, 2]

    def test_rrf_promotes_consensus(self):
        candidates = [{"content": f"doc{i}"} for i in range(4)]
        score_map = {
            ("q1", 0): 0.9, ("q1", 1): 0.5, ("q1", 3): 0.2, ("q1", 2): 0.1,
            ("q2", 2): 0.9, ("q2", 1): 0.5, ("q2", 3): 0.2, ("q2", 0): 0.1,
        }
        with patch.object(_tools, "BGEReranker", return_value=self._fake_reranker(score_map)):
            result = _multi_query_rerank_with_rrf(["q1", "q2"], candidates, top_k=4)
        assert result[0]["index"] == 1

    def test_blank_queries_skipped(self):
        candidates = [{"content": "x"}]
        score_map = {("q1", 0): 1.0}
        with patch.object(_tools, "BGEReranker", return_value=self._fake_reranker(score_map)):
            result = _multi_query_rerank_with_rrf(["", "q1", "  "], candidates, top_k=1)
        assert len(result) == 1
