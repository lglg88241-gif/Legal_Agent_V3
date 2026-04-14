from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.test_regressions import install_backend_import_stubs


def test_relative_time_span_is_converted_to_months() -> None:
    install_backend_import_stubs()
    sys.modules.pop("app.services.legal_agent", None)
    from app.services import legal_agent

    months = legal_agent._safe_extract_months_v3("去年9月进厂，干到今年3月底")
    assert months == 7

    payload = legal_agent._build_layered_advisor_payload_v3(
        "去年9月进厂，干到今年3月底，月工资10000元，公司一直没签劳动合同。",
        "能赔多少？",
        "",
    )
    assert payload["needs_core_followup"] is False
    assert "60000" in payload["answer"]


def test_advisor_prompt_contains_strong_rag_binding() -> None:
    # The advisor prompt now lives in app/agent/nodes/advisor.py
    source = (PROJECT_ROOT / "app" / "agent" / "nodes" / "advisor.py").read_text(encoding="utf-8")
    assert '<rag_binding priority="MAX">' in source
    assert "<rag_context>" in source
    assert "唯一允许直接锚定的外部依据" in source
    assert "必须显式穿插这些名称" in source
