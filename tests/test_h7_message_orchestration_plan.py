from __future__ import annotations

import importlib
import sys
from types import ModuleType

import pytest

from app.services.message_orchestrator import build_execution_plan_from_message_understanding


FACT_MESSAGE = "B公司的人事通知我去签确认书。而且人事王经理口头通知我不用来了。"


def _subject_reference_result() -> dict[str, object]:
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
            "mention": "公司",
            "target_type": "organization",
        },
        "fact_update": None,
        "previous_fact_confirmation": None,
        "advice_question": None,
        "legal_question": None,
        "confidence": "high",
        "reason": "Current message asks for dossier subject reference.",
        "provider": "openai",
        "model": "gpt-5.1",
        "fallback_reason": None,
        "trace": {"steps": ["call_llm"], "warnings": []},
    }


def _clarification_response_result() -> dict[str, object]:
    return {
        "source": "message_understanding_agent",
        "intent": "clarification_response",
        "route_hint": "DOSSIER_MEMORY",
        "needs_legal_rag": False,
        "needs_dossier_graph_context": False,
        "needs_structured_persistence": False,
        "needs_graph_update": False,
        "subject_reference": None,
        "clarification_response": {
            "enabled": True,
            "question": "是B公司",
            "target_mention": "公司",
            "reference_type": "organization",
            "scope_attempted": ["current_conversation_graph"],
            "expected_response": "entity_name_or_context",
        },
        "fact_update": None,
        "previous_fact_confirmation": None,
        "advice_question": None,
        "legal_question": None,
        "confidence": "high",
        "reason": "Pending clarification follow-up detected.",
        "provider": "contract",
        "model": "pending-clarification-contract-v1",
        "fallback_reason": None,
        "trace": {"steps": ["build_request", "detect_pending_clarification"], "warnings": []},
    }


def _fact_update_result() -> dict[str, object]:
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
            "fact_text": FACT_MESSAGE,
            "entities": [
                {"label": "B公司", "type": "organization"},
                {"label": "王经理", "type": "person"},
                {"label": "确认书", "type": "document"},
            ],
            "events": [
                {
                    "summary": "王经理口头通知当事人不用来了",
                    "participants": ["王经理", "当事人"],
                }
            ],
        },
        "previous_fact_confirmation": None,
        "advice_question": None,
        "legal_question": None,
        "confidence": "high",
        "reason": "Current message adds case facts.",
        "provider": "openai",
        "model": "gpt-5.1",
        "fallback_reason": None,
        "trace": {"steps": ["call_llm"], "warnings": []},
    }


def _graph_with_nodes(nodes: list[dict[str, object]]) -> dict[str, object]:
    return {
        "query": "current conversation graph",
        "nodes": nodes,
        "edges": [],
    }


def _request_context_with_graph(
    *,
    message: str,
    graph: dict[str, object],
    recent_messages: list[dict[str, object]] | None = None,
    message_understanding: dict[str, object] | None = None,
):
    from app.services.agentic_request_context import build_agentic_request_context

    return build_agentic_request_context(
        request_id="req-graph-reference",
        user_id=1,
        conversation_id=9,
        dossier_id=12,
        message_text=message,
        recent_messages=recent_messages or [],
        persistence_consent="tracked",
        conversation_mode="TRACKED_CASE",
        current_conversation_graph=graph,
        message_understanding=message_understanding or {"intent": "fact_update"},
    )


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
        "reason": "Current message confirms the previous factual statement.",
        "provider": "openai",
        "model": "gpt-5.1",
        "fallback_reason": None,
        "trace": {"steps": ["call_llm"], "warnings": []},
    }


@pytest.mark.parametrize(
    "message",
    [
        "这里的公司指的是哪家公司？",
        "这个公司是谁？",
        "前面说的公司是哪一个？",
    ],
)
def test_build_execution_plan_subject_reference_paraphrases(message: str) -> None:
    plan = build_execution_plan_from_message_understanding(
        _subject_reference_result(),
        original_message=message,
        recent_messages=[],
        dossier_id=12,
        persistence_consent="tracked",
    )

    assert plan["source"] == "message_understanding"
    assert plan["intent"] == "subject_reference"
    assert plan["primary_route"] == "DOSSIER_MEMORY"
    assert plan["is_actionable"] is True
    assert [step["name"] for step in plan["steps"]] == ["dossier_memory_answer"]
    assert plan["resolved_inputs"]["effective_persistence_text"] == message
    assert plan["trace"]["provider"] == "openai"
    assert all(step["name"] != "graph_update" for step in plan["steps"])


