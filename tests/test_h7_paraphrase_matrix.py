from __future__ import annotations

import importlib
import sys
from types import ModuleType
from typing import Any

import pytest


FACT_MESSAGE = "B公司的人事通知我去签确认书。而且人事王经理口头通知我不用来了。"


def _subject_reference_result(*, mention: str, target_type: str) -> dict[str, object]:
    return {
        "source": "message_understanding_agent",
        "intent": "subject_reference",
        "route_hint": "DOSSIER_MEMORY",
        "needs_legal_rag": False,
        "needs_dossier_graph_context": True,
        "needs_structured_persistence": False,
        "needs_graph_update": False,
        "subject_reference": {
            "enabled": True,
            "target_span": mention,
            "mention": mention,
            "target_type": target_type,
            "question": "",
            "resolution_strategy": "use_dossier_context",
        },
        "fact_update": None,
        "previous_fact_confirmation": None,
        "advice_question": None,
        "legal_question": None,
        "confidence": "high",
        "reason": "Paraphrase resolved to dossier memory lookup.",
        "provider": "openai",
        "model": "gpt-5.1",
        "fallback_reason": None,
        "trace": {"steps": ["call_llm"], "warnings": []},
    }


def _fact_update_result(message: str) -> dict[str, object]:
    return {
        "source": "message_understanding_agent",
        "intent": "fact_update",
        "route_hint": "CASE_UPDATE",
        "needs_legal_rag": False,
        "needs_dossier_graph_context": False,
        "needs_structured_persistence": True,
        "needs_graph_update": True,
        "subject_reference": None,
        "fact_update": {
            "enabled": True,
            "fact_text": message,
            "entities": [],
            "events": [],
        },
        "previous_fact_confirmation": None,
        "advice_question": None,
        "legal_question": None,
        "confidence": "high",
        "reason": "Paraphrase resolved to factual case update.",
        "provider": "openai",
        "model": "gpt-5.1",
        "fallback_reason": None,
        "trace": {"steps": ["call_llm"], "warnings": []},
    }


def _confirm_previous_fact_result(
    *,
    target_text: str | None = None,
    target_message_id: int | None = 71,
) -> dict[str, object]:
    return {
        "source": "message_understanding_agent",
        "intent": "confirm_previous_fact",
        "route_hint": "CASE_UPDATE",
        "needs_legal_rag": False,
        "needs_dossier_graph_context": False,
        "needs_structured_persistence": True,
        "needs_graph_update": True,
        "subject_reference": None,
        "fact_update": None,
        "previous_fact_confirmation": {
            "enabled": True,
            "action": "confirm",
            "confirmation_text": "我确认这个事实",
            "target_message_id": target_message_id,
            "target_text": target_text,
            "target_fact_summary": "王经理口头通知当事人不用来了" if (target_text or target_message_id) else None,
        },
        "advice_question": None,
        "legal_question": None,
        "confidence": "high" if (target_text or target_message_id) else "low",
        "reason": "Paraphrase resolved to previous fact confirmation.",
        "provider": "openai",
        "model": "gpt-5.1",
        "fallback_reason": None,
        "trace": {"steps": ["call_llm"], "warnings": []},
    }


def _legal_or_advice_result(*, intent: str, route_hint: str, question: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "source": "message_understanding_agent",
        "intent": intent,
        "route_hint": route_hint,
        "needs_legal_rag": True,
        "needs_dossier_graph_context": False,
        "needs_structured_persistence": False,
        "needs_graph_update": False,
        "subject_reference": None,
        "fact_update": None,
        "previous_fact_confirmation": None,
        "advice_question": None,
        "legal_question": None,
        "confidence": "high",
        "reason": "Paraphrase resolved to legal/advice request.",
        "provider": "openai",
        "model": "gpt-5.1",
        "fallback_reason": None,
        "trace": {"steps": ["call_llm"], "warnings": []},
    }
    if intent == "legal_question":
        payload["legal_question"] = {"enabled": True, "question": question}
    else:
        payload["advice_question"] = {"enabled": True, "question": question}
    return payload


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


