from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path
from types import ModuleType


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def install_graph_import_stubs() -> None:
    fake_json_repair = ModuleType("json_repair")
    fake_json_repair.repair_json = lambda content: content
    sys.modules["json_repair"] = fake_json_repair

    fake_openai = ModuleType("openai")

    class FakeAsyncOpenAI:
        def __init__(self, *args, **kwargs) -> None:
            pass

    fake_openai.AsyncOpenAI = FakeAsyncOpenAI
    sys.modules["openai"] = fake_openai

    fake_langchain_community = ModuleType("langchain_community")
    fake_langchain_chat_models = ModuleType("langchain_community.chat_models")

    class FakeChatZhipuAI:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def ainvoke(self, *args, **kwargs):
            return type("Response", (), {"content": "[]"})()

    fake_langchain_chat_models.ChatZhipuAI = FakeChatZhipuAI
    fake_langchain_community.chat_models = fake_langchain_chat_models
    sys.modules["langchain_community"] = fake_langchain_community
    sys.modules["langchain_community.chat_models"] = fake_langchain_chat_models

    fake_langchain_core = ModuleType("langchain_core")
    fake_langchain_messages = ModuleType("langchain_core.messages")

    class FakeMessage:
        def __init__(self, content: str = "") -> None:
            self.content = content

    fake_langchain_messages.AnyMessage = FakeMessage
    fake_langchain_messages.BaseMessage = FakeMessage
    fake_langchain_messages.BaseMessageChunk = FakeMessage
    fake_langchain_messages.HumanMessage = FakeMessage
    fake_langchain_messages.MessageLikeRepresentation = FakeMessage
    fake_langchain_messages.RemoveMessage = FakeMessage
    fake_langchain_messages.SystemMessage = FakeMessage
    fake_langchain_messages.convert_to_messages = lambda value: value
    fake_langchain_messages.message_chunk_to_message = lambda value: value
    fake_langchain_core.messages = fake_langchain_messages
    sys.modules["langchain_core"] = fake_langchain_core
    sys.modules["langchain_core.messages"] = fake_langchain_messages


def load_graph_extractor_module():
    install_graph_import_stubs()
    sys.modules.pop("app.services.graph_extractor", None)
    return importlib.import_module("app.services.graph_extractor")


def _allow_guard(actions: list[dict]) -> dict:
    return {
        "source": "graph_reference_guard",
        "decision": "allow",
        "confidence": "high",
        "needs_clarification": False,
        "clarification_questions": [],
        "pending_clarification": None,
        "reference_resolutions": [],
        "allowed_actions": actions,
        "blocked_actions": [],
    }


def _clarify_guard(*, mention: str, question: str, message: str, blocked_actions: list[dict]) -> dict:
    return {
        "source": "graph_reference_guard",
        "decision": "clarify",
        "confidence": "low",
        "needs_clarification": True,
        "clarification_questions": [question],
        "pending_clarification": {
            "type": "subject_reference_resolution",
            "source": "graph_reference_guard",
            "target_mention": mention,
            "reference_type": "unknown",
            "previous_question": question,
            "scope_attempted": ["current_conversation_graph"],
            "expected_response": "entity_name_or_context",
            "pending_graph_update_input_text": message,
        },
        "reference_resolutions": [],
        "allowed_actions": [],
        "blocked_actions": blocked_actions,
    }


def test_extract_case_triples_preserves_multiple_explicit_entities(monkeypatch) -> None:
    graph_extractor = load_graph_extractor_module()

    payload = [
        {
            "source": "A公司",
            "source_category": "主体",
            "relation": "签订劳动合同",
            "target": "我",
            "target_category": "主体",
        },
        {
            "source": "B公司",
            "source_category": "主体",
            "relation": "发放工资",
            "target": "我",
            "target_category": "主体",
        },
        {
            "source": "C公司",
            "source_category": "主体",
            "relation": "缴纳社保",
            "target": "我",
            "target_category": "主体",
        },
        {
            "source": "王经理",
            "source_category": "主体",
            "relation": "口头通知",
            "target": "我",
            "target_category": "主体",
        },
    ]

    class FakeLLM:
        async def ainvoke(self, messages):
            return type("Response", (), {"content": json.dumps(payload, ensure_ascii=False)})()

    monkeypatch.setattr(graph_extractor, "_get_graph_llm", lambda: FakeLLM())

    result = asyncio.run(
        graph_extractor.extract_case_triples(
            "我在A公司上班，劳动合同是和A公司签的，但工资一直由B公司发放，社保由C公司缴纳。后来王经理口头通知我不用来了。"
        )
    )

    sources = {item["source"] for item in result}
    targets = {item["target"] for item in result}

    assert {"A公司", "B公司", "C公司", "王经理"}.issubset(sources)
    assert "用人单位" not in sources
    assert "领导" not in sources
    assert "当事人" in targets


def test_normalize_triples_does_not_let_generic_label_override_explicit_entity() -> None:
    graph_extractor = load_graph_extractor_module()

    normalized = graph_extractor._normalize_triples(
        [
            {
                "source": "A公司",
                "source_category": "主体",
                "relation": "拖欠工资",
                "target": "我",
                "target_category": "主体",
            },
            {
                "source": "公司",
                "source_category": "主体",
                "relation": "声称",
                "target": "外包员工",
                "target_category": "主体",
            },
        ]
    )

    sources = [item["source"] for item in normalized]
    assert "A公司" in sources
    assert "用人单位" not in sources
    assert "公司" in sources
    assert graph_extractor._canonicalize_entity("A公司") == "A公司"
    assert graph_extractor._canonicalize_entity("公司") == "公司"