def test_build_execution_plan_clarification_response_routes_to_dossier_memory() -> None:
    plan = build_execution_plan_from_message_understanding(
        _clarification_response_result(),
        original_message="是B公司",
        recent_messages=[],
        dossier_id=12,
        persistence_consent="tracked",
    )

    assert plan["intent"] == "clarification_response"
    assert plan["primary_route"] == "DOSSIER_MEMORY"
    assert plan["response_mode"] == "dossier_memory_answer"
    assert plan["is_actionable"] is True
    assert [step["name"] for step in plan["steps"]] == ["dossier_memory_answer"]
    assert plan["resolved_inputs"]["subject_reference_scope_order"] == ["current_conversation_graph"]


@pytest.mark.parametrize(
    "message",
    [
        FACT_MESSAGE,
        "后来王经理又口头说让我不用再来上班。",
        "我补充一下，人事让我先签确认书再谈补缴社保。",
    ],
)
def test_build_execution_plan_fact_update_paraphrases_use_current_message(message: str) -> None:
    plan = build_execution_plan_from_message_understanding(
        _fact_update_result(),
        original_message=message,
        recent_messages=[],
        dossier_id=12,
        persistence_consent="tracked",
    )

    assert plan["intent"] == "fact_update"
    assert plan["primary_route"] == "CASE_UPDATE"
    assert plan["is_actionable"] is True
    assert [step["name"] for step in plan["steps"]] == [
        "fact_persistence",
        "graph_update",
        "assistant_acknowledgement",
    ]
    assert plan["resolved_inputs"]["effective_persistence_text"] == message
    assert plan["resolved_inputs"]["graph_update_input_text"] == message


def test_build_execution_plan_does_not_preblock_fact_update_with_named_entities() -> None:
    message = "我想咨询一个劳动纠纷。我叫张三，在上海一家叫蓝海科技的公司做后端开发。公司从2023年5月开始拖欠我工资，到现在大概欠了我6万元。"
    plan = build_execution_plan_from_message_understanding(
        _fact_update_result(),
        original_message=message,
        recent_messages=[],
        dossier_id=12,
        persistence_consent="tracked",
        request_context=_request_context_with_graph(
            message=message,
            graph=_graph_with_nodes([]),
        ),
    )

    assert plan["response_mode"] == "tracked_case_refresh"
    assert plan["persistence_allowed"] is True
    assert plan["graph_update_allowed"] is True
    assert [step["name"] for step in plan["steps"]] == [
        "fact_persistence",
        "graph_update",
        "assistant_acknowledgement",
    ]
    assert "reference_guard" not in plan


def test_build_execution_plan_fact_update_defers_pronoun_safety_to_graph_write() -> None:
    message = "我没有签正式劳动合同，但是每天都去公司打卡，也有工资流水和微信聊天记录。"
    plan = build_execution_plan_from_message_understanding(
        _fact_update_result(),
        original_message=message,
        recent_messages=[],
        dossier_id=12,
        persistence_consent="tracked",
        request_context=_request_context_with_graph(
            message=message,
            graph=_graph_with_nodes(
                [
                    {"id": "person-zhang", "label": "张三", "type": "主体"},
                    {"id": "person-li", "label": "李四", "type": "主体"},
                    {"id": "company-lanhai", "label": "蓝海科技", "type": "主体"},
                ]
            ),
        ),
    )

    assert plan["response_mode"] == "tracked_case_refresh"
    assert plan["persistence_allowed"] is True
    assert plan["graph_update_allowed"] is True
    assert [step["name"] for step in plan["steps"]] == [
        "fact_persistence",
        "graph_update",
        "assistant_acknowledgement",
    ]
    assert plan["resolved_inputs"]["graph_update_input_text"] == message


def test_build_execution_plan_fact_update_does_not_preblock_generic_company_word() -> None:
    message = "公司没有给我签劳动合同。"
    plan = build_execution_plan_from_message_understanding(
        _fact_update_result(),
        original_message=message,
        recent_messages=[],
        dossier_id=12,
        persistence_consent="tracked",
        request_context=_request_context_with_graph(
            message=message,
            graph=_graph_with_nodes(
                [
                    {"id": "person-zhang", "label": "张三", "type": "主体", "properties": {"role": "current_party"}},
                    {"id": "company-lanhai", "label": "蓝海科技", "type": "主体"},
                    {"id": "company-outsourcing", "label": "外包公司", "type": "主体"},
                ]
            ),
        ),
    )

    assert plan["response_mode"] == "tracked_case_refresh"
    assert plan["graph_update_allowed"] is True
    assert [step["name"] for step in plan["steps"]] == [
        "fact_persistence",
        "graph_update",
        "assistant_acknowledgement",
    ]


