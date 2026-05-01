from __future__ import annotations

import asyncio
import json

import pytest

from app.services.memory_resolver import (
    build_subject_reference_response_text_async,
    build_subject_reference_response_text,
    extract_pending_subject_reference_clarification,
    looks_like_subject_reference_clarification_response,
)


def _subject_reference_message_understanding() -> dict[str, object]:
    return {
        "intent": "subject_reference",
        "route_hint": "DOSSIER_MEMORY",
        "subject_reference": {
            "enabled": True,
            "target_mention": "公司",
            "mention": "公司",
            "target_type": "organization",
            "reference_type": "organization",
            "scope_order": [
                "current_conversation_graph",
                "current_conversation_messages",
                "dossier_confirmed_participants",
                "dossier_display_graph",
                "dossier_cross_conversation_candidates",
            ],
            "needs_clarification_if_ambiguous": True,
        },
    }


def _fact_memory_message_understanding() -> dict[str, object]:
    result = _subject_reference_message_understanding()
    result["subject_reference"] = {
        **result["subject_reference"],
        "target_mention": "",
        "mention": "",
        "target_type": "unknown",
        "reference_type": "unknown",
    }
    return result


def _lanhai_graph() -> dict[str, object]:
    return {
        "nodes": [
            {"id": "person-zhang", "label": "张三", "type": "主体", "category": "主体"},
            {"id": "company-lanhai", "label": "蓝海科技", "type": "主体", "category": "主体"},
            {"id": "event-wage", "label": "拖欠工资", "type": "事件", "category": "事件"},
            {"id": "loss", "label": "经济损失", "type": "后果", "category": "后果"},
            {"id": "amount", "label": "6万元", "type": "金额", "category": "金额"},
            {"id": "time", "label": "2023年5月", "type": "时间", "category": "时间"},
            {"id": "wage", "label": "工资", "type": "客体", "category": "客体"},
        ],
        "edges": [
            {"source": "person-zhang", "target": "company-lanhai", "label": "任职于"},
            {"source": "person-zhang", "target": "event-wage", "label": "主张"},
            {"source": "company-lanhai", "target": "event-wage", "label": "实施"},
            {"source": "event-wage", "target": "loss", "label": "导致"},
            {"source": "person-zhang", "target": "loss", "label": "遭受"},
            {"source": "loss", "target": "amount", "label": "金额"},
            {"source": "event-wage", "target": "time", "label": "开始时间"},
            {"source": "event-wage", "target": "wage", "label": "涉及"},
        ],
    }


def _run(coro):
    return asyncio.run(coro)


def test_extract_pending_subject_reference_clarification_from_recent_assistant_metadata() -> None:
    pending = extract_pending_subject_reference_clarification(
        [
            {"role": "user", "content": "这里的公司指的是哪家公司？"},
            {
                "role": "assistant",
                "content": "当前还不能稳定确定“公司”具体指向哪个主体。",
                "metadata": {
                    "pending_clarification": {
                        "type": "subject_reference_resolution",
                        "target_mention": "公司",
                        "reference_type": "organization",
                        "previous_question": "这里的公司指的是哪家公司？",
                        "scope_attempted": [
                            "current_conversation_graph",
                            "current_conversation_messages",
                        ],
                        "expected_response": "entity_name_or_context",
                    }
                },
            },
        ]
    )

    assert pending is not None
    assert pending["type"] == "subject_reference_resolution"
    assert pending["target_mention"] == "公司"
    assert pending["reference_type"] == "organization"
    assert pending["scope_attempted"] == [
        "current_conversation_graph",
        "current_conversation_messages",
    ]


def test_clarification_response_detection_matches_short_followup() -> None:
    pending = {
        "type": "subject_reference_resolution",
        "target_mention": "公司",
        "reference_type": "organization",
        "previous_question": "这里的公司指的是哪家公司？",
        "scope_attempted": ["current_conversation_graph"],
        "expected_response": "entity_name_or_context",
    }

    assert looks_like_subject_reference_clarification_response(
        message="是B公司",
        pending_clarification=pending,
    ) is True
    assert looks_like_subject_reference_clarification_response(
        message="我，当事人",
        pending_clarification=pending,
    ) is True
    assert looks_like_subject_reference_clarification_response(
        message="这里的公司为什么和我前面说的不一样？",
        pending_clarification=pending,
    ) is False
    assert looks_like_subject_reference_clarification_response(
        message="李四是谁",
        pending_clarification=pending,
    ) is False
    assert looks_like_subject_reference_clarification_response(
        message="蓝海科技是什么",
        pending_clarification=pending,
    ) is False