def test_normalize_triples_allows_generic_nodes_when_no_explicit_entity_exists() -> None:
    graph_extractor = load_graph_extractor_module()

    normalized = graph_extractor._normalize_triples(
        [
            {
                "source": "公司",
                "source_category": "主体",
                "relation": "未签劳动合同",
                "target": "我",
                "target_category": "主体",
            },
            {
                "source": "公司",
                "source_category": "主体",
                "relation": "未缴纳社保",
                "target": "我",
                "target_category": "主体",
            },
        ]
    )

    assert len(normalized) == 2
    assert all(item["source"] == "公司" for item in normalized)
    assert all(item["target"] == "当事人" for item in normalized)


def test_normalize_actions_resolves_self_anchor_to_existing_named_party() -> None:
    graph_extractor = load_graph_extractor_module()

    normalized = graph_extractor._normalize_actions(
        {
            "actions": [
                {
                    "action": "ADD",
                    "source": "当事人",
                    "source_category": "主体",
                    "relation": "持有",
                    "target": "工资流水",
                    "target_category": "客体",
                },
                {
                    "action": "ADD",
                    "source": "微信聊天记录",
                    "source_category": "客体",
                    "relation": "证明",
                    "target": "我",
                    "target_category": "主体",
                },
            ]
        },
        existing_nodes={"张三", "蓝海科技"},
        self_anchor_target="张三",
    )

    assert normalized[0]["source"] == "张三"
    assert normalized[1]["target"] == "张三"
    assert all(item["source"] != "当事人" for item in normalized)
    assert all(item["target"] != "当事人" for item in normalized)


def test_normalize_triples_enforces_responsibility_category_contract() -> None:
    graph_extractor = load_graph_extractor_module()

    normalized = graph_extractor._normalize_triples(
        [
            {
                "source": "张三",
                "source_category": "主体",
                "relation": "任职于",
                "target": "蓝海科技",
                "target_category": "客体",
            },
            {
                "source": "蓝海科技",
                "source_category": "客体",
                "relation": "实施",
                "target": "拖欠工资",
                "target_category": "主体",
            },
            {
                "source": "法定代表人王某",
                "source_category": "客体",
                "relation": "代表",
                "target": "蓝海科技",
                "target_category": "主体",
            },
            {
                "source": "王经理",
                "source_category": "客体",
                "relation": "通知",
                "target": "违法辞退",
                "target_category": "主体",
            },
            {
                "source": "HR李某",
                "source_category": "客体",
                "relation": "发送",
                "target": "微信聊天记录",
                "target_category": "主体",
            },
        ]
    )

    by_pair = {(item["source"], item["target"]): item for item in normalized}
    assert by_pair[("张三", "蓝海科技")]["target_category"] == "主体"
    assert by_pair[("蓝海科技", "拖欠工资")]["source_category"] == "主体"
    assert by_pair[("蓝海科技", "拖欠工资")]["target_category"] == "事件"
    assert by_pair[("法定代表人王某", "蓝海科技")]["source_category"] == "主体"
    assert by_pair[("王经理", "违法辞退")]["source_category"] == "主体"
    assert by_pair[("王经理", "违法辞退")]["target_category"] == "事件"
    assert by_pair[("HR李某", "微信聊天记录")]["source_category"] == "主体"
    assert by_pair[("HR李某", "微信聊天记录")]["target_category"] == "客体"


def test_normalize_actions_keeps_facts_evidence_amounts_and_dates_out_of_subjects() -> None:
    graph_extractor = load_graph_extractor_module()

    normalized = graph_extractor._normalize_actions(
        {
            "actions": [
                {
                    "action": "ADD",
                    "source": "微信聊天记录",
                    "source_category": "主体",
                    "relation": "证明",
                    "target": "工资流水",
                    "target_category": "主体",
                },
                {
                    "action": "ADD",
                    "source": "劳动合同",
                    "source_category": "主体",
                    "relation": "约定",
                    "target": "6万元",
                    "target_category": "主体",
                },
                {
                    "action": "ADD",
                    "source": "拖欠工资",
                    "source_category": "主体",
                    "relation": "发生于",
                    "target": "2023年5月",
                    "target_category": "主体",
                },
                {
                    "action": "ADD",
                    "source": "欠薪后果",
                    "source_category": "主体",
                    "relation": "包括",
                    "target": "经济损失",
                    "target_category": "主体",
                },
            ]
        }
    )

    by_source = {item["source"]: item for item in normalized}
    assert by_source["微信聊天记录"]["source_category"] == "客体"
    assert by_source["微信聊天记录"]["target_category"] == "客体"
    assert by_source["劳动合同"]["source_category"] == "客体"
    assert by_source["劳动合同"]["target_category"] == "客体"
    assert by_source["拖欠工资"]["source_category"] == "事件"
    assert by_source["拖欠工资"]["target_category"] == "客体"
    assert by_source["欠薪后果"]["source_category"] == "后果"
    assert by_source["欠薪后果"]["target_category"] == "后果"


