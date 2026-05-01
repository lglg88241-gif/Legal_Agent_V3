from __future__ import annotations

import asyncio
import importlib
import sys
from types import ModuleType

from app.agent.nodes.advisor import (
    _format_dossier_graph_summary_block,
    build_advisor_system_prompt,
)
from app.logic.calculators import build_claim_payload
from app.models import Conversation, Dossier
from app.schemas.case_state import CaseState
from app.services import dossier_graph


def _sample_payload():
    return build_claim_payload(
        CaseState.from_text(
            "我在公司工作两年，公司一直没有签劳动合同。",
            "公司突然通知我明天不用来了怎么办？",
            "",
        )
    )


class DummyDB:
    pass


def _install_celery_import_stubs() -> None:
    fake_openai = ModuleType("openai")

    class FakeAsyncOpenAI:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def close(self) -> None:
            return None

    fake_openai.AsyncOpenAI = FakeAsyncOpenAI
    sys.modules["openai"] = fake_openai

    fake_graph_extractor = ModuleType("app.services.graph_extractor")

    async def _format_graph_memory(*args, **kwargs) -> str:
        return ""

    async def _refresh_case_graph(*args, **kwargs):
        return {"actions_applied": 0, "actions": []}

    fake_graph_extractor.format_graph_memory = _format_graph_memory
    fake_graph_extractor.refresh_case_graph = _refresh_case_graph
    sys.modules["app.services.graph_extractor"] = fake_graph_extractor

    fake_redis_mgr = ModuleType("app.memory.redis_mgr")
    fake_redis_mgr.get_redis_saver_for_current_loop = lambda: None
    sys.modules["app.memory.redis_mgr"] = fake_redis_mgr


def _load_celery_module():
    _install_celery_import_stubs()
    sys.modules.pop("app.celery_app", None)
    return importlib.import_module("app.celery_app")


def test_build_dossier_graph_summary_for_conversation_returns_none_without_dossier_binding(
    monkeypatch,
) -> None:
    async def fake_get_conversation_for_user(db, user_id: int, conversation_id: int):
        return Conversation(
            id=41,
            user_id=user_id,
            dossier_id=None,
            session_id="session-41",
            title="普通会话",
            conversation_mode="UNKNOWN",
            persistence_consent="unknown",
        )

    monkeypatch.setattr(dossier_graph, "get_conversation_for_user", fake_get_conversation_for_user)

    result = asyncio.run(
        dossier_graph.build_dossier_graph_summary_for_conversation(
            DummyDB(),
            user_id=7,
            conversation_id=41,
        )
    )

    assert result is None


def test_build_dossier_graph_summary_contains_stats_entities_relations_and_aliases() -> None:
    summary = dossier_graph.build_dossier_graph_summary(
        {
            "status": "success",
            "dossier_id": 12,
            "participant_aliases": {
                "primary_party": {
                    "label": "我",
                    "role": "劳动者",
                    "party_side": "claimant",
                    "confirmed_by_user": True,
                },
                "opposing_party": {
                    "label": "公司",
                    "role": "用人单位",
                    "party_side": "opponent",
                    "confirmed_by_user": True,
                },
            },
            "graph": {
                "nodes": [
                    {
                        "id": "n1",
                        "label": "我",
                        "type": "主体",
                        "conversation_id": 101,
                        "properties": {"conversation_id": 101},
                    },
                    {
                        "id": "n2",
                        "label": "公司",
                        "type": "主体",
                        "conversation_id": 101,
                        "properties": {"conversation_id": 101},
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "n1",
                        "target": "n2",
                        "label": "工作于",
                        "type": "CASE_RELATION",
                        "properties": {"conversation_id": 101},
                    }
                ],
                "stats": {
                    "conversation_count": 2,
                    "node_count": 2,
                    "edge_count": 1,
                },
            },
            "generated_at": "2026-04-26T00:00:00+00:00",
        }
    )

    assert summary is not None
    assert "<dossier_graph_summary>" in summary
    assert "图谱统计：绑定会话 2 个，实体 2 个，关系 1 条。" in summary
    assert "主要当事人：我（劳动者）" in summary
    assert "对方：公司（用人单位）" in summary
    assert "- 我 [主体]（conversation_id=101）" in summary
    assert "- 我 --工作于--> 公司（conversation_id=101）" in summary
    assert "图谱不能替代法律检索依据" in summary
    assert "不得把图谱中的推断关系直接当作已证实事实" in summary


