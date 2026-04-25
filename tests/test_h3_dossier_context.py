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
            "我在公司工作两年，公司一直没有签劳动合同。",
            "公司突然通知我明天不用来了怎么办？",
            "",
        )
    )


def test_format_dossier_context_block_contains_summary_and_bound_conversations() -> None:
    block = _format_dossier_context_block(
        {
            "id": 12,
            "title": "劳动争议案卷",
            "case_type": "劳动争议",
            "summary": "围绕解除劳动合同和未签合同争议持续跟踪",
            "status": "active",
            "conversation_count": 2,
            "active_conversation_id": 101,
            "conversations": [
                {
                    "id": 101,
                    "title": "当前会话",
                    "conversation_mode": "TRACKED_CASE",
                    "persistence_consent": "tracked",
                    "case_type": "劳动争议",
                    "case_summary": "用户主张违法解除和未签劳动合同赔偿",
                },
                {
                    "id": 102,
                    "title": "补充证据",
                    "conversation_mode": "CASE_ANALYSIS",
                    "persistence_consent": "temporary",
                    "case_summary": "补充聊天记录和工资流水",
                },
            ],
        }
    )

    assert "<dossier_context>" in block
    assert "标题：劳动争议案卷" in block
    assert "绑定会话数量：2" in block
    assert "current_conversation=true" in block
    assert "不能替代检索到的法条原文" in block


def test_build_advisor_system_prompt_includes_dossier_context_when_present() -> None:
    prompt = build_advisor_system_prompt(
        case_summary="当前会话摘要",
        graph_context="",
        rag_context="《劳动合同法》第四十七条：经济补偿按工作年限计算。",
        payload=_sample_payload(),
        question="公司突然通知我明天不用来了怎么办？",
        dossier_context=_format_dossier_context_block(
            {
                "title": "劳动争议案卷",
                "case_type": "劳动争议",
                "summary": "围绕解除劳动合同争议持续跟踪",
                "status": "active",
                "conversation_count": 1,
                "active_conversation_id": 101,
                "conversations": [
                    {
                        "id": 101,
                        "title": "当前会话",
                        "conversation_mode": "TRACKED_CASE",
                        "persistence_consent": "tracked",
                        "case_summary": "用户主张违法解除",
                    }
                ],
            }
        ),
    )

    assert "<dossier_context>" in prompt
    assert "[案卷上下文]" in prompt
    assert "不能替代检索到的法条原文" in prompt
    assert "优先指出冲突并向用户澄清" in prompt


def test_build_advisor_system_prompt_stays_unchanged_without_dossier_context() -> None:
    prompt = build_advisor_system_prompt(
        case_summary="当前会话摘要",
        graph_context="",
        rag_context="《劳动合同法》第四十七条：经济补偿按工作年限计算。",
        payload=_sample_payload(),
        question="公司突然通知我明天不用来了怎么办？",
    )

    assert "<dossier_context>" not in prompt
    assert "[案卷上下文]" not in prompt