def test_normalize_actions_keeps_software_channels_out_of_subjects() -> None:
    graph_extractor = load_graph_extractor_module()

    normalized = graph_extractor._normalize_actions(
        {
            "actions": [
                {
                    "action": "ADD",
                    "source": "李四",
                    "source_category": "主体",
                    "relation": "通过",
                    "target": "微信",
                    "target_category": "主体",
                },
                {
                    "action": "ADD",
                    "source": "王经理",
                    "source_category": "主体",
                    "relation": "通过",
                    "target": "钉钉",
                    "target_category": "主体",
                },
                {
                    "action": "ADD",
                    "source": "HR李某",
                    "source_category": "主体",
                    "relation": "发送",
                    "target": "飞书通知",
                    "target_category": "主体",
                },
                {
                    "action": "ADD",
                    "source": "蓝海科技",
                    "source_category": "主体",
                    "relation": "通过",
                    "target": "短信",
                    "target_category": "主体",
                },
                {
                    "action": "ADD",
                    "source": "老板",
                    "source_category": "主体",
                    "relation": "通过",
                    "target": "邮件",
                    "target_category": "主体",
                },
                {
                    "action": "ADD",
                    "source": "财务",
                    "source_category": "主体",
                    "relation": "通过",
                    "target": "电话",
                    "target_category": "主体",
                },
                {
                    "action": "ADD",
                    "source": "腾讯公司",
                    "source_category": "客体",
                    "relation": "运营",
                    "target": "微信",
                    "target_category": "主体",
                },
            ]
        }
    )

    by_pair = {(item["source"], item["target"]): item for item in normalized}
    assert by_pair[("李四", "微信")]["target_category"] == "客体"
    assert by_pair[("王经理", "钉钉")]["target_category"] == "客体"
    assert by_pair[("HR李某", "飞书通知")]["target_category"] == "客体"
    assert by_pair[("蓝海科技", "短信")]["target_category"] == "客体"
    assert by_pair[("老板", "邮件")]["target_category"] == "客体"
    assert by_pair[("财务", "电话")]["target_category"] == "客体"
    assert by_pair[("腾讯公司", "微信")]["source_category"] == "主体"
    assert by_pair[("腾讯公司", "微信")]["target_category"] == "客体"


def test_infer_self_anchor_target_requires_unique_specific_person() -> None:
    graph_extractor = load_graph_extractor_module()

    assert graph_extractor._infer_self_anchor_target_from_records(
        [
            {"name": "张三", "category": "主体"},
            {"name": "蓝海科技", "category": "主体"},
        ]
    ) == "张三"
    assert graph_extractor._infer_self_anchor_target_from_records(
        [
            {"name": "张三", "category": "主体"},
            {"name": "李四", "category": "主体"},
        ]
    ) == ""
    assert graph_extractor._infer_self_anchor_target_from_records(
        [
            {"name": "张三", "category": "主体", "role": "current_party"},
            {"name": "李四", "category": "主体"},
        ]
    ) == "张三"


def test_execute_graph_actions_resolves_self_anchor_before_writing(monkeypatch) -> None:
    graph_extractor = load_graph_extractor_module()
    merge_calls = []

    class FakeResult:
        def __iter__(self):
            return iter(
                [
                    {"name": "张三", "category": "主体", "role": "current_party"},
                    {"name": "蓝海科技", "category": "主体"},
                ]
            )

        def consume(self):
            return None

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def run(self, query, params=None):
            if "RETURN n.name AS name" in query:
                return FakeResult()
            return FakeResult()

    class FakeDriver:
        def session(self):
            return FakeSession()

        def close(self):
            return None

    def fake_merge_fact(session, conversation_id, fact, user_id=0):
        merge_calls.append(fact)

    monkeypatch.setattr(graph_extractor, "_build_driver", lambda: FakeDriver())
    monkeypatch.setattr(graph_extractor, "_merge_fact", fake_merge_fact)

    result = graph_extractor._execute_graph_actions_sync(
        901,
        [
            {
                "action": "ADD",
                "source": "当事人",
                "source_category": "主体",
                "relation": "持有",
                "target": "工资流水",
                "target_category": "客体",
                "old_target": "",
                "target_name": "",
            }
        ],
        user_id=7,
    )

    assert merge_calls[0]["source"] == "张三"
    assert result["actions"][0]["source"] == "张三"
    assert result["actions_applied"] == 1