def test_memory_resolver_uses_current_conversation_graph_nodes_without_participant_mapping() -> None:
    text, confidence, trace = build_subject_reference_response_text(
        question="这里的公司指的是哪家公司？",
        message_understanding=_subject_reference_message_understanding(),
        dossier_context={"participant_mentions": [], "confirmed_participants": []},
        dossier_graph_context={
            "graph_nodes": [
                {
                    "id": "company-b",
                    "conversation_id": 9,
                    "display_label": "B公司",
                    "raw_label": "B公司",
                    "type": "organization",
                }
            ],
            "participant_mentions": [],
            "participants": [],
            "matched_nodes": [],
        },
        current_conversation_graph={
            "nodes": [
                {
                    "id": "company-b",
                    "conversation_id": 9,
                    "display_label": "B公司",
                    "raw_label": "B公司",
                    "type": "organization",
                }
            ],
            "edges": [],
        },
        recent_messages=[],
        current_conversation_id=9,
    )

    assert "当前会话" in text
    assert "B公司" in text
    assert trace["selected_scope"] == "current_conversation_graph"
    assert trace["used_evidence"][0]["source"] == "current_conversation_graph_node"
    assert confidence >= 0.8


def test_memory_resolver_clarification_response_confirms_explicit_company_name() -> None:
    text, confidence, trace = build_subject_reference_response_text(
        question="是B公司",
        message_understanding=_subject_reference_message_understanding(),
        dossier_context={"participant_mentions": [], "confirmed_participants": []},
        dossier_graph_context={"graph_nodes": [], "participant_mentions": [], "participants": [], "matched_nodes": []},
        recent_messages=[
            {
                "role": "assistant",
                "content": "当前还不能稳定确定“公司”具体指向哪个主体。",
                "metadata": {
                    "pending_clarification": {
                        "type": "subject_reference_resolution",
                        "target_mention": "公司",
                        "reference_type": "organization",
                        "previous_question": "这里的公司指的是哪家公司？",
                        "scope_attempted": ["current_conversation_graph"],
                        "expected_response": "entity_name_or_context",
                    }
                },
            }
        ],
        current_conversation_id=9,
    )

    assert text == "已理解，你这里说的“公司”指 B公司。"
    assert trace["selected_scope"] == "clarification_response"
    assert trace["unresolved_reason"] is None
    assert "pending_clarification" not in trace
    assert confidence >= 0.9


def test_memory_resolver_clarification_response_rejects_self_answer_for_company_target() -> None:
    text, confidence, trace = build_subject_reference_response_text(
        question="我，当事人",
        message_understanding=_subject_reference_message_understanding(),
        dossier_context={"participant_mentions": [], "confirmed_participants": []},
        dossier_graph_context={"graph_nodes": [], "participant_mentions": [], "participants": [], "matched_nodes": []},
        recent_messages=[
            {
                "role": "assistant",
                "content": "当前还不能稳定确定“公司”具体指向哪个主体。",
                "metadata": {
                    "pending_clarification": {
                        "type": "subject_reference_resolution",
                        "target_mention": "公司",
                        "reference_type": "organization",
                        "previous_question": "这里的公司指的是哪家公司？",
                        "scope_attempted": ["current_conversation_graph"],
                        "expected_response": "entity_name_or_context",
                    }
                },
            }
        ],
        current_conversation_id=9,
    )

    assert "你补充的是当事人本人" in text
    assert "公司" in text
    assert trace["selected_scope"] == "clarification_response"
    assert trace["unresolved_reason"] == "clarification_type_mismatch"
    assert trace["pending_clarification"]["target_mention"] == "公司"
    assert confidence < 0.6


