from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.nodes.advisor import (
    _format_dossier_context_block,
    build_advisor_system_prompt,
)
from app.logic.calculators import build_claim_payload
from app.schemas.case_state import CaseState


def _sample_payload():
    return build_claim_payload(
        CaseState.from_text(
            "\u6211\u5728\u516c\u53f8\u5de5\u4f5c\u4e24\u5e74\uff0c\u516c\u53f8\u4e00\u76f4\u6ca1\u6709\u7b7e\u52b3\u52a8\u5408\u540c\u3002",
            "\u516c\u53f8\u7a81\u7136\u901a\u77e5\u6211\u660e\u5929\u4e0d\u7528\u6765\u4e86\u600e\u4e48\u529e\uff1f",
            "",
        )
    )


def test_format_dossier_context_block_contains_summary_and_bound_conversations() -> None:
    block = _format_dossier_context_block(
        {
            "id": 12,
            "title": "\u52b3\u52a8\u4e89\u8bae\u6848\u5377",
            "case_type": "\u52b3\u52a8\u4e89\u8bae",
            "summary": "\u56f4\u7ed5\u89e3\u9664\u52b3\u52a8\u5408\u540c\u548c\u672a\u7b7e\u5408\u540c\u4e89\u8bae\u6301\u7eed\u8ddf\u8e2a",
            "status": "active",
            "conversation_count": 2,
            "active_conversation_id": 101,
            "participant_aliases": {
                "primary_party": {
                    "label": "\u6211",
                    "role": "\u52b3\u52a8\u8005",
                    "party_side": "claimant",
                    "confirmed_by_user": True,
                },
                "opposing_party": {
                    "label": "\u516c\u53f8",
                    "role": "\u7528\u4eba\u5355\u4f4d",
                    "party_side": "opponent",
                    "confirmed_by_user": True,
                },
            },
            "conversations": [
                {
                    "id": 101,
                    "title": "\u5f53\u524d\u4f1a\u8bdd",
                    "conversation_mode": "TRACKED_CASE",
                    "persistence_consent": "tracked",
                    "case_type": "\u52b3\u52a8\u4e89\u8bae",
                    "case_summary": "\u7528\u6237\u4e3b\u5f20\u8fdd\u6cd5\u89e3\u9664\u548c\u672a\u7b7e\u52b3\u52a8\u5408\u540c\u8d54\u507f",
                },
                {
                    "id": 102,
                    "title": "\u8865\u5145\u8bc1\u636e",
                    "conversation_mode": "CASE_ANALYSIS",
                    "persistence_consent": "temporary",
                    "case_summary": "\u8865\u5145\u804a\u5929\u8bb0\u5f55\u548c\u5de5\u8d44\u6d41\u6c34",
                },
            ],
        }
    )

    assert "<dossier_context>" in block
    assert "[\u6848\u5377\u4e0a\u4e0b\u6587]" in block
    assert "\u6807\u9898\uff1a\u52b3\u52a8\u4e89\u8bae\u6848\u5377" in block
    assert "\u7ed1\u5b9a\u4f1a\u8bdd\u6570\u91cf\uff1a2" in block
    assert "participant_aliases:" in block
    assert "label=\u6211" in block
    assert "current_conversation=true" in block
    assert "\u4e0d\u7b49\u540c\u771f\u5b9e\u59d3\u540d" in block
    assert "\u4ec5\u5728\u5f53\u524d\u6848\u5377\u5185\u751f\u6548" in block
    assert "\u4e0d\u540c\u6848\u5377\u4e2d\u7684\u201c\u6211\u201d\u201c\u516c\u53f8\u201d\u201c\u5bf9\u65b9\u201d\u9ed8\u8ba4\u4e0d\u662f\u540c\u4e00\u5b9e\u4f53" in block
    assert "\u4e0d\u80fd\u66ff\u4ee3\u68c0\u7d22\u5230\u7684\u6cd5\u5f8b\u539f\u6587" in block