def test_reference_guard_rewrites_unambiguous_self_fact_before_graph_write(monkeypatch) -> None:
    graph_extractor = load_graph_extractor_module()

    async def fake_extract_incremental_graph_actions(user_input):
        return [
            {
                "action": "ADD",
                "source": "公司",
                "source_category": "主体",
                "relation": "拖欠工资",
                "target": "我",
                "target_category": "主体",
                "old_target": "",
            },
            {
                "action": "ADD",
                "source": "我",
                "source_category": "主体",
                "relation": "持有",
                "target": "微信聊天记录",
                "target_category": "客体",
                "old_target": "",
            }
        ]

    async def fake_get_case_graph_node_records(conversation_id):
        return [
            {"name": "张三", "category": "主体", "role": "current_party"},
            {"name": "蓝海科技", "category": "主体"},
        ]

    async def fake_execute_graph_actions(conversation_id, actions, user_id=0):
        return {"conversation_id": conversation_id, "actions_applied": len(actions), "actions": actions}

    monkeypatch.setattr(graph_extractor, "extract_incremental_graph_actions", fake_extract_incremental_graph_actions)
    monkeypatch.setattr(graph_extractor, "get_case_graph_node_records", fake_get_case_graph_node_records)
    monkeypatch.setattr(graph_extractor, "execute_graph_actions", fake_execute_graph_actions)

    async def fake_reference_guard(**kwargs):
        return _allow_guard(
            [
                {
                    "action": "ADD",
                    "source": "蓝海科技",
                    "source_category": "主体",
                    "relation": "拖欠工资",
                    "target": "张三",
                    "target_category": "主体",
                    "old_target": "",
                },
                {
                    "action": "ADD",
                    "source": "张三",
                    "source_category": "主体",
                    "relation": "持有",
                    "target": "微信聊天记录",
                    "target_category": "客体",
                    "old_target": "",
                }
            ]
        )

    monkeypatch.setattr(graph_extractor, "evaluate_graph_action_reference_guard_async", fake_reference_guard)

    async def fake_get_case_graph_node_names(conversation_id):
        return []

    async def fake_format_current_graph_state(conversation_id):
        return "[]"

    async def fake_plan_graph_gc(*args, **kwargs):
        return {"obsolete_nodes": [], "obsolete_edges": []}

    async def fake_delete_obsolete_nodes(*args, **kwargs):
        return 0

    async def fake_delete_obsolete_edges(*args, **kwargs):
        return 0

    monkeypatch.setattr(graph_extractor, "get_case_graph_node_names", fake_get_case_graph_node_names)
    monkeypatch.setattr(graph_extractor, "format_current_graph_state", fake_format_current_graph_state)
    monkeypatch.setattr(graph_extractor, "plan_graph_gc", fake_plan_graph_gc)
    monkeypatch.setattr(graph_extractor, "delete_obsolete_nodes", fake_delete_obsolete_nodes)
    monkeypatch.setattr(graph_extractor, "delete_obsolete_edges", fake_delete_obsolete_edges)

    result = asyncio.run(
        graph_extractor.refresh_case_graph(
            901,
            "我没有签正式劳动合同，但是每天都去公司打卡，也有工资流水和微信聊天记录。",
            user_id=7,
        )
    )

    assert result["needs_clarification"] is False
    assert result["actions_applied"] == 2
    assert result["actions"][0]["source"] == "蓝海科技"
    assert result["actions"][0]["target"] == "张三"
    assert result["actions"][1]["source"] == "张三"
    assert all(item["target"] != "当事人" for item in result["actions"])


