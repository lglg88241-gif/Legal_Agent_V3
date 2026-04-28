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

    fake_langchain_messages.HumanMessage = FakeMessage
    fake_langchain_messages.SystemMessage = FakeMessage
    fake_langchain_core.messages = fake_langchain_messages
    sys.modules["langchain_core"] = fake_langchain_core
    sys.modules["langchain_core.messages"] = fake_langchain_messages


def load_graph_extractor_module():
    install_graph_import_stubs()
    sys.modules.pop("app.services.graph_extractor", None)
    return importlib.import_module("app.services.graph_extractor")


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


def test_graph_prompts_require_preserving_explicit_entity_names() -> None:
    graph_extractor = load_graph_extractor_module()

    assert "明确出现的公司名、人名、机构名必须优先保留原名" in graph_extractor.GRAPH_EXTRACTION_PROMPT
    assert "主节点名应保留为“A公司”" in graph_extractor.INCREMENTAL_EXTRACTION_PROMPT
    assert "不能合并成一个“用人单位”" in graph_extractor.GRAPH_EXTRACTION_PROMPT