def test_build_advisor_system_prompt_includes_dossier_context_when_present() -> None:
    prompt = build_advisor_system_prompt(
        case_summary="\u5f53\u524d\u4f1a\u8bdd\u6458\u8981",
        graph_context="",
        rag_context="\u300a\u52b3\u52a8\u5408\u540c\u6cd5\u300b\u7b2c\u56db\u5341\u4e03\u6761\uff1a\u7ecf\u6d4e\u8865\u507f\u6309\u5de5\u4f5c\u5e74\u9650\u8ba1\u7b97\u3002",
        payload=_sample_payload(),
        question="\u516c\u53f8\u7a81\u7136\u901a\u77e5\u6211\u660e\u5929\u4e0d\u7528\u6765\u4e86\u600e\u4e48\u529e\uff1f",
        dossier_context=_format_dossier_context_block(
            {
                "title": "\u52b3\u52a8\u4e89\u8bae\u6848\u5377",
                "case_type": "\u52b3\u52a8\u4e89\u8bae",
                "summary": "\u56f4\u7ed5\u89e3\u9664\u52b3\u52a8\u5408\u540c\u4e89\u8bae\u6301\u7eed\u8ddf\u8e2a",
                "status": "active",
                "conversation_count": 1,
                "active_conversation_id": 101,
                "participant_aliases": {
                    "primary_party": {
                        "label": "\u6211",
                        "role": "\u52b3\u52a8\u8005",
                        "party_side": "claimant",
                        "confirmed_by_user": True,
                    }
                },
                "conversations": [
                    {
                        "id": 101,
                        "title": "\u5f53\u524d\u4f1a\u8bdd",
                        "conversation_mode": "TRACKED_CASE",
                        "persistence_consent": "tracked",
                        "case_summary": "\u7528\u6237\u4e3b\u5f20\u8fdd\u6cd5\u89e3\u9664",
                    }
                ],
            }
        ),
    )

    assert "<dossier_context>" in prompt
    assert "[\u6848\u5377\u4e0a\u4e0b\u6587]" in prompt
    assert "participant_aliases:" in prompt
    assert "label=\u6211" in prompt
    assert "\u4e0d\u7b49\u540c\u771f\u5b9e\u59d3\u540d" in prompt
    assert "\u4e0d\u5f97\u7f16\u9020\u5176\u771f\u5b9e\u8eab\u4efd" in prompt
    assert "\u4ec5\u5728\u5f53\u524d\u6848\u5377\u5185\u751f\u6548" in prompt
    assert "\u4e0d\u540c\u6848\u5377\u4e2d\u7684\u201c\u6211\u201d\u201c\u516c\u53f8\u201d\u201c\u5bf9\u65b9\u201d\u9ed8\u8ba4\u4e0d\u662f\u540c\u4e00\u5b9e\u4f53" in prompt
    assert "\u4e0d\u80fd\u66ff\u4ee3\u68c0\u7d22\u5230\u7684\u6cd5\u5f8b\u539f\u6587" in prompt
    assert "\u4f18\u5148\u6307\u51fa\u51b2\u7a81\u5e76\u5411\u7528\u6237\u6f84\u6e05" in prompt
    assert "\u5fc5\u987b\u5148\u76f4\u63a5\u56de\u7b54\u8be5\u95ee\u9898" in prompt
    assert "\u4e0d\u8981\u53ea\u8bf4\u201c\u6211\u53ef\u4ee5\u5e2e\u4f60\u6574\u7406\u201d" in prompt


def test_build_advisor_system_prompt_stays_unchanged_without_dossier_context() -> None:
    prompt = build_advisor_system_prompt(
        case_summary="\u5f53\u524d\u4f1a\u8bdd\u6458\u8981",
        graph_context="",
        rag_context="\u300a\u52b3\u52a8\u5408\u540c\u6cd5\u300b\u7b2c\u56db\u5341\u4e03\u6761\uff1a\u7ecf\u6d4e\u8865\u507f\u6309\u5de5\u4f5c\u5e74\u9650\u8ba1\u7b97\u3002",
        payload=_sample_payload(),
        question="\u516c\u53f8\u7a81\u7136\u901a\u77e5\u6211\u660e\u5929\u4e0d\u7528\u6765\u4e86\u600e\u4e48\u529e\uff1f",
    )

    assert "<dossier_context>" not in prompt
    assert "[\u6848\u5377\u4e0a\u4e0b\u6587]" not in prompt
    assert "<dossier_graph_context_guard>" not in prompt
    assert "\u5f53\u524d\u6848\u5377" not in prompt