def test_build_execution_plan_clarification_response_resumes_pending_graph_fact() -> None:
    original_fact = "我没有签正式劳动合同，但是每天都去公司打卡。"
    result = _clarification_response_result()
    result["clarification_response"]["pending_graph_update_input_text"] = original_fact
    plan = build_execution_plan_from_message_understanding(
        result,
        original_message="指的是张三",
        recent_messages=[],
        dossier_id=12,
        persistence_consent="tracked",
        request_context=_request_context_with_graph(
            message="指的是张三",
            graph=_graph_with_nodes([]),
            message_understanding=result,
        ),
    )

    assert plan["primary_route"] == "CASE_UPDATE"
    assert plan["response_mode"] == "tracked_case_refresh"
    assert plan["persistence_allowed"] is True
    assert plan["graph_update_allowed"] is True
    assert [step["name"] for step in plan["steps"]] == [
        "fact_persistence",
        "graph_update",
        "assistant_acknowledgement",
    ]
    assert original_fact in plan["resolved_inputs"]["graph_update_input_text"]
    assert "指代确认：指的是张三" in plan["resolved_inputs"]["graph_update_input_text"]


def test_build_execution_plan_temporary_fact_update_uses_transient_case_analysis() -> None:
    plan = build_execution_plan_from_message_understanding(
        _fact_update_result(),
        original_message=FACT_MESSAGE,
        recent_messages=[],
        dossier_id=12,
        persistence_consent="temporary",
    )

    assert plan["intent"] == "fact_update"
    assert plan["primary_route"] == "CASE_UPDATE"
    assert plan["response_mode"] == "temporary_case_analysis"
    assert plan["persistence_allowed"] is False
    assert plan["graph_update_allowed"] is False
    assert plan["is_actionable"] is True
    assert [step["name"] for step in plan["steps"]] == ["legal_agent_answer"]
    assert plan["resolved_inputs"]["effective_persistence_text"] == FACT_MESSAGE
    assert plan["resolved_inputs"]["graph_update_input_text"] == FACT_MESSAGE


@pytest.mark.parametrize(
    "message",
    [
        "我确认这个事实",
        "对，上面那件事属实",
        "确认上一条",
    ],
)
def test_build_execution_plan_confirm_previous_fact_paraphrases_use_previous_fact_text(
    message: str,
) -> None:
    plan = build_execution_plan_from_message_understanding(
        _confirm_previous_fact_result(target_text=None, target_message_id=71),
        original_message=message,
        recent_messages=[
            {"message_id": 71, "role": "user", "content": FACT_MESSAGE},
            {"message_id": 72, "role": "assistant", "content": "继续补充。"},
        ],
        dossier_id=12,
        persistence_consent="tracked",
    )

    assert plan["intent"] == "confirm_previous_fact"
    assert plan["primary_route"] == "CASE_UPDATE"
    assert plan["is_actionable"] is True
    assert [step["name"] for step in plan["steps"]] == [
        "fact_confirmation",
        "fact_persistence",
        "graph_update",
        "assistant_acknowledgement",
    ]
    assert plan["resolved_inputs"]["previous_fact_text"] == FACT_MESSAGE
    assert plan["resolved_inputs"]["effective_persistence_text"] == FACT_MESSAGE
    assert plan["resolved_inputs"]["graph_update_input_text"] == FACT_MESSAGE


def test_build_execution_plan_confirm_previous_fact_without_previous_fact_is_not_actionable() -> None:
    plan = build_execution_plan_from_message_understanding(
        _confirm_previous_fact_result(target_text=None, target_message_id=None),
        original_message="我确认这个事实",
        recent_messages=[],
        dossier_id=12,
        persistence_consent="tracked",
    )

    assert plan["intent"] == "confirm_previous_fact"
    assert plan["primary_route"] == "CASE_UPDATE"
    assert plan["is_actionable"] is False
    assert [step["name"] for step in plan["steps"]] == ["fact_confirmation"]
    assert plan["resolved_inputs"]["previous_fact_text"] == ""
    assert "confirmed_previous_fact_text_unavailable" in plan["trace"]["warnings"]


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

    sys.modules.pop("langchain_core", None)
    sys.modules.pop("langchain_core.messages", None)
    sys.modules.pop("langchain_community", None)
    sys.modules.pop("langchain_community.chat_models", None)

    fake_redis_mgr = ModuleType("app.memory.redis_mgr")
    fake_redis_mgr.get_redis_saver_for_current_loop = lambda: None
    sys.modules["app.memory.redis_mgr"] = fake_redis_mgr