@pytest.mark.parametrize(
    ("question", "answer_text", "used_ids"),
    [
        (
            "张三任职的公司叫什么？",
            "根据当前会话记录，张三任职的公司是蓝海科技。",
            ["current_conversation_graph:edge:0"],
        ),
        (
            "劳动者任职的单位叫什么？",
            "根据当前会话记录，张三任职的单位是蓝海科技。",
            ["current_conversation_graph:edge:0"],
        ),
        (
            "张三被拖欠的工资是多少？",
            "根据当前会话记录，张三被拖欠的工资金额是6万元。",
            [
                "current_conversation_graph:edge:1",
                "current_conversation_graph:edge:3",
                "current_conversation_graph:edge:5",
            ],
        ),
        (
            "谁拖欠了张三工资？",
            "根据当前会话记录，拖欠张三工资的是蓝海科技。",
            [
                "current_conversation_graph:edge:1",
                "current_conversation_graph:edge:2",
            ],
        ),
    ],
)
def test_fact_memory_questions_use_current_graph_evidence_with_semantic_agent(
    monkeypatch: pytest.MonkeyPatch,
    question: str,
    answer_text: str,
    used_ids: list[str],
) -> None:
    import app.services.memory_resolver as memory_resolver_module

    captured_payload: dict[str, object] = {}

    class FakeResponse:
        content = json.dumps(
            {
                "decision": "answer",
                "answer": answer_text,
                "confidence": 0.94,
                "used_evidence_ids": used_ids,
                "selected_scope": "current_conversation_graph",
                "reason": "证据包中的当前会话图谱关系足以回答。",
            },
            ensure_ascii=False,
        )

    class FakeLLM:
        async def ainvoke(self, messages):
            captured_payload.update(json.loads(messages[1]["content"]))
            return FakeResponse()

    monkeypatch.setattr(
        memory_resolver_module,
        "create_cloud_llm",
        lambda **_kwargs: FakeLLM(),
    )

    text, confidence, trace = _run(
        build_subject_reference_response_text_async(
            question=question,
            message_understanding=_fact_memory_message_understanding(),
            dossier_context={"participant_mentions": [], "confirmed_participants": []},
            dossier_graph_context={"graph_nodes": [], "matched_edges": []},
            current_conversation_graph=_lanhai_graph(),
            recent_messages=[
                {
                    "role": "user",
                    "content": "我叫张三，在上海一家叫蓝海科技的公司做后端开发。公司从2023年5月开始拖欠我工资，到现在大概欠了我6万元。",
                }
            ],
            current_conversation_id=9,
        )
    )

    assert text == answer_text
    assert confidence >= 0.9
    assert trace["selected_scope"] == "current_conversation_graph"
    assert trace["unresolved_reason"] is None
    assert trace["used_evidence"]
    evidence_text = "\n".join(item["text"] for item in captured_payload["current_conversation_graph_facts"])
    assert "张三（主体） - 任职于 - 蓝海科技（主体）" in evidence_text
    assert "6万元" in evidence_text
    assert "2023年5月" in evidence_text


def test_subject_candidate_clarification_filters_non_responsible_graph_nodes() -> None:
    text, confidence, trace = build_subject_reference_response_text(
        question="张三任职的公司叫什么？",
        message_understanding={
            "intent": "subject_reference",
            "route_hint": "DOSSIER_MEMORY",
            "subject_reference": {
                "enabled": True,
                "target_mention": "公司",
                "mention": "公司",
                "target_type": "organization",
                "reference_type": "organization",
                "scope_order": ["current_conversation_graph"],
                "needs_clarification_if_ambiguous": True,
            },
        },
        dossier_context={"participant_mentions": [], "confirmed_participants": []},
        dossier_graph_context={"graph_nodes": [], "matched_edges": []},
        current_conversation_graph={
            "nodes": [
                {"id": "time", "label": "2023年5月", "type": "时间", "category": "时间"},
                {"id": "amount", "label": "6万元", "type": "金额", "category": "金额"},
                {"id": "wage", "label": "工资", "type": "客体", "category": "客体"},
            ],
            "edges": [],
        },
        recent_messages=[],
        current_conversation_id=9,
    )

    assert "2023年5月" not in text
    assert "6万元" not in text
    assert "工资" not in text
    assert trace["unresolved_reason"] == "no_matching_subject_found"
    assert confidence < 0.5


