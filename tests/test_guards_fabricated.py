from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.guards import detect_fabricated_statute


def test_detect_fabricated_statute_labor_contract_law_325() -> None:
    result = detect_fabricated_statute(
        "根据2025最新修订的劳动合同法第三百二十五条，连续工作满20年的员工可以要求无限期劳动合同，是这样吗"
    )

    assert result["is_fabricated"] is True
    assert "98条" in result["reason"]
    assert "第325条" in result["reason"]
    assert "第十四条" in result["suggested_correction"]


def test_detect_fabricated_statute_criminal_law_500() -> None:
    result = detect_fabricated_statute("刑法第500条是怎么规定诈骗罪的？")

    assert result["is_fabricated"] is True
    assert "452条" in result["reason"]
    assert "第500条" in result["reason"]


def test_detect_fabricated_statute_real_article_14() -> None:
    result = detect_fabricated_statute("劳动合同法第十四条关于无固定期限劳动合同是怎么规定的？")

    assert result["is_fabricated"] is False
    assert result["reason"] == ""
    assert result["suggested_correction"] == ""


def test_detect_fabricated_statute_without_article_number() -> None:
    result = detect_fabricated_statute("劳动合同法关于无固定期限劳动合同是怎么规定的？")

    assert result["is_fabricated"] is False
    assert result["reason"] == ""
    assert result["suggested_correction"] == ""