def _step_lookup(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    trace = result.get("execution_trace") if isinstance(result, dict) else {}
    steps = trace.get("steps") if isinstance(trace, dict) else []
    lookup: dict[str, dict[str, Any]] = {}
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        key = str(step.get("name") or "").strip()
        if key and key not in lookup:
            lookup[key] = step
    return lookup


def _build_subject_reference_context() -> tuple[dict[str, Any], dict[str, Any]]:
    dossier_context = {
        "id": 12,
        "title": "劳动争议案卷",
        "confirmed_participants": [
            {
                "id": 101,
                "label": "B公司",
                "role": "用人单位",
                "party_side": "opponent",
                "entity_type": "organization",
            },
            {
                "id": 102,
                "label": "张三",
                "role": "劳动者本人",
                "party_side": "self",
                "entity_type": "person",
            },
        ],
        "participant_mentions": [
            {
                "conversation_id": 9,
                "participant_id": 101,
                "raw_label": "公司",
                "graph_node_id": "company-1",
                "graph_node_label": "公司",
                "graph_node_type": "organization",
                "participant_label": "B公司",
                "participant_entity_type": "organization",
            },
            {
                "conversation_id": 9,
                "participant_id": 101,
                "raw_label": "单位",
                "graph_node_id": "company-1",
                "graph_node_label": "单位",
                "graph_node_type": "organization",
                "participant_label": "B公司",
                "participant_entity_type": "organization",
            },
            {
                "conversation_id": 9,
                "participant_id": 102,
                "raw_label": "当事人",
                "graph_node_id": "person-1",
                "graph_node_label": "当事人",
                "graph_node_type": "person",
                "participant_label": "张三",
                "participant_entity_type": "person",
            },
            {
                "conversation_id": 9,
                "participant_id": 102,
                "raw_label": "这个人",
                "graph_node_id": "person-1",
                "graph_node_label": "这个人",
                "graph_node_type": "person",
                "participant_label": "张三",
                "participant_entity_type": "person",
            },
        ],
    }
    dossier_graph_context = {
        "participants": [
            {"label": "B公司", "role": "用人单位", "entity_type": "organization"},
            {"label": "张三", "role": "劳动者本人", "entity_type": "person"},
        ],
        "participant_mentions": list(dossier_context["participant_mentions"]),
        "matched_nodes": [
            {"display_label": "B公司", "raw_label": "公司", "type": "organization"},
            {"display_label": "B公司", "raw_label": "单位", "type": "organization"},
            {"display_label": "张三", "raw_label": "当事人", "type": "person"},
            {"display_label": "张三", "raw_label": "这个人", "type": "person"},
        ],
    }
    return dossier_context, dossier_graph_context


def _run_task_with_message_understanding(
    monkeypatch: pytest.MonkeyPatch,
    *,
    question: str,
    message_understanding_result: dict[str, object],
    recent_messages: list[dict[str, object]] | None = None,
    case_summary: str = "",
    persistence_consent: str = "tracked",
    conversation_mode: str = "TRACKED_CASE",
    dossier_context: dict[str, Any] | None = None,
    dossier_graph_context: dict[str, Any] | None = None,
    legal_agent_response: dict[str, Any] | None = None,
    fail_on_legal_agent: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], list[tuple[int, int, int]], dict[str, Any]]:
    celery_module = _load_celery_module()
    persisted: dict[str, Any] = {}
    graph_calls: list[tuple[int, int, int]] = []
    trackers: dict[str, Any] = {
        "legal_agent_calls": 0,
    }
    resolved_dossier_context = dossier_context if isinstance(dossier_context, dict) else {"id": 12, "title": "劳动争议案卷"}
    resolved_graph_context = dossier_graph_context

    async def fake_run_task_async(conversation_id: int, user_id: int, current_question: str | None = None):
        return {
            "chat_history": "",
            "latest_user_input": current_question,
            "recent_messages": recent_messages or [],
            "case_summary": case_summary,
            "graph_memory": "",
            "saved_case_state": None,
            "persistence_consent": persistence_consent,
            "conversation_mode": conversation_mode,
            "dossier_id": 12,
            "dossier_context": resolved_dossier_context,
            "dossier_graph_summary": "",
            "dossier_graph_context": resolved_graph_context,
        }

    class FakeLegalAgent:
        async def ask(self, *args, **kwargs):
            trackers["legal_agent_calls"] += 1
            if fail_on_legal_agent:
                raise AssertionError("this paraphrase should not call legal_agent.ask")
            return legal_agent_response or {
                "response": "这是一个法律问题回答。",
                "sources": [],
                "route_intent": "GENERAL_CONSULT",
                "case_summary": "",
                "confidence_score": 0.6,
            }

    async def fake_understand_message_with_agent(**kwargs):
        return dict(message_understanding_result)

    async def fake_persist_dialog_turn_async(**kwargs):
        persisted.update(kwargs)
        return {"user_message_id": 101, "assistant_message_id": 102}

    async def fake_merge_message_metadata_async(**kwargs):
        metadata_patch = kwargs.get("metadata_patch")
        if isinstance(metadata_patch, dict):
            metadata = persisted.get("metadata")
            if isinstance(metadata, dict):
                metadata.update(metadata_patch)
        return None

    class FakeAsyncResult:
        def __init__(self, task_id: str) -> None:
            self.id = task_id

    class FakeGraphTask:
        @staticmethod
        def delay(conversation_id: int, user_id: int, message_id: int):
            graph_calls.append((conversation_id, user_id, message_id))
            return FakeAsyncResult(f"graph-task-{conversation_id}-{message_id}")

    monkeypatch.setattr(celery_module, "_run_task_async", fake_run_task_async)
    monkeypatch.setattr(celery_module, "legal_agent", FakeLegalAgent())
    monkeypatch.setattr(celery_module, "understand_message_with_agent", fake_understand_message_with_agent)
    monkeypatch.setattr(celery_module, "_persist_dialog_turn_async", fake_persist_dialog_turn_async)
    monkeypatch.setattr(celery_module, "_merge_message_metadata_async", fake_merge_message_metadata_async)
    monkeypatch.setattr(celery_module, "update_graph_task", FakeGraphTask())

    result = celery_module.ask_agent_task.run(
        1,
        9,
        "session-1",
        question,
        True,
        True,
    )
    return result, persisted, graph_calls, trackers