def _load_celery_module():
    _install_celery_import_stubs()
    sys.modules.pop("app.celery_app", None)
    return importlib.import_module("app.celery_app")


def test_final_route_intent_preserves_out_of_scope_from_legal_answer_specialist() -> None:
    celery_module = _load_celery_module()

    route_intent = celery_module._resolve_final_route_intent(
        answer_route_intent="OUT_OF_SCOPE",
        subject_reference_short_circuit=False,
        case_update_plan_active=False,
        legal_answer_plan_active=True,
        agentic_route="LEGAL_QA",
    )

    assert route_intent == "OUT_OF_SCOPE"


def _run_ask_agent_task(
    monkeypatch: pytest.MonkeyPatch,
    *,
    question: str,
    recent_messages: list[dict[str, object]],
    message_understanding_result: dict[str, object],
    persistence_consent: str = "tracked",
    conversation_mode: str = "TRACKED_CASE",
    current_conversation_graph: dict[str, object] | None = None,
) -> tuple[object, dict[str, object], list[tuple[int, int, int]]]:
    celery_module = _load_celery_module()
    persisted: dict[str, object] = {}
    graph_calls: list[tuple[int, int, int]] = []

    async def fake_run_task_async(conversation_id: int, user_id: int, current_question: str | None = None):
        return {
            "chat_history": "",
            "latest_user_input": current_question,
            "recent_messages": recent_messages,
            "case_summary": "旧摘要",
            "graph_memory": "",
            "saved_case_state": None,
            "persistence_consent": persistence_consent,
            "conversation_mode": conversation_mode,
            "dossier_id": 12,
            "dossier_context": {"id": 12, "title": "劳动争议案卷"},
            "dossier_graph_summary": "",
            "dossier_graph_context": None,
            "current_conversation_graph": current_conversation_graph,
        }

    class FakeLegalAgent:
        async def ask(self, *args, **kwargs):
            return {
                "response": "主链路旧回答",
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

    class FakeGraphTask:
        @staticmethod
        def delay(conversation_id: int, user_id: int, message_id: int) -> None:
            graph_calls.append((conversation_id, user_id, message_id))

    monkeypatch.setattr(celery_module, "_run_task_async", fake_run_task_async)
    monkeypatch.setattr(celery_module, "legal_agent", FakeLegalAgent())
    monkeypatch.setattr(celery_module, "understand_message_with_agent", fake_understand_message_with_agent)
    monkeypatch.setattr(celery_module, "_persist_dialog_turn_async", fake_persist_dialog_turn_async)
    monkeypatch.setattr(celery_module, "update_graph_task", FakeGraphTask())

    result = celery_module.ask_agent_task.run(
        1,
        9,
        "session-1",
        question,
        True,
        True,
    )
    return result, persisted, graph_calls


def test_ask_agent_task_uses_execution_plan_for_fact_update_even_if_legacy_answer_route_is_general(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question = "后来王经理又口头说让我不用再来上班。"
    result, persisted, graph_calls = _run_ask_agent_task(
        monkeypatch,
        question=question,
        recent_messages=[],
        message_understanding_result=_fact_update_result(),
    )

    assert result["status"] == "success"
    assert result["route_intent"] == "CASE_UPDATE"
    assert result["execution_plan"]["primary_route"] == "CASE_UPDATE"
    assert "王经理" in persisted["case_summary"]
    assert persisted["metadata"]["execution_plan"]["primary_route"] == "CASE_UPDATE"
    assert graph_calls == [(9, 1, 101)]
    action_summaries = [str(item.get("result_summary") or "") for item in result["agent_actions"]]
    assert any("Execution plan selected CASE_UPDATE" in summary for summary in action_summaries)
    assert all("General consult route selected" not in summary for summary in action_summaries)


def test_ask_agent_task_schedules_graph_update_for_pronoun_fact_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question = "我没有签正式劳动合同，但是每天都去公司打卡，也有工资流水和微信聊天记录。"
    result, persisted, graph_calls = _run_ask_agent_task(
        monkeypatch,
        question=question,
        recent_messages=[],
        message_understanding_result=_fact_update_result(),
        current_conversation_graph=_graph_with_nodes(
            [
                {"id": "person-zhang", "label": "张三", "type": "主体"},
                {"id": "person-li", "label": "李四", "type": "主体"},
                {"id": "company-lanhai", "label": "蓝海科技", "type": "主体"},
            ]
        ),
    )

    assert result["status"] == "success"
    assert result["route_intent"] == "CASE_UPDATE"
    assert "已记录这条新的案情事实" in result["response"]
    assert graph_calls == [(9, 1, 101)]
    assert persisted["user_metadata"]["graph_update_input_text"] == question
    assert "pending_clarification" not in persisted["metadata"]


def test_ask_agent_task_reference_clarification_resumes_pending_graph_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_fact = "我没有签正式劳动合同，但是每天都去公司打卡。"
    message_understanding_result = _clarification_response_result()
    message_understanding_result["clarification_response"]["pending_graph_update_input_text"] = original_fact
    result, persisted, graph_calls = _run_ask_agent_task(
        monkeypatch,
        question="指的是张三",
        recent_messages=[
            {"message_id": 11, "role": "user", "content": original_fact, "metadata": {}},
            {
                "message_id": 12,
                "role": "assistant",
                "content": "为避免把案卷图谱写错，这里的“我”指的是哪位当事人？",
                "metadata": {
                    "pending_clarification": {
                        "type": "subject_reference_resolution",
                        "source": "graph_reference_guard",
                        "target_mention": "我",
                        "reference_type": "self",
                        "previous_question": "为避免把案卷图谱写错，这里的“我”指的是哪位当事人？",
                        "scope_attempted": ["current_conversation_graph"],
                        "expected_response": "entity_name_or_context",
                        "pending_graph_update_input_text": original_fact,
                    }
                },
            },
        ],
        message_understanding_result=message_understanding_result,
        current_conversation_graph=_graph_with_nodes(
            [
                {"id": "person-zhang", "label": "张三", "type": "主体"},
                {"id": "company-lanhai", "label": "蓝海科技", "type": "主体"},
            ]
        ),
    )

    assert result["status"] == "success"
    assert result["route_intent"] == "CASE_UPDATE"
    assert result["execution_plan"]["primary_route"] == "CASE_UPDATE"
    assert graph_calls == [(9, 1, 101)]
    assert original_fact in persisted["user_metadata"]["graph_update_input_text"]
    assert "指代确认：指的是张三" in persisted["user_metadata"]["graph_update_input_text"]
    assert "pending_clarification" not in persisted["metadata"]


def test_ask_agent_task_uses_execution_plan_for_confirm_previous_fact_graph_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, persisted, graph_calls = _run_ask_agent_task(
        monkeypatch,
        question="确认上一条",
        recent_messages=[
            {"message_id": 71, "role": "user", "content": FACT_MESSAGE},
        ],
        message_understanding_result=_confirm_previous_fact_result(target_text=None, target_message_id=71),
    )

    assert result["status"] == "success"
    assert result["route_intent"] == "CASE_UPDATE"
    assert result["execution_plan"]["resolved_inputs"]["previous_fact_text"] == FACT_MESSAGE
    assert persisted["user_metadata"]["graph_update_input_text"] == FACT_MESSAGE
    assert persisted["user_metadata"]["confirmed_fact_text"] == FACT_MESSAGE
    assert "王经理" in persisted["case_summary"]
    assert graph_calls == [(9, 1, 101)]
    action_summaries = [str(item.get("result_summary") or "") for item in result["agent_actions"]]
    assert any("Execution plan selected CASE_UPDATE" in summary for summary in action_summaries)
    assert any("Previous fact confirmed for graph update" in summary for summary in action_summaries)


@pytest.mark.parametrize(
    ("persistence_consent", "conversation_mode"),
    [
        ("temporary", "TRACKED_CASE"),
        ("tracked", "LEGAL_QA"),
    ],
)
def test_execution_plan_guards_block_graph_update_when_required(
    monkeypatch: pytest.MonkeyPatch,
    persistence_consent: str,
    conversation_mode: str,
) -> None:
    result, persisted, graph_calls = _run_ask_agent_task(
        monkeypatch,
        question="我确认这个事实",
        recent_messages=[
            {"message_id": 71, "role": "user", "content": FACT_MESSAGE},
        ],
        message_understanding_result=_confirm_previous_fact_result(target_text=None, target_message_id=71),
        persistence_consent=persistence_consent,
        conversation_mode=conversation_mode,
    )

    assert result["status"] == "success"
    assert result["route_intent"] == "CASE_UPDATE"
    assert persisted["case_summary"] == ""
    assert graph_calls == []
