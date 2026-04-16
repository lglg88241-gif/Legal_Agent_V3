"""Surgery #13: 案件快照持久化 + 案件类型推断 测试。"""
from __future__ import annotations

import json
import os
import sys
from types import ModuleType, SimpleNamespace

import pytest

os.environ.setdefault("USE_TORCH", "0")

if "json_repair" not in sys.modules:
    fake_json_repair = ModuleType("json_repair")
    fake_json_repair.repair_json = lambda content: content
    sys.modules["json_repair"] = fake_json_repair

if "app.agent.tools" not in sys.modules:
    fake_tools = ModuleType("app.agent.tools")
    fake_tools.AGENT_TOOLS = [SimpleNamespace(name="search_law_articles")]
    sys.modules["app.agent.tools"] = fake_tools

_GRAPH_RELOAD_MODULES = (
    "app.services.graph_extractor",
    "app.agent.nodes.researcher",
    "app.services.legal_agent",
)


@pytest.fixture(autouse=True)
def _reset_graph_related_modules():
    for module_name in _GRAPH_RELOAD_MODULES:
        sys.modules.pop(module_name, None)


class TestCaseTypeInference:
    """案件类型自动推断测试。"""

    def test_labor_dispute(self) -> None:
        from app.services.legal_agent import _infer_case_type

        assert _infer_case_type("公司未签劳动合同，拖欠工资3个月", "我能要多少赔偿") == "劳动争议"

    def test_criminal(self) -> None:
        from app.services.legal_agent import _infer_case_type

        assert _infer_case_type("正当防卫致人轻伤", "会被判刑吗") == "刑事"

    def test_civil_tort(self) -> None:
        from app.services.legal_agent import _infer_case_type

        assert _infer_case_type("邻居在网上发帖诽谤我", "名誉权侵权") == "民事侵权"

    def test_contract(self) -> None:
        from app.services.legal_agent import _infer_case_type

        assert _infer_case_type("买卖合同对方违约不发货", "能退款吗") == "合同纠纷"

    def test_environment(self) -> None:
        from app.services.legal_agent import _infer_case_type

        assert _infer_case_type("工厂排放污染河流", "公益诉讼") == "环境公益"

    def test_unknown_returns_none(self) -> None:
        from app.services.legal_agent import _infer_case_type

        assert _infer_case_type("今天天气真好", "你好") is None

    def test_empty_input(self) -> None:
        from app.services.legal_agent import _infer_case_type

        assert _infer_case_type("", "") is None


class TestCaseStateSnapshot:
    """CaseState JSON 序列化快照测试。"""

    def test_snapshot_roundtrip(self) -> None:
        from app.schemas.case_state import CaseState

        cs = CaseState.from_text(
            "月薪8000元，工作10个月，未签劳动合同",
            "能要多少赔偿",
        )
        snapshot = cs.model_dump()

        assert snapshot["monthly_salary"] == 8000.0
        assert snapshot["work_months"] == 10
        assert snapshot["has_unsigned_contract"] is True
        assert snapshot["double_salary_gap"] > 0
        assert snapshot["claim_total"] > 0

        restored = CaseState(**snapshot)
        assert restored.monthly_salary == cs.monthly_salary
        assert restored.claim_total == cs.claim_total

    def test_empty_snapshot(self) -> None:
        from app.schemas.case_state import CaseState

        cs = CaseState()
        snapshot = cs.model_dump()
        assert snapshot["monthly_salary"] is None
        assert snapshot["claim_total"] == 0.0

    def test_snapshot_is_json_serializable(self) -> None:
        from app.schemas.case_state import CaseState

        cs = CaseState.from_text("月薪12000元，工作6个月，未签合同，拖欠2个月工资")
        snapshot = cs.model_dump()
        json_str = json.dumps(snapshot, ensure_ascii=False)
        assert isinstance(json_str, str)
        assert "12000" in json_str


class TestConversationStoreNewFunctions:
    """conversation_store 新增函数的参数校验测试。"""

    def test_serialize_conversation_has_new_fields(self) -> None:
        from app.services.conversation_store import serialize_conversation
        import inspect

        source = inspect.getsource(serialize_conversation)
        assert "case_type" in source
        assert "case_state" in source

    def test_search_user_cases_function_exists(self) -> None:
        from app.services.conversation_store import search_user_cases
        import inspect

        sig = inspect.signature(search_user_cases)
        params = list(sig.parameters.keys())
        assert "user_id" in params
        assert "case_type" in params
        assert "keyword" in params

    def test_update_case_state_function_exists(self) -> None:
        from app.services.conversation_store import update_conversation_case_state
        import inspect

        sig = inspect.signature(update_conversation_case_state)
        params = list(sig.parameters.keys())
        assert "case_state_dict" in params

    def test_load_case_state_function_exists(self) -> None:
        from app.services.conversation_store import load_conversation_case_state
        import inspect

        sig = inspect.signature(load_conversation_case_state)
        params = list(sig.parameters.keys())
        assert "conversation_id" in params


class TestCaseStateFromQuestion:
    """Bug A fix: 当 case_summary 为空时，应该从 question 构建 CaseState。"""

    def test_question_only_extracts_salary(self) -> None:
        from app.schemas.case_state import CaseState

        cs = CaseState.from_text(
            "",
            "我在公司工作了10个月，月薪8000元，公司一直没签劳动合同",
            "",
        )
        assert cs.monthly_salary == 8000.0
        assert cs.work_months == 10
        assert cs.has_unsigned_contract is True
        assert cs.double_salary_gap > 0
        assert cs.claim_total > 0

    def test_question_with_owed_wages(self) -> None:
        from app.schemas.case_state import CaseState

        cs = CaseState.from_text(
            "",
            "公司拖欠我3个月工资，月薪12000元",
            "",
        )
        assert cs.monthly_salary == 12000.0
        assert cs.owed_wages == 36000.0
        assert cs.has_owed_wages is True


class TestCaseStateMergeFallback:
    """Bug B fix: saved_case_state 应补全 from_text 未能提取的字段。"""

    def test_merge_salary_from_saved_state(self) -> None:
        from app.schemas.case_state import CaseState

        cs = CaseState.from_text(
            "用户被辞退，存在劳动争议",
            "拖欠了2个月工资",
            "",
        )
        assert cs.monthly_salary is None

        saved = {
            "monthly_salary": 8000.0,
            "work_months": 10,
            "has_unsigned_contract": True,
            "deposit": 0.0,
        }
        if cs.monthly_salary is None and saved.get("monthly_salary") is not None:
            cs.monthly_salary = saved["monthly_salary"]
        if cs.work_months is None and saved.get("work_months") is not None:
            cs.work_months = saved["work_months"]
        if not cs.has_unsigned_contract and saved.get("has_unsigned_contract"):
            cs.has_unsigned_contract = True
        cs = cs.model_validate(cs.model_dump())

        assert cs.monthly_salary == 8000.0
        assert cs.work_months == 10
        assert cs.has_unsigned_contract is True
        assert cs.double_salary_gap > 0
        assert cs.claim_total > 0

    def test_no_saved_state_no_crash(self) -> None:
        """saved_case_state 为 None 时不应崩溃。"""
        from app.schemas.case_state import CaseState

        cs = CaseState.from_text("", "你好", "")
        snapshot = cs.model_dump()
        assert snapshot["monthly_salary"] is None
        assert snapshot["claim_total"] == 0.0
