from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.guards import sanitize_hallucinated_statutes


def test_impossible_article_劳动合同法_325条() -> None:
    result = sanitize_hallucinated_statutes(
        "《劳动合同法》第325条规定可以直接要求无限期劳动合同。",
        "《劳动合同法》第325条是这样吗？",
        [],
    )
    assert "法条编号纠错" in result
    assert "《劳动合同法》第325条不存在" in result
    assert "共98条" in result


def test_impossible_article_刑法_500条() -> None:
    result = sanitize_hallucinated_statutes(
        "《刑法》第500条规定了相关量刑。",
        "《刑法》第500条怎么规定的？",
        [],
    )
    assert "法条编号纠错" in result
    assert "《刑法》第500条不存在" in result
    assert "共452条" in result


def test_valid_article_劳动合同法_82条() -> None:
    result = sanitize_hallucinated_statutes(
        "《劳动合同法》第82条规定，用人单位应当依法承担相应责任。",
        "《劳动合同法》第82条怎么规定？",
        [],
    )
    assert "法条编号纠错" not in result


def test_中文数字_三百二十五() -> None:
    result = sanitize_hallucinated_statutes(
        "《劳动合同法》第三百二十五条规定了相关责任。",
        "《劳动合同法》第三百二十五条是这样吗？",
        [],
    )
    assert "法条编号纠错" in result
    assert "《劳动合同法》第325条不存在" in result
    assert "共98条" in result
