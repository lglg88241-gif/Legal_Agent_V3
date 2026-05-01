"""Test conversation entity state handling of self anchor + real name."""
import pytest
from app.services.conversation_entity_state import build_conversation_entity_state


def test_self_anchor_with_real_name_should_merge():
    """
    测试：当用户说"我叫张三，是劳动者"时，
    graph_extractor 可能输出两个节点："当事人"和"张三"，
    conversation_entity_state 应该将它们合并为一个实体。
    """
    # 模拟 graph_extractor 输出：两个节点 + 一条边
    raw_graph_payload = {
        "nodes": [
            {
                "id": "node_1",
                "label": "当事人",
                "type": "person",
                "properties": {
                    "role": "current_party",
                },
                "conversation_id": 123,
            },
            {
                "id": "node_2",
                "label": "张三",
                "type": "person",
                "properties": {},
                "conversation_id": 123,
            },
            {
                "id": "node_3",
                "label": "B公司",
                "type": "organization",
                "properties": {},
                "conversation_id": 123,
            },
        ],
        "edges": [
            {
                "source": "node_1",
                "target": "node_2",
                "label": "称为",
                "type": "identity",
                "conversation_id": 123,
            },
            {
                "source": "node_2",
                "target": "node_3",
                "label": "签订劳动合同",
                "type": "contract",
                "conversation_id": 123,
            },
        ],
    }

    message_text = "我叫张三，是这起劳动争议里的劳动者。我和B公司签了劳动合同，但B公司一直没有给我缴纳社保。"

    result = build_conversation_entity_state(
        conversation_id=123,
        message_text=message_text,
        raw_graph_payload=raw_graph_payload,
        conversation_mode="legal_question",
        persistence_consent=True,
    )

    entities = result.get("entities", [])

    # 期望：只有 2 个实体（张三作为 current_party，B公司作为 employer）
    print(f"\n实际实体数量: {len(entities)}")
    for entity in entities:
        print(f"  - {entity.get('canonical_label')} (role={entity.get('role')}, type={entity.get('entity_type')}, aliases={entity.get('aliases')}, mentions={len(entity.get('mentions', []))})")

    # 断言：不应该同时存在"当事人"和"张三"两个独立实体
    entity_labels = [e.get("canonical_label") for e in entities]

    # 期望："张三"是 canonical_label，"当事人"在 aliases 中
    assert "张三" in entity_labels, "应该有'张三'实体"
    assert "当事人" not in entity_labels, "不应该有独立的'当事人'实体"

    # 找到张三实体
    zhang_san = next((e for e in entities if e.get("canonical_label") == "张三"), None)
    assert zhang_san is not None
    assert zhang_san.get("role") == "current_party"
    assert "当事人" in zhang_san.get("aliases", []), "'当事人'应该在张三的 aliases 中"
    assert "劳动者" in zhang_san.get("aliases", []), "'劳动者'应该在张三的 aliases 中"

    # 检查是否有重复的 B公司
    b_company_entities = [e for e in entities if "公司" in e.get("canonical_label", "")]
    print(f"\nB公司相关实体数量: {len(b_company_entities)}")
    for e in b_company_entities:
        print(f"  - {e.get('canonical_label')}: mentions={e.get('mentions')}")


def test_self_anchor_only_no_real_name():
    """
    测试：当用户只说"我是劳动者"，没有提供真实姓名时，
    应该生成一个 clarification_task。
    """
    raw_graph_payload = {
        "nodes": [
            {
                "id": "node_1",
                "label": "当事人",
                "type": "person",
                "properties": {"role": "current_party"},
                "conversation_id": 123,
            },
            {
                "id": "node_2",
                "label": "B公司",
                "type": "organization",
                "properties": {},
                "conversation_id": 123,
            },
        ],
        "edges": [],
    }

    message_text = "我是劳动者，和B公司有劳动争议。"

    result = build_conversation_entity_state(
        conversation_id=123,
        message_text=message_text,
        raw_graph_payload=raw_graph_payload,
        conversation_mode="legal_question",
        persistence_consent=True,
    )

    entities = result.get("entities", [])
    clarification_tasks = result.get("clarification_tasks", [])

    print(f"\n实体数量: {len(entities)}")
    print(f"clarification_tasks: {clarification_tasks}")

    # 期望：没有 current_party 实体（因为只有自称，没有具体姓名）
    # 或者：有一个 clarification_task 要求用户提供姓名
    current_party_entities = [e for e in entities if e.get("role") == "current_party"]

    if not current_party_entities:
        # 应该有 clarification_task
        assert len(clarification_tasks) > 0, "应该有 clarification_task"
        assert any(
            task.get("type") == "conversation_current_party_name" for task in clarification_tasks
        ), "应该有 conversation_current_party_name 任务"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
