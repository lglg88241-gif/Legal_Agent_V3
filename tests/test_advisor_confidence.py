"""Surgery #11: advisor 启发式 confidence 测试。"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.nodes.advisor import _compute_heuristic_confidence


class TestAdvisorHeuristicConfidence:
    """当 reflector 未执行时，advisor 应基于启发式赋值 confidence。"""

    def test_no_sources_no_payload_gives_low_confidence(self) -> None:
        """零检索结果 + 无 payload -> 0.35"""
        state = {
            "confidence_score": 0.0,
            "rag_sources": [],
            "claim_payload": None,
        }
        conf = _compute_heuristic_confidence(state)
        assert conf == 0.35

    def test_few_sources_gives_medium_confidence(self) -> None:
        """1-2 个检索结果 -> 0.55"""
        state = {
            "confidence_score": 0.0,
            "rag_sources": [{"title": "a"}, {"title": "b"}],
            "claim_payload": None,
        }
        conf = _compute_heuristic_confidence(state)
        assert conf == 0.55

    def test_many_sources_with_payload_gives_high_confidence(self) -> None:
        """3+ 检索结果 + payload -> 0.78"""
        state = {
            "confidence_score": 0.0,
            "rag_sources": [{"title": "a"}, {"title": "b"}, {"title": "c"}],
            "claim_payload": {"total": 5000},
        }
        conf = _compute_heuristic_confidence(state)
        assert conf == 0.78

    def test_many_sources_no_payload_gives_default(self) -> None:
        """3+ 检索结果但无 payload -> 0.65"""
        state = {
            "confidence_score": 0.0,
            "rag_sources": [{"title": "a"}, {"title": "b"}, {"title": "c"}],
            "claim_payload": None,
        }
        conf = _compute_heuristic_confidence(state)
        assert conf == 0.65

    def test_reflector_value_not_overwritten(self) -> None:
        """reflector 已赋值 0.62 -> 不应被覆盖。"""
        state = {
            "confidence_score": 0.62,
            "rag_sources": [],
            "claim_payload": None,
        }
        conf = _compute_heuristic_confidence(state)
        assert conf is None or conf == 0.62