def test_build_dossier_graph_summary_includes_truncation_and_warning_notes() -> None:
    summary = dossier_graph.build_dossier_graph_summary(
        {
            "status": "success",
            "dossier_id": 18,
            "warning": "Neo4j dossier graph is unavailable",
            "participant_aliases": {},
            "graph": {
                "nodes": [
                    {"id": "n1", "label": "甲", "type": "主体", "conversation_id": 1, "properties": {}},
                    {"id": "n2", "label": "乙", "type": "主体", "conversation_id": 2, "properties": {}},
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "n1",
                        "target": "n2",
                        "label": "关联",
                        "type": "CASE_RELATION",
                        "properties": {"conversation_id": 1},
                    },
                    {
                        "id": "e2",
                        "source": "n2",
                        "target": "n1",
                        "label": "补充",
                        "type": "CASE_RELATION",
                        "properties": {"conversation_id": 2},
                    },
                ],
                "stats": {
                    "conversation_count": 3,
                    "node_count": 2,
                    "edge_count": 2,
                },
            },
        },
        max_nodes=1,
        max_edges=1,
    )

    assert summary is not None
    assert "仅展示前 1 个实体。" in summary
    assert "仅展示前 1 条关系。" in summary
    assert "图谱服务提示：Neo4j dossier graph is unavailable。" in summary


def test_build_dossier_graph_summary_for_conversation_handles_neo4j_warning_without_failure(
    monkeypatch,
) -> None:
    dossier = Dossier(
        id=52,
        user_id=7,
        title="劳动争议案卷",
        case_type="劳动争议",
        summary="摘要",
        status="active",
        participant_aliases_json='{"primary_party":{"label":"我","role":"劳动者","party_side":"claimant","confirmed_by_user":true}}',
    )
    conversation = Conversation(
        id=61,
        user_id=7,
        dossier_id=52,
        session_id="session-61",
        title="当前会话",
        conversation_mode="TRACKED_CASE",
        persistence_consent="tracked",
    )

    async def fake_get_conversation_for_user(db, user_id: int, conversation_id: int):
        return conversation

    async def fake_get_dossier_for_user(db, user_id: int, dossier_id: int):
        return dossier

    async def fake_list_dossier_conversations(db, user_id: int, dossier_id: int):
        return [conversation]

    async def fake_list_dossier_participants(db, user_id: int, dossier_id: int):
        return []

    async def fake_list_dossier_participant_mentions(db, user_id: int, dossier_id: int):
        return []

    async def fake_get_dossier_graph_payload(**kwargs):
        return {
            "status": "success",
            "dossier_id": 52,
            "warning": "Neo4j dossier graph is unavailable",
            "participant_aliases": {
                "primary_party": {
                    "label": "我",
                    "role": "劳动者",
                    "party_side": "claimant",
                    "confirmed_by_user": True,
                }
            },
            "graph": {
                "nodes": [],
                "edges": [],
                "stats": {
                    "conversation_count": 1,
                    "node_count": 0,
                    "edge_count": 0,
                },
            },
        }

    monkeypatch.setattr(dossier_graph, "get_conversation_for_user", fake_get_conversation_for_user)
    monkeypatch.setattr(dossier_graph, "get_dossier_for_user", fake_get_dossier_for_user)
    monkeypatch.setattr(dossier_graph, "list_dossier_conversations", fake_list_dossier_conversations)
    monkeypatch.setattr(dossier_graph, "list_dossier_participants", fake_list_dossier_participants)
    monkeypatch.setattr(
        dossier_graph,
        "list_dossier_participant_mentions",
        fake_list_dossier_participant_mentions,
    )
    monkeypatch.setattr(dossier_graph, "get_dossier_graph_payload", fake_get_dossier_graph_payload)

    summary = asyncio.run(
        dossier_graph.build_dossier_graph_summary_for_conversation(
            DummyDB(),
            user_id=7,
            conversation_id=61,
        )
    )

    assert summary is not None
    assert "<dossier_graph_summary>" in summary
    assert "图谱服务提示：Neo4j dossier graph is unavailable。" in summary