@pytest.mark.parametrize(
    ("question", "mention", "target_type"),
    [
        ("这里的公司指的是哪家公司？", "公司", "organization"),
        ("当前案卷里的当事人是谁？", "当事人", "self"),
        ("你说的这个人指谁？", "这个人", "person"),
        ("刚才提到的那家单位是哪一家？", "单位", "organization"),
        ("当事人和张三是同一个人吗？", "当事人", "person"),
    ],
)
def test_subject_reference_paraphrase_matrix_routes_through_dossier_memory(
    monkeypatch: pytest.MonkeyPatch,
    question: str,
    mention: str,
    target_type: str,
) -> None:
    dossier_context, dossier_graph_context = _build_subject_reference_context()
    result, persisted, graph_calls, trackers = _run_task_with_message_understanding(
        monkeypatch,
        question=question,
        message_understanding_result=_subject_reference_result(mention=mention, target_type=target_type),
        recent_messages=[
            {"message_id": 11, "role": "user", "content": "我和B公司签了劳动合同。"},
            {"message_id": 12, "role": "assistant", "content": "继续补充。"},
        ],
        case_summary="张三与B公司之间存在劳动关系争议。",
        dossier_context=dossier_context,
        dossier_graph_context=dossier_graph_context,
        fail_on_legal_agent=True,
    )

    steps = _step_lookup(result)
    action_summaries = [str(item.get("result_summary") or "") for item in result["agent_actions"]]

    assert result["message_understanding"]["intent"] == "subject_reference"
    assert result["message_understanding"]["route_hint"] == "DOSSIER_MEMORY"
    assert result["message_understanding"]["needs_legal_rag"] is False
    assert result["route_intent"] == "DOSSIER_MEMORY"
    assert "dossier_memory_subject_reference" in steps
    assert "legal_agent_answer" not in steps
    assert "graph_update" not in steps
    assert trackers["legal_agent_calls"] == 0
    assert graph_calls == []
    assert persisted["metadata"]["route_intent"] == "DOSSIER_MEMORY"
    assert all("General consult route selected" not in summary for summary in action_summaries)
    assert all("Retrieval pipeline completed" not in summary for summary in action_summaries)
    assert all("Final legal response generated" not in summary for summary in action_summaries)


