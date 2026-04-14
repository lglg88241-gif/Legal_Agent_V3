from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.schemas.case_state import CaseState
from app.logic.calculators import build_claim_payload


def test_relative_time_span_is_converted_to_months() -> None:
    from app.schemas.case_state import _extract_months

    months = _extract_months("去年9月进厂，干到今年3月底")
    assert months == 7

    payload = build_claim_payload(
        CaseState.from_text(
            "去年9月进厂，干到今年3月底，月工资10000元，公司一直没签劳动合同。",
            "能赔多少？",
            "",
        )
    )
    assert payload.needs_core_followup is False
    assert "60000" in payload.answer


def test_advisor_prompt_contains_strong_rag_binding() -> None:
    """
    Verify the three-pillar hallucination prevention constraints are present
    in advisor.py after the Surgery 6 prompt rewrite.

    Checks:
    - <number_lock> block exists (number fabrication prevention)
    - <citation_lock> block exists (phantom article citation prevention)
    - <rag_context> placeholder exists (RAG anchoring)
    - Role demotion: LLM identified as text editor, not autonomous lawyer
    - Few-shot examples present
    """
    source = (PROJECT_ROOT / "app" / "agent" / "nodes" / "advisor.py").read_text(encoding="utf-8")
    # Number lock — core defence against math hallucination
    assert "<number_lock>" in source
    assert "严禁" in source
    # Citation lock — core defence against phantom article citations
    assert "<citation_lock>" in source
    assert "第X条" in source
    # RAG anchoring still present
    assert "<rag_context>" in source
    assert "<rag_binding>" in source
    # Role demotion: LLM is a text editor, not an autonomous lawyer
    assert "精校员" in source or "text editor" in source.lower()
    # Few-shot examples to demonstrate correct vs wrong behaviour
    assert "<examples>" in source