def test_build_advisor_system_prompt_includes_dossier_graph_summary_when_present() -> None:
    graph_summary = _format_dossier_graph_summary_block(
        """
<dossier_graph_summary>
图谱统计：绑定会话 3 个，实体 10 个，关系 8 条。
注意：
- 图谱由绑定会话中的结构化案情信息聚合生成，可能包含系统推断的未确认关系。
- 如图谱内容与本轮用户输入冲突，应优先澄清。
- 图谱不能替代法律检索依据。
- 不得把图谱中的推断关系直接当作已证实事实。
</dossier_graph_summary>
        """
    )

    prompt = build_advisor_system_prompt(
        case_summary="当前会话摘要",
        graph_context="",
        rag_context="《劳动合同法》第四十七条：经济补偿按工作年限计算。",
        payload=_sample_payload(),
        question="公司突然通知我明天不用来了怎么办？",
        dossier_graph_summary=graph_summary,
    )

    assert "<dossier_graph_summary>" in prompt
    assert "图谱不能替代法律检索依据" in prompt
    assert "不得把图谱中的推断关系直接当作已证实事实" in prompt
    assert "如图谱内容与本轮用户输入冲突，应优先澄清" in prompt


def test_build_advisor_system_prompt_stays_unchanged_without_dossier_graph_summary() -> None:
    prompt = build_advisor_system_prompt(
        case_summary="当前会话摘要",
        graph_context="",
        rag_context="《劳动合同法》第四十七条：经济补偿按工作年限计算。",
        payload=_sample_payload(),
        question="公司突然通知我明天不用来了怎么办？",
    )

    assert "<dossier_graph_summary>" not in prompt


def test_ask_agent_task_passes_dossier_graph_summary_without_breaking_advisor(
    monkeypatch,
) -> None:
    celery_module = _load_celery_module()
    captured: dict[str, object] = {}

    async def fake_run_task_async(conversation_id: int, user_id: int):
        return {
            "chat_history": "",
            "case_summary": "旧摘要",
            "graph_memory": "",
            "saved_case_state": {"monthly_salary": 12000},
            "persistence_consent": "unknown",
            "dossier_context": {
                "title": "劳动争议案卷",
                "conversation_count": 1,
            },
            "dossier_graph_summary": (
                "<dossier_graph_summary>\n"
                "图谱统计：绑定会话 1 个，实体 0 个，关系 0 条。\n"
                "注意：\n"
                "- 图谱服务提示：Neo4j dossier graph is unavailable。\n"
                "</dossier_graph_summary>"
            ),
        }

    class FakeLegalAgent:
        async def ask(self, *args, **kwargs):
            captured["dossier_graph_summary"] = kwargs.get("dossier_graph_summary")
            return {
                "response": "分析结果",
                "sources": [],
                "route_intent": "GENERAL_CONSULT",
                "case_summary": "",
            }

    async def fake_persist_dialog_turn_async(**kwargs):
        return {"user_message_id": 11, "assistant_message_id": 12}

    monkeypatch.setattr(celery_module, "_run_task_async", fake_run_task_async)
    monkeypatch.setattr(celery_module, "legal_agent", FakeLegalAgent())
    monkeypatch.setattr(celery_module, "_persist_dialog_turn_async", fake_persist_dialog_turn_async)

    result = celery_module.ask_agent_task.run(
        1,
        9,
        "session-1",
        "我在公司工作两年，公司一直未签合同，公司通知我明天不用来了",
        True,
        True,
    )

    assert result["status"] == "success"
    assert str(captured["dossier_graph_summary"]).startswith("<dossier_graph_summary>")


def test_build_dossier_graph_summary_marks_aliases_as_dossier_local_only() -> None:
    summary = dossier_graph.build_dossier_graph_summary(
        {
            "status": "success",
            "dossier_id": 88,
            "participant_aliases": {
                "primary_party": {
                    "label": "我",
                    "role": "劳动者",
                    "party_side": "claimant",
                    "confirmed_by_user": True,
                },
                "opposing_party": {
                    "label": "公司",
                    "role": "用人单位",
                    "party_side": "opponent",
                    "confirmed_by_user": True,
                },
            },
            "graph": {
                "nodes": [],
                "edges": [],
                "stats": {
                    "conversation_count": 1,
                    "node_count": 0,
                    "edge_count": 0,
                },
            },
        }
    )

    assert summary is not None
    assert "participant aliases 只在当前案卷内表示用户确认的称呼" in summary
    assert "不得跨案卷自动合并同名主体" in summary