@pytest.mark.parametrize(
    "question",
    [
        "B公司的人事通知我去签确认书",
        "后来王经理口头通知我不用来了",
        "我补充一下，公司其实有给我交过两个月社保",
        "实际入职时间是2024年3月",
        "工资其实是8500不是8000",
    ],
)
def test_fact_update_paraphrase_matrix_schedules_persistence_and_graph(
    monkeypatch: pytest.MonkeyPatch,
    question: str,
) -> None:
    result, persisted, graph_calls, trackers = _run_task_with_message_understanding(
        monkeypatch,
        question=question,
        message_understanding_result=_fact_update_result(question),
        recent_messages=[],
        case_summary="",
        fail_on_legal_agent=True,
    )

    steps = _step_lookup(result)

    assert result["message_understanding"]["intent"] == "fact_update"
    assert result["message_understanding"]["route_hint"] == "CASE_UPDATE"
    assert result["message_understanding"]["needs_structured_persistence"] is True
    assert result["message_understanding"]["needs_graph_update"] is True
    assert result["route_intent"] == "CASE_UPDATE"
    assert trackers["legal_agent_calls"] == 0
    assert steps["fact_persistence"]["status"] == "success"
    assert steps["graph_update"]["status"] == "success"
    assert steps["assistant_acknowledgement"]["status"] == "success"
    assert steps["graph_update"]["details"]["task_id"] == "graph-task-9-101"
    assert graph_calls == [(9, 1, 101)]
    assert persisted["metadata"]["route_intent"] == "CASE_UPDATE"


@pytest.mark.parametrize(
    "question",
    [
        "我确认这个事实",
        "是的，就是这样",
        "这个属实",
        "没错，刚才说的对",
        "确认上一条",
    ],
)
def test_confirm_previous_fact_paraphrase_matrix_uses_previous_fact_text(
    monkeypatch: pytest.MonkeyPatch,
    question: str,
) -> None:
    result, persisted, graph_calls, trackers = _run_task_with_message_understanding(
        monkeypatch,
        question=question,
        message_understanding_result=_confirm_previous_fact_result(target_text=None, target_message_id=71),
        recent_messages=[
            {"message_id": 71, "role": "user", "content": FACT_MESSAGE},
            {"message_id": 72, "role": "assistant", "content": "继续补充。"},
        ],
        case_summary="",
        fail_on_legal_agent=True,
    )

    steps = _step_lookup(result)

    assert result["message_understanding"]["intent"] == "confirm_previous_fact"
    assert result["message_understanding"]["route_hint"] == "CASE_UPDATE"
    assert persisted["user_metadata"]["confirmed_fact_text"] == FACT_MESSAGE
    assert persisted["user_metadata"]["graph_update_input_text"] == FACT_MESSAGE
    assert persisted["user_metadata"]["graph_update_input_text"] != question
    assert trackers["legal_agent_calls"] == 0
    assert steps["fact_confirmation"]["status"] == "success"
    assert steps["fact_persistence"]["status"] == "success"
    assert steps["graph_update"]["status"] == "success"
    assert graph_calls == [(9, 1, 101)]


@pytest.mark.parametrize(
    ("question", "intent", "route_hint"),
    [
        ("劳动合同法第八十七条规定了什么？", "legal_question", "LEGAL_QA"),
        ("未签劳动合同的双倍工资怎么算？", "legal_question", "LEGAL_QA"),
        ("我应该准备哪些证据去仲裁？", "advice_question", "GENERAL_CONSULT"),
    ],
)
def test_legal_and_advice_paraphrase_matrix_needs_rag_without_graph_updates(
    monkeypatch: pytest.MonkeyPatch,
    question: str,
    intent: str,
    route_hint: str,
) -> None:
    result, persisted, graph_calls, trackers = _run_task_with_message_understanding(
        monkeypatch,
        question=question,
        message_understanding_result=_legal_or_advice_result(
            intent=intent,
            route_hint=route_hint,
            question=question,
        ),
        recent_messages=[],
        case_summary="",
        legal_agent_response={
            "response": "这是沿用旧法律问答链路的回答。",
            "sources": [],
            "route_intent": "GENERAL_CONSULT",
            "case_summary": "",
            "confidence_score": 0.7,
        },
    )

    steps = _step_lookup(result)

    assert result["message_understanding"]["intent"] == intent
    assert result["message_understanding"]["needs_legal_rag"] is True
    assert result["message_understanding"]["needs_graph_update"] is False
    assert result["execution_plan"]["primary_route"] == route_hint
    assert result["execution_trace"]["legacy_fallback"]["used"] is True
    assert trackers["legal_agent_calls"] == 1
    assert "legal_agent_answer" in steps
    assert "graph_update" not in steps
    assert "fact_persistence" not in steps
    assert graph_calls == []
    assert persisted["case_summary"] == ""