def test_fact_memory_falls_back_to_recent_message_context_when_graph_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.memory_resolver as memory_resolver_module

    class FakeResponse:
        content = json.dumps(
            {
                "decision": "answer",
                "answer": "根据当前会话记录，张三任职的公司是蓝海科技。",
                "confidence": 0.88,
                "used_evidence_ids": ["current_conversation_messages:message:0"],
                "selected_scope": "current_conversation_messages",
                "reason": "最近用户事实消息中明确说明任职公司。",
            },
            ensure_ascii=False,
        )

    class FakeLLM:
        async def ainvoke(self, _messages):
            return FakeResponse()

    monkeypatch.setattr(
        memory_resolver_module,
        "create_cloud_llm",
        lambda **_kwargs: FakeLLM(),
    )

    text, confidence, trace = _run(
        build_subject_reference_response_text_async(
            question="张三任职的公司叫什么？",
            message_understanding=_fact_memory_message_understanding(),
            dossier_context={"participant_mentions": [], "confirmed_participants": []},
            dossier_graph_context={"graph_nodes": [], "matched_edges": []},
            current_conversation_graph={"nodes": [], "edges": []},
            recent_messages=[
                {
                    "message_id": 17,
                    "role": "user",
                    "content": "我想咨询一个劳动纠纷。我叫张三，在上海一家叫蓝海科技的公司做后端开发。",
                }
            ],
            current_conversation_id=9,
        )
    )

    assert "蓝海科技" in text
    assert trace["selected_scope"] == "current_conversation_messages"
    assert trace["used_evidence"][0]["message_id"] == 17
    assert confidence >= 0.8


def test_fact_memory_escalates_to_dossier_graph_when_current_conversation_has_no_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.memory_resolver as memory_resolver_module

    class FakeResponse:
        content = json.dumps(
            {
                "decision": "answer",
                "answer": "当前会话证据不足，结合案卷其他会话记录，张三任职的公司是蓝海科技。",
                "confidence": 0.82,
                "used_evidence_ids": ["dossier_display_graph:edge:0"],
                "selected_scope": "dossier_display_graph",
                "reason": "当前会话没有命中，案卷图谱有直接任职关系。",
            },
            ensure_ascii=False,
        )

    class FakeLLM:
        async def ainvoke(self, _messages):
            return FakeResponse()

    monkeypatch.setattr(
        memory_resolver_module,
        "create_cloud_llm",
        lambda **_kwargs: FakeLLM(),
    )

    text, confidence, trace = _run(
        build_subject_reference_response_text_async(
            question="张三任职的公司叫什么？",
            message_understanding=_fact_memory_message_understanding(),
            dossier_context={"participant_mentions": [], "confirmed_participants": []},
            dossier_graph_context={
                "graph_payload": {
                    "graph": {
                        "nodes": [
                            {"id": "person-zhang-10", "label": "张三", "type": "主体", "conversation_id": 10},
                            {"id": "company-lanhai-10", "label": "蓝海科技", "type": "主体", "conversation_id": 10},
                        ],
                        "edges": [
                            {"source": "person-zhang-10", "target": "company-lanhai-10", "label": "任职于"}
                        ],
                    }
                }
            },
            current_conversation_graph={"nodes": [], "edges": []},
            recent_messages=[],
            current_conversation_id=9,
        )
    )

    assert "案卷其他会话" in text
    assert "蓝海科技" in text
    assert trace["selected_scope"] == "dossier_display_graph"
    assert trace["escalated_to_dossier"] is True
    assert confidence >= 0.8


def test_fact_memory_clarifies_when_evidence_is_insufficient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.memory_resolver as memory_resolver_module

    class FakeResponse:
        content = json.dumps(
            {
                "decision": "clarify",
                "answer": "当前证据还不能确定张三任职的公司，请补充公司名称或相关事实。",
                "confidence": 0.31,
                "used_evidence_ids": [],
                "selected_scope": "",
                "reason": "没有可用证据。",
            },
            ensure_ascii=False,
        )

    class FakeLLM:
        async def ainvoke(self, _messages):
            return FakeResponse()

    monkeypatch.setattr(
        memory_resolver_module,
        "create_cloud_llm",
        lambda **_kwargs: FakeLLM(),
    )

    text, confidence, trace = _run(
        build_subject_reference_response_text_async(
            question="张三任职的公司叫什么？",
            message_understanding=_fact_memory_message_understanding(),
            dossier_context={"participant_mentions": [], "confirmed_participants": []},
            dossier_graph_context={"graph_nodes": [], "matched_edges": []},
            current_conversation_graph={"nodes": [{"id": "person-zhang", "label": "张三", "type": "主体"}], "edges": []},
            recent_messages=[],
            current_conversation_id=9,
        )
    )

    assert "不能确定" in text
    assert trace["unresolved_reason"] == "no_fact_evidence_found"
    assert confidence < 0.5