def test_semantic_reference_guard_allows_user_sentence_when_model_resolves_company_and_self() -> None:
    import app.services.graph_reference_guard as guard

    actions = [
        {
            "action": "ADD",
            "source": "公司",
            "source_category": "主体",
            "relation": "拖欠工资",
            "target": "我",
            "target_category": "主体",
            "old_target": "",
        }
    ]

    async def fake_model(payload):
        assert "公司" in payload["mentions_to_check"]
        assert "我" in payload["mentions_to_check"]
        return {
            "provider": "fake",
            "model": "semantic-test",
            "content": json.dumps(
                {
                    "decision": "allow",
                    "confidence": "high",
                    "reason": "原句明确给出自称张三和其任职公司蓝海科技。",
                    "clarification_question": "",
                    "resolutions": [
                        {"mention": "我", "resolved_label": "张三", "reference_type": "self", "confidence": "high"},
                        {"mention": "公司", "resolved_label": "蓝海科技", "reference_type": "organization", "confidence": "high"},
                    ],
                    "rewritten_actions": [
                        {
                            "action": "ADD",
                            "source": "蓝海科技",
                            "source_category": "主体",
                            "relation": "拖欠工资",
                            "target": "张三",
                            "target_category": "主体",
                            "old_target": "",
                            "target_name": "",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        }

    result = asyncio.run(
        guard.evaluate_graph_action_reference_guard_async(
            user_input="我想咨询一个劳动纠纷。我叫张三，在上海一家叫蓝海科技的公司做后端开发。公司从2023年5月开始拖欠我工资，到现在大概欠了我6万元。",
            actions=actions,
            existing_node_records=[],
            model_caller=fake_model,
        )
    )

    assert result["needs_clarification"] is False
    assert result["allowed_actions"][0]["source"] == "蓝海科技"
    assert result["allowed_actions"][0]["target"] == "张三"


def test_reference_guard_allows_named_entities_without_clarifying_new_person() -> None:
    import app.services.graph_reference_guard as guard

    actions = [
        {
            "action": "ADD",
            "source": "李四",
            "source_category": "主体",
            "relation": "老板/负责人",
            "target": "蓝海科技",
            "target_category": "主体",
            "old_target": "",
        },
        {
            "action": "ADD",
            "source": "李四",
            "source_category": "主体",
            "relation": "承诺发放",
            "target": "工资",
            "target_category": "客体",
            "old_target": "",
        },
    ]

    async def fail_model(payload):
        raise AssertionError(f"named entities should not invoke reference guard model: {payload}")

    result = asyncio.run(
        guard.evaluate_graph_action_reference_guard_async(
            user_input="蓝海科技的老板叫李四，他一直在微信上跟我说等融资到账就把工资发给我，但已经拖了好几个月。",
            actions=actions,
            existing_node_records=[{"name": "蓝海科技", "category": "主体"}],
            model_caller=fail_model,
        )
    )

    assert result["needs_clarification"] is False
    assert result["reason"] == "no_unresolved_action_references"
    assert result["allowed_actions"] == actions


def test_reference_guard_only_sends_generic_mentions_to_model() -> None:
    import app.services.graph_reference_guard as guard

    actions = [
        {
            "action": "ADD",
            "source": "他",
            "source_category": "主体",
            "relation": "承诺发放",
            "target": "工资",
            "target_category": "客体",
            "old_target": "",
        }
    ]

    async def fake_model(payload):
        assert payload["mentions_to_check"] == ["他"]
        assert "工资" not in payload["mentions_to_check"]
        return {
            "provider": "fake",
            "model": "semantic-test",
            "content": json.dumps(
                {
                    "decision": "allow",
                    "confidence": "high",
                    "reason": "原句明确说明李四是蓝海科技老板，后续他指李四。",
                    "clarification_question": "",
                    "resolutions": [
                        {"mention": "他", "resolved_label": "李四", "reference_type": "person", "confidence": "high"}
                    ],
                    "rewritten_actions": [
                        {
                            "action": "ADD",
                            "source": "李四",
                            "source_category": "主体",
                            "relation": "承诺发放",
                            "target": "工资",
                            "target_category": "客体",
                            "old_target": "",
                            "target_name": "",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        }

    result = asyncio.run(
        guard.evaluate_graph_action_reference_guard_async(
            user_input="蓝海科技的老板叫李四，他一直在微信上跟我说等融资到账就把工资发给我。",
            actions=actions,
            existing_node_records=[{"name": "蓝海科技", "category": "主体"}],
            model_caller=fake_model,
        )
    )

    assert result["needs_clarification"] is False
    assert result["allowed_actions"][0]["source"] == "李四"


def test_semantic_reference_guard_applies_org_resolution_and_keeps_self_anchor_pending() -> None:
    import app.services.graph_reference_guard as guard

    actions = [
        {
            "action": "ADD",
            "source": "公司",
            "source_category": "主体",
            "relation": "未签订",
            "target": "我",
            "target_category": "主体",
            "old_target": "",
        }
    ]

    async def fake_model(payload):
        return {
            "provider": "fake",
            "model": "semantic-test",
            "content": json.dumps(
                {
                    "decision": "allow",
                    "confidence": "high",
                    "reason": "模型误放行但没有返回重写动作。",
                    "clarification_question": "",
                    "resolutions": [
                        {"mention": "公司", "resolved_label": "蓝海科技", "reference_type": "organization", "confidence": "high"}
                    ],
                    "rewritten_actions": [],
                },
                ensure_ascii=False,
            ),
        }

    result = asyncio.run(
        guard.evaluate_graph_action_reference_guard_async(
            user_input="公司没有给我签劳动合同。",
            actions=actions,
            existing_node_records=[{"name": "张三", "category": "主体"}, {"name": "蓝海科技", "category": "主体"}],
            model_caller=fake_model,
        )
    )

    assert result["needs_clarification"] is True
    assert result["allowed_actions"] == [
        {
            **actions[0],
            "source": "蓝海科技",
            "target": "当事人",
        }
    ]
    assert result["blocked_actions"] == []


def test_reference_guard_partially_writes_clear_actions_when_model_leaves_generic_residue() -> None:
    import app.services.graph_reference_guard as guard

    actions = [
        {
            "action": "ADD",
            "source": "当事人",
            "source_category": "主体",
            "relation": "任职于",
            "target": "A公司",
            "target_category": "主体",
            "old_target": "",
        },
        {
            "action": "ADD",
            "source": "A公司",
            "source_category": "主体",
            "relation": "未签订",
            "target": "劳动合同",
            "target_category": "客体",
            "old_target": "",
        },
        {
            "action": "ADD",
            "source": "A公司",
            "source_category": "主体",
            "relation": "口头辞退",
            "target": "当事人",
            "target_category": "主体",
            "old_target": "",
        },
    ]

    async def fake_model(payload):
        assert payload["mentions_to_check"] == ["当事人"]
        return {
            "provider": "fake",
            "model": "semantic-test",
            "content": json.dumps(
                {
                    "decision": "allow",
                    "confidence": "high",
                    "reason": "A公司是明确责任主体，但当事人未能稳定重写。",
                    "clarification_question": "",
                    "resolutions": [],
                    "rewritten_actions": actions,
                },
                ensure_ascii=False,
            ),
        }

    result = asyncio.run(
        guard.evaluate_graph_action_reference_guard_async(
            user_input="我补充一下案情：我在A公司工作两年，月薪12000，公司一直没签劳动合同，2024年4月4日公司口头辞退我。",
            actions=actions,
            existing_node_records=[],
            model_caller=fake_model,
        )
    )

    assert result["needs_clarification"] is True
    assert result["reason"].endswith("partial_write_with_unresolved_reference")
    assert result["allowed_actions"] == [
        {
            **actions[0],
            "source": "当事人",
        },
        actions[1],
        {
            **actions[2],
            "target": "当事人",
        },
    ]
    assert result["blocked_actions"] == []


def test_reference_guard_writes_self_anchor_placeholder_when_all_actions_include_self_residue() -> None:
    import app.services.graph_reference_guard as guard

    actions = [
        {
            "action": "ADD",
            "source": "当事人",
            "source_category": "主体",
            "relation": "任职于",
            "target": "A公司",
            "target_category": "主体",
            "old_target": "",
        },
        {
            "action": "ADD",
            "source": "A公司",
            "source_category": "主体",
            "relation": "口头辞退",
            "target": "当事人",
            "target_category": "主体",
            "old_target": "",
        },
    ]

    async def fake_model(payload):
        return {
            "provider": "fake",
            "model": "semantic-test",
            "content": json.dumps(
                {
                    "decision": "allow",
                    "confidence": "high",
                    "reason": "原句是第一人称事实补充，但当前当事人姓名尚未确认。",
                    "clarification_question": "",
                    "resolutions": [],
                    "rewritten_actions": actions,
                },
                ensure_ascii=False,
            ),
        }

    result = asyncio.run(
        guard.evaluate_graph_action_reference_guard_async(
            user_input="我补充一下案情：我在A公司工作两年，公司一直没签劳动合同，2024年4月4日公司口头辞退我。",
            actions=actions,
            existing_node_records=[],
            model_caller=fake_model,
        )
    )

    assert result["needs_clarification"] is True
    assert result["allowed_actions"] == actions
    assert result["blocked_actions"] == []
    assert result["pending_clarification"]["target_mention"] == "当事人"


def test_reference_guard_blocks_third_party_generic_while_writing_clear_actions() -> None:
    import app.services.graph_reference_guard as guard

    actions = [
        {
            "action": "ADD",
            "source": "A公司",
            "source_category": "主体",
            "relation": "通知补签",
            "target": "协议",
            "target_category": "客体",
            "old_target": "",
        },
        {
            "action": "ADD",
            "source": "人事",
            "source_category": "主体",
            "relation": "通知",
            "target": "当事人",
            "target_category": "主体",
            "old_target": "",
        },
    ]

    async def fake_model(payload):
        return {
            "provider": "fake",
            "model": "semantic-test",
            "content": json.dumps(
                {
                    "decision": "allow",
                    "confidence": "high",
                    "reason": "A公司事实明确，人事仍是泛称。",
                    "clarification_question": "",
                    "resolutions": [],
                    "rewritten_actions": actions,
                },
                ensure_ascii=False,
            ),
        }

    result = asyncio.run(
        guard.evaluate_graph_action_reference_guard_async(
            user_input="后来A公司的人事又通知我补签协议。",
            actions=actions,
            existing_node_records=[],
            model_caller=fake_model,
        )
    )

    assert result["needs_clarification"] is True
    assert result["allowed_actions"] == [actions[0]]
    assert result["blocked_actions"] == [actions[1]]


def test_refresh_case_graph_applies_partial_guard_actions_before_clarification(monkeypatch) -> None:
    graph_extractor = load_graph_extractor_module()
    applied_actions: list[dict] = []

    async def fake_extract_incremental_graph_actions(user_input):
        return [
            {
                "action": "ADD",
                "source": "当事人",
                "source_category": "主体",
                "relation": "任职于",
                "target": "A公司",
                "target_category": "主体",
                "old_target": "",
            },
            {
                "action": "ADD",
                "source": "A公司",
                "source_category": "主体",
                "relation": "未签订",
                "target": "劳动合同",
                "target_category": "客体",
                "old_target": "",
            },
        ]

    async def fake_get_case_graph_node_records(conversation_id):
        return []

    async def fake_reference_guard(**kwargs):
        return {
            "source": "graph_reference_guard",
            "model_version": "semantic-reference-guard-v1",
            "provider": "fake",
            "model": "semantic-test",
            "decision": "clarify",
            "confidence": "high",
            "reason": "partial_write_with_unresolved_reference",
            "needs_clarification": True,
            "clarification_questions": ["为避免把案卷图谱写错，这里的“当事人”具体指谁？请直接回复主体名称。"],
            "pending_clarification": {
                "type": "subject_reference_resolution",
                "source": "graph_reference_guard",
                "target_mention": "当事人",
                "pending_graph_update_input_text": kwargs["user_input"],
            },
            "reference_resolutions": [],
            "allowed_actions": [
                {
                    **kwargs["actions"][0],
                    "source": "当事人",
                },
                kwargs["actions"][1],
            ],
            "blocked_actions": [],
        }

    async def fake_execute_graph_actions(conversation_id, actions, user_id=0):
        applied_actions.extend(actions)
        return {"conversation_id": conversation_id, "actions_applied": len(actions), "actions": actions}

    async def fake_get_case_graph_node_names(conversation_id):
        return ["A公司", "劳动合同"]

    async def fake_format_current_graph_state(conversation_id):
        return "[]"

    async def fake_plan_graph_gc(*args, **kwargs):
        return {"obsolete_nodes": [], "obsolete_edges": []}

    async def fake_delete_obsolete_nodes(*args, **kwargs):
        return 0

    async def fake_delete_obsolete_edges(*args, **kwargs):
        return 0

    monkeypatch.setattr(graph_extractor, "extract_incremental_graph_actions", fake_extract_incremental_graph_actions)
    monkeypatch.setattr(graph_extractor, "get_case_graph_node_records", fake_get_case_graph_node_records)
    monkeypatch.setattr(graph_extractor, "evaluate_graph_action_reference_guard_async", fake_reference_guard)
    monkeypatch.setattr(graph_extractor, "execute_graph_actions", fake_execute_graph_actions)
    monkeypatch.setattr(graph_extractor, "get_case_graph_node_names", fake_get_case_graph_node_names)
    monkeypatch.setattr(graph_extractor, "format_current_graph_state", fake_format_current_graph_state)
    monkeypatch.setattr(graph_extractor, "plan_graph_gc", fake_plan_graph_gc)
    monkeypatch.setattr(graph_extractor, "delete_obsolete_nodes", fake_delete_obsolete_nodes)
    monkeypatch.setattr(graph_extractor, "delete_obsolete_edges", fake_delete_obsolete_edges)

    result = asyncio.run(
        graph_extractor.refresh_case_graph(
            906,
            "我补充一下案情：我在A公司工作两年，月薪12000，公司一直没签劳动合同。",
            user_id=7,
        )
    )

    assert result["actions_applied"] == 2
    assert result["needs_clarification"] is True
    assert applied_actions == [
        {
            "action": "ADD",
            "source": "当事人",
            "source_category": "主体",
            "relation": "任职于",
            "target": "A公司",
            "target_category": "主体",
            "old_target": "",
        },
        {
            "action": "ADD",
            "source": "A公司",
            "source_category": "主体",
            "relation": "未签订",
            "target": "劳动合同",
            "target_category": "客体",
            "old_target": "",
        }
    ]


def test_reference_guard_applies_prior_message_resolutions_before_graph_write(monkeypatch) -> None:
    graph_extractor = load_graph_extractor_module()
    applied_actions: list[dict] = []

    async def fake_extract_incremental_graph_actions(user_input):
        return [
            {
                "action": "ADD",
                "source": "公司",
                "source_category": "主体",
                "relation": "拖欠工资",
                "target": "当事人",
                "target_category": "主体",
                "old_target": "",
            }
        ]

    async def fake_get_case_graph_node_records(conversation_id):
        return []

    async def fake_execute_graph_actions(conversation_id, actions, user_id=0):
        applied_actions.extend(actions)
        return {"conversation_id": conversation_id, "actions_applied": len(actions), "actions": actions}

    async def fake_get_case_graph_node_names(conversation_id):
        return []

    async def fake_format_current_graph_state(conversation_id):
        return "[]"

    async def fake_plan_graph_gc(*args, **kwargs):
        return {"obsolete_nodes": [], "obsolete_edges": []}

    async def fake_delete_obsolete_nodes(*args, **kwargs):
        return 0

    async def fake_delete_obsolete_edges(*args, **kwargs):
        return 0

    monkeypatch.setattr(graph_extractor, "extract_incremental_graph_actions", fake_extract_incremental_graph_actions)
    monkeypatch.setattr(graph_extractor, "get_case_graph_node_records", fake_get_case_graph_node_records)
    monkeypatch.setattr(graph_extractor, "execute_graph_actions", fake_execute_graph_actions)
    monkeypatch.setattr(graph_extractor, "get_case_graph_node_names", fake_get_case_graph_node_names)
    monkeypatch.setattr(graph_extractor, "format_current_graph_state", fake_format_current_graph_state)
    monkeypatch.setattr(graph_extractor, "plan_graph_gc", fake_plan_graph_gc)
    monkeypatch.setattr(graph_extractor, "delete_obsolete_nodes", fake_delete_obsolete_nodes)
    monkeypatch.setattr(graph_extractor, "delete_obsolete_edges", fake_delete_obsolete_edges)

    result = asyncio.run(
        graph_extractor.refresh_case_graph(
            905,
            "我想咨询一个劳动纠纷。我叫张三，在上海一家叫蓝海科技的公司做后端开发。公司从2023年5月开始拖欠我工资。",
            user_id=7,
            prior_reference_guard={
                "provider": "fake",
                "model": "semantic-test",
                "decision": "allow",
                "confidence": "high",
                "needs_clarification": False,
                "reference_resolutions": [
                    {"mention": "我", "resolved_label": "张三", "reference_type": "self", "confidence": "high"},
                    {"mention": "当事人", "resolved_label": "张三", "reference_type": "self", "confidence": "high"},
                    {"mention": "公司", "resolved_label": "蓝海科技", "reference_type": "organization", "confidence": "high"},
                ],
            },
        )
    )

    assert result["needs_clarification"] is False
    assert result["actions_applied"] == 1
    assert applied_actions[0]["source"] == "蓝海科技"
    assert applied_actions[0]["target"] == "张三"
    assert result["reference_guard"]["reason"] == "prior_reference_guard_resolutions_applied"


def test_reference_guard_blocks_ambiguous_self_before_graph_write(monkeypatch) -> None:
    graph_extractor = load_graph_extractor_module()

    async def fake_extract_incremental_graph_actions(user_input):
        return [
            {
                "action": "ADD",
                "source": "我",
                "source_category": "主体",
                "relation": "未签订",
                "target": "劳动合同",
                "target_category": "客体",
                "old_target": "",
            }
        ]

    async def fake_get_case_graph_node_records(conversation_id):
        return [
            {"name": "张三", "category": "主体"},
            {"name": "李四", "category": "主体"},
            {"name": "蓝海科技", "category": "主体"},
        ]

    async def fail_execute_graph_actions(*args, **kwargs):
        raise AssertionError("ambiguous reference must not write graph")

    monkeypatch.setattr(graph_extractor, "extract_incremental_graph_actions", fake_extract_incremental_graph_actions)
    monkeypatch.setattr(graph_extractor, "get_case_graph_node_records", fake_get_case_graph_node_records)
    monkeypatch.setattr(graph_extractor, "execute_graph_actions", fail_execute_graph_actions)

    async def fake_reference_guard(**kwargs):
        return _clarify_guard(
            mention="我",
            question="为避免把案卷图谱写错，这里的“我”指的是哪个主体？当前候选有：张三、李四、蓝海科技。请直接回复主体名称。",
            message=kwargs["user_input"],
            blocked_actions=kwargs["actions"],
        )

    monkeypatch.setattr(graph_extractor, "evaluate_graph_action_reference_guard_async", fake_reference_guard)

    result = asyncio.run(
        graph_extractor.refresh_case_graph(
            902,
            "我没有签正式劳动合同，但是每天都去公司打卡。",
            user_id=7,
        )
    )

    assert result["actions_applied"] == 0
    assert result["needs_clarification"] is True
    assert "这里的“我”" in result["clarification_questions"][0]
    assert "张三" in result["clarification_questions"][0]
    assert "李四" in result["clarification_questions"][0]
    assert result["pending_clarification"]["pending_graph_update_input_text"].startswith("我没有签正式劳动合同")


def test_reference_guard_blocks_ambiguous_company_before_graph_write(monkeypatch) -> None:
    graph_extractor = load_graph_extractor_module()

    async def fake_extract_incremental_graph_actions(user_input):
        return [
            {
                "action": "ADD",
                "source": "公司",
                "source_category": "主体",
                "relation": "未签订",
                "target": "劳动合同",
                "target_category": "客体",
                "old_target": "",
            }
        ]

    async def fake_get_case_graph_node_records(conversation_id):
        return [
            {"name": "张三", "category": "主体", "role": "current_party"},
            {"name": "蓝海科技", "category": "主体"},
            {"name": "外包公司", "category": "主体"},
        ]

    async def fail_execute_graph_actions(*args, **kwargs):
        raise AssertionError("ambiguous organization must not write graph")

    monkeypatch.setattr(graph_extractor, "extract_incremental_graph_actions", fake_extract_incremental_graph_actions)
    monkeypatch.setattr(graph_extractor, "get_case_graph_node_records", fake_get_case_graph_node_records)
    monkeypatch.setattr(graph_extractor, "execute_graph_actions", fail_execute_graph_actions)

    async def fake_reference_guard(**kwargs):
        return _clarify_guard(
            mention="公司",
            question="为避免把案卷图谱写错，这里的“公司”指的是哪个主体？当前候选有：张三、蓝海科技、外包公司。请直接回复主体名称。",
            message=kwargs["user_input"],
            blocked_actions=kwargs["actions"],
        )

    monkeypatch.setattr(graph_extractor, "evaluate_graph_action_reference_guard_async", fake_reference_guard)

    result = asyncio.run(
        graph_extractor.refresh_case_graph(903, "公司没有给我签劳动合同。", user_id=7)
    )

    assert result["needs_clarification"] is True
    assert result["actions_applied"] == 0
    assert "这里的“公司”" in result["clarification_questions"][0]
    assert "蓝海科技" in result["clarification_questions"][0]
    assert "外包公司" in result["clarification_questions"][0]


def test_reference_guard_blocks_ambiguous_third_person_before_graph_write(monkeypatch) -> None:
    graph_extractor = load_graph_extractor_module()

    async def fake_extract_incremental_graph_actions(user_input):
        return [
            {
                "action": "ADD",
                "source": "他",
                "source_category": "主体",
                "relation": "要求签署",
                "target": "确认书",
                "target_category": "客体",
                "old_target": "",
            }
        ]

    async def fake_get_case_graph_node_records(conversation_id):
        return [
            {"name": "张三", "category": "主体", "role": "current_party"},
            {"name": "王经理", "category": "主体"},
            {"name": "李四", "category": "主体"},
            {"name": "蓝海科技", "category": "主体"},
        ]

    async def fail_execute_graph_actions(*args, **kwargs):
        raise AssertionError("ambiguous third-person reference must not write graph")

    monkeypatch.setattr(graph_extractor, "extract_incremental_graph_actions", fake_extract_incremental_graph_actions)
    monkeypatch.setattr(graph_extractor, "get_case_graph_node_records", fake_get_case_graph_node_records)
    monkeypatch.setattr(graph_extractor, "execute_graph_actions", fail_execute_graph_actions)

    async def fake_reference_guard(**kwargs):
        return _clarify_guard(
            mention="他",
            question="为避免把案卷图谱写错，这里的“他”指的是哪个主体？当前候选有：张三、王经理、李四、蓝海科技。请直接回复主体名称。",
            message=kwargs["user_input"],
            blocked_actions=kwargs["actions"],
        )

    monkeypatch.setattr(graph_extractor, "evaluate_graph_action_reference_guard_async", fake_reference_guard)

    result = asyncio.run(
        graph_extractor.refresh_case_graph(904, "他让我签确认书。", user_id=7)
    )

    assert result["needs_clarification"] is True
    assert result["actions_applied"] == 0
    assert "这里的“他”" in result["clarification_questions"][0]
    assert "王经理" in result["clarification_questions"][0]
    assert "李四" in result["clarification_questions"][0]


def test_graph_prompts_require_preserving_explicit_entity_names() -> None:
    graph_extractor = load_graph_extractor_module()

    assert "明确出现的公司名、人名、机构名必须优先保留原名" in graph_extractor.GRAPH_EXTRACTION_PROMPT
    assert "主节点名应保留为“A公司”" in graph_extractor.INCREMENTAL_EXTRACTION_PROMPT
    assert "不能合并成一个“用人单位”" in graph_extractor.GRAPH_EXTRACTION_PROMPT
    assert "category 是会话图谱写入案卷图谱的类型契约" in graph_extractor.GRAPH_EXTRACTION_PROMPT
    assert "只要能承担责任，也必须标为“主体”" in graph_extractor.INCREMENTAL_EXTRACTION_PROMPT
    assert "微信、钉钉、飞书、短信、邮件、电话" in graph_extractor.BELIEF_REVISION_PROMPT_V2
    assert "软件或渠道本身绝不能标为主体" in graph_extractor.BELIEF_REVISION_PROMPT_V2
