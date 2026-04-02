from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from json_repair import repair_json
from langchain_community.chat_models import ChatZhipuAI
from langchain_core.messages import HumanMessage, SystemMessage
from neo4j import GraphDatabase
from pydantic import BaseModel, Field, ValidationError

from app.core.config import settings

logger = logging.getLogger(__name__)

CATEGORY_SUBJECT = "主体"
CATEGORY_OBJECT = "客体"
CATEGORY_EVENT = "事件"
CATEGORY_CONSEQUENCE = "后果"
ALLOWED_CATEGORIES = {
    CATEGORY_SUBJECT,
    CATEGORY_OBJECT,
    CATEGORY_EVENT,
    CATEGORY_CONSEQUENCE,
}
ALLOWED_ACTIONS = {"ADD", "UPDATE", "DELETE"}


class GraphAction(BaseModel):
    action: Literal["ADD", "UPDATE", "DELETE"] = Field(
        description="必须是 ADD, UPDATE 或 DELETE"
    )
    source: Optional[str] = Field(None, description="主语实体，DELETE时可为空")
    relation: Optional[str] = Field(None, description="关系，DELETE时可为空")
    target: Optional[str] = Field(None, description="宾语实体，DELETE时可为空")
    target_name: Optional[str] = Field(None, description="仅DELETE动作需要，指明要删除的节点名称")
    source_category: Optional[str] = None
    target_category: Optional[str] = None
    old_target: Optional[str] = None
    new_target: Optional[str] = None


class GraphActionList(BaseModel):
    actions: list[GraphAction]


class ObsoleteNodeList(BaseModel):
    obsolete_nodes: list[str] = Field(default_factory=list)


INCREMENTAL_EXTRACTION_PROMPT = """
你是一个案情图谱增量抽取器。
这是用户最新的补充口供：{user_input}

请只提取这段新口供里新增的实体和关系，输出 ADD 动作列表。
要求：
1. 只输出本轮新增事实，不要重复改写旧事实。
2. 绝对不要输出 UPDATE 或 DELETE。
3. 实体要做命名对齐，例如“我/本人/用户”统一为“当事人”。
4. category 只能是：主体、客体、事件、后果。
5. 只能输出合法 JSON，不要解释。

输出格式：
{
  "actions": [
    {
      "action": "ADD",
      "source": "当事人",
      "source_category": "主体",
      "relation": "主张",
      "target": "赔偿",
      "target_category": "后果"
    }
  ]
}
""".strip()

SEMANTIC_GC_PROMPT = """
你是一个图谱对齐引擎。
这是当前案卷的唯一确凿事实：{case_summary}
这是图谱数据库中现有的节点集合：{current_nodes}

请交叉对比，找出图谱中那些已经与最新事实冲突、被推翻、或者根本不存在于摘要中的幽灵节点。
要求：
1. 只返回确实应该被剔除的节点名称。
2. 输出的节点名必须逐字复制自给定节点集合，禁止改写、扩写或杜撰。
3. 如果节点虽然暂时没有在摘要中展开，但仍与现有事实一致，则不要删除。
4. 只能输出合法 JSON，不要解释。

输出格式：
{"obsolete_nodes": ["旧公司名", "错误金额"]}

如果完全一致，返回：
{"obsolete_nodes": []}
""".strip()

GRAPH_EXTRACTION_PROMPT = """
你是一个法律案件动态图谱抽取器。
请从输入文本中提取已经明确出现的事实三元组，并做实体对齐。

规则：
1. 只提取已经明确表达的事实，禁止臆测。
2. 同一事物必须统一命名，例如“我/本人/用户”统一为“当事人”。
3. 每个实体都必须给出 category，且只能是：主体、客体、事件、后果。
4. 只返回 JSON 数组，不要输出任何解释。

返回格式：
[
  {
    "source": "实体A",
    "source_category": "主体",
    "relation": "关系",
    "target": "实体B",
    "target_category": "事件"
  }
]
""".strip()

BELIEF_REVISION_PROMPT = """
你是一个严谨的案情图谱管理员。
这是当前图谱状态：{current_graph}
这是用户的最新输入：{user_input}

请判断新输入是对案情的补充，还是对之前错误信息的修正/推翻。
请严格输出 JSON，动作类型只能是以下三种之一：
ADD
UPDATE
DELETE

要求：
1. 如果是新增事实，输出 ADD。
2. 如果是修正已有事实，输出 UPDATE，并明确 old_target 与 new_target。
3. 如果是推翻错误事实，输出 DELETE。
4. 所有实体都要做命名对齐，例如“我/本人/用户”统一为“当事人”。
5. 所有 category 只能是：主体、客体、事件、后果。
6. 只输出 JSON，不要输出解释性文字。

输出格式：
{
  "actions": [
    {
      "action": "ADD",
      "source": "当事人",
      "source_category": "主体",
      "relation": "主张",
      "target": "赔偿",
      "target_category": "后果"
    },
    {
      "action": "UPDATE",
      "source": "当事人",
      "source_category": "主体",
      "relation": "过马路状态",
      "old_target": "闯红灯",
      "new_target": "绿灯通行",
      "target_category": "事件"
    },
    {
      "action": "DELETE",
      "source": "当事人",
      "relation": "存在事实",
      "target": "错误事实"
    }
  ]
}
""".strip()

BELIEF_REVISION_PROMPT_V2 = """
你是一个图谱数据库的底层操作员。你的任务是根据用户的输入，输出对图谱的 CRUD 操作 JSON。
【当前已有节点】：{existing_nodes}
【当前已有关系】：{current_graph}
【用户最新输入】：{user_input}

⚠️ 极度危险警告：绝对不要把用户的“否认、要求删除、记错了、改成、不是A而是B”这类元指令当作实体或关系写入图谱。它们是执行指令，不是事实本身。遇到这类表达，你必须直接输出 DELETE 或 UPDATE。

【硬性规则】
1. 你必须先做冲突检测。只要新输入推翻、纠正、覆盖了旧事实，就绝对不能只输出 ADD。
2. 当输出 DELETE、UPDATE 时，若涉及已有节点，必须从 existing_nodes 中逐字复制节点名，禁止改写。
3. 所有实体都要做命名对齐，例如“我/本人/用户”统一为“当事人”，“对方车/对方车辆”统一为“肇事车辆”。
4. category 只能是：主体、客体、事件、后果。
5. 只能输出合法 JSON，不要输出解释文字。

【正确操作示例】
场景 1 - 纠错反转：
现有图谱包含：[当事人]-(砸伤)->[车间机器]。用户说：“我没在车间被砸，是路上被车撞了。”
输出：
{"actions": [{"action": "DELETE", "target_name": "车间机器"}, {"action": "DELETE", "target_name": "砸伤"}, {"action": "ADD", "source": "当事人", "source_category": "主体", "relation": "撞伤", "target": "肇事车辆", "target_category": "客体"}]}

场景 2 - 明确要求删除：
现有图谱包含：[机器]。用户说：“把跟机器相关的事情删掉。”
输出：
{"actions": [{"action": "DELETE", "target_name": "机器"}]}

【绝对禁止的错误做法】
将“要求删除”“否认”“记错了”这些指令写成关系，比如：
[当事人]-(要求删除)->[机器]
这是严重错误。

【输出格式】
{
  "actions": [
    {
      "action": "ADD",
      "source": "当事人",
      "source_category": "主体",
      "relation": "主张",
      "target": "赔偿",
      "target_category": "后果"
    },
    {
      "action": "UPDATE",
      "source": "当事人",
      "source_category": "主体",
      "relation": "过马路状态",
      "old_target": "闯红灯",
      "new_target": "绿灯通行",
      "target_category": "事件"
    },
    {
      "action": "DELETE",
      "target_name": "机器"
    }
  ]
}
""".strip()

FETCH_FACTS_QUERY = """
MATCH (source:DynamicCaseEntity {conversation_id: $conversation_id})-[rel:CASE_RELATION {conversation_id: $conversation_id}]->(target:DynamicCaseEntity {conversation_id: $conversation_id})
RETURN source.name AS source_name,
       source.category AS source_category,
       rel.relation AS relation,
       target.name AS target_name,
       target.category AS target_category
ORDER BY source_name, relation, target_name
LIMIT 500
"""

FETCH_NODE_NAMES_QUERY = """
MATCH (n:DynamicCaseEntity {conversation_id: $conversation_id})
RETURN n.name AS name
ORDER BY name
LIMIT 500
"""

DELETE_GRAPH_QUERY = """
MATCH (n:DynamicCaseEntity {conversation_id: $conversation_id})
DETACH DELETE n
"""

_graph_llm: ChatZhipuAI | None = None
_graph_lock = threading.Lock()

ENTITY_ALIAS_MAP = {
    "我": "当事人",
    "本人": "当事人",
    "自己": "当事人",
    "用户": "当事人",
    "我们": "当事人",
    "咱们": "当事人",
    "对方车": "肇事车辆",
    "对方车辆": "肇事车辆",
    "对面车辆": "肇事车辆",
    "对方的车": "肇事车辆",
    "肇事车": "肇事车辆",
    "公司": "用人单位",
    "单位": "用人单位",
    "用工单位": "用人单位",
}


def _get_graph_llm() -> ChatZhipuAI:
    global _graph_llm

    if _graph_llm is None:
        with _graph_lock:
            if _graph_llm is None:
                _graph_llm = ChatZhipuAI(
                    model="glm-4",
                    api_key=settings.ZHIPUAI_API_KEY,
                    temperature=0.1,
                )
    return _graph_llm


def _build_driver():
    return GraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
    )


def _canonicalize_entity(name: str) -> str:
    cleaned = " ".join((name or "").strip().split())
    if not cleaned:
        return ""
    return ENTITY_ALIAS_MAP.get(cleaned, cleaned)


def _normalize_category(value: Any, entity_name: str = "") -> str:
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned in ALLOWED_CATEGORIES:
            return cleaned

    name = entity_name.strip()
    if not name:
        return CATEGORY_OBJECT

    if any(token in name for token in ("赔偿", "补偿", "损失", "医疗费", "工伤待遇", "伤残", "后果", "责任")):
        return CATEGORY_CONSEQUENCE
    if any(token in name for token in ("事故", "争议", "仲裁", "解除", "辞退", "工伤认定", "索赔", "违约", "受伤")):
        return CATEGORY_EVENT
    if any(token in name for token in ("车辆", "合同", "工资", "社保", "岗位", "证据", "保险")):
        return CATEGORY_OBJECT
    return CATEGORY_SUBJECT


def _normalize_relation(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _safe_json_load(raw_content: str, fallback: Any) -> Any:
    try:
        return json.loads(repair_json(raw_content))
    except Exception as exc:
        logger.warning("Failed to parse graph JSON payload: %s", exc)
        return fallback


def _parse_graph_action_response(
    raw_content: str,
    existing_nodes: set[str] | None = None,
) -> list[dict[str, str]]:
    repaired_content = repair_json(raw_content)
    json.loads(repaired_content)
    parsed = GraphActionList.model_validate_json(repaired_content)
    return _normalize_actions(
        {
            "actions": [
                action.model_dump(exclude_none=True)
                for action in parsed.actions
            ]
        },
        existing_nodes,
    )


def _parse_obsolete_nodes_response(
    raw_content: str,
    existing_nodes: set[str] | None = None,
) -> list[str]:
    repaired_content = repair_json(raw_content)
    json.loads(repaired_content)
    parsed = ObsoleteNodeList.model_validate_json(repaired_content)

    normalized: list[str] = []
    seen: set[str] = set()
    for node_name in parsed.obsolete_nodes:
        resolved_name = _resolve_existing_node_name(node_name, existing_nodes)
        if not resolved_name or resolved_name in seen:
            continue
        seen.add(resolved_name)
        normalized.append(resolved_name)

    return normalized


def _normalize_triples(raw_payload: Any) -> list[dict[str, str]]:
    if isinstance(raw_payload, dict):
        candidates = raw_payload.get("triples") or raw_payload.get("relations") or raw_payload.get("data") or []
    elif isinstance(raw_payload, list):
        candidates = raw_payload
    else:
        candidates = []

    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    for item in candidates:
        if not isinstance(item, dict):
            continue

        source = _canonicalize_entity(item.get("source", ""))
        relation = _normalize_relation(item.get("relation", ""))
        target = _canonicalize_entity(item.get("target", ""))
        if not source or not relation or not target:
            continue

        key = (source, relation, target)
        if key in seen:
            continue
        seen.add(key)

        normalized.append(
            {
                "source": source,
                "source_category": _normalize_category(item.get("source_category"), source),
                "relation": relation,
                "target": target,
                "target_category": _normalize_category(item.get("target_category"), target),
            }
        )

    return normalized


def _resolve_existing_node_name(name: Any, existing_nodes: set[str] | None = None) -> str:
    cleaned = " ".join(str(name or "").strip().split())
    if not cleaned:
        return ""
    if existing_nodes and cleaned in existing_nodes:
        return cleaned

    canonical = _canonicalize_entity(cleaned)
    if existing_nodes and canonical in existing_nodes:
        return canonical
    return canonical or cleaned


def _normalize_actions(raw_payload: Any, existing_nodes: set[str] | None = None) -> list[dict[str, str]]:
    if isinstance(raw_payload, dict):
        candidates = raw_payload.get("actions", [])
    elif isinstance(raw_payload, list):
        candidates = raw_payload
    else:
        candidates = []

    normalized: list[dict[str, str]] = []

    for item in candidates:
        if not isinstance(item, dict):
            continue

        action = str(item.get("action", "")).strip().upper()
        if action not in ALLOWED_ACTIONS:
            continue

        source = _resolve_existing_node_name(item.get("source", ""), existing_nodes)
        relation = _normalize_relation(item.get("relation", ""))
        target = _resolve_existing_node_name(item.get("new_target", "") or item.get("target", ""), existing_nodes)
        old_target = _resolve_existing_node_name(item.get("old_target", "") or item.get("target", ""), existing_nodes)
        target_name = _resolve_existing_node_name(
            item.get("target_name", "") or item.get("target", "") or item.get("old_target", ""),
            existing_nodes,
        )

        if action == "ADD" and (not source or not relation or not target):
            continue
        if action == "UPDATE" and (not source or not relation or not old_target or not target):
            continue
        if action == "DELETE" and not (target_name or (source and relation and old_target)):
            continue

        normalized.append(
            {
                "action": action,
                "source": source,
                "source_category": _normalize_category(item.get("source_category"), source),
                "relation": relation,
                "target": target if action != "DELETE" else target_name,
                "target_category": _normalize_category(item.get("target_category"), target),
                "old_target": old_target,
                "target_name": target_name,
            }
        )

    return normalized


def _cleanup_orphans(session, conversation_id: int) -> None:
    session.run(
        """
        MATCH (n)
        WHERE n.conversation_id = $conversation_id AND NOT (n)--()
        DELETE n
        """,
        {"conversation_id": conversation_id},
    ).consume()


def _merge_fact(session, conversation_id: int, fact: dict[str, str]) -> None:
    session.run(
        """
        MERGE (source:DynamicCaseEntity {conversation_id: $conversation_id, name: $source})
        ON CREATE SET source.created_at = datetime()
        SET source.updated_at = datetime(),
            source.category = $source_category,
            source.node_type = $source_category
        MERGE (target:DynamicCaseEntity {conversation_id: $conversation_id, name: $target})
        ON CREATE SET target.created_at = datetime()
        SET target.updated_at = datetime(),
            target.category = $target_category,
            target.node_type = $target_category
        MERGE (source)-[rel:CASE_RELATION {
            conversation_id: $conversation_id,
            relation: $relation,
            source_name: $source,
            target_name: $target
        }]->(target)
        ON CREATE SET rel.created_at = datetime()
        SET rel.updated_at = datetime()
        """,
        {
            "conversation_id": conversation_id,
            "source": fact["source"],
            "source_category": fact["source_category"],
            "relation": fact["relation"],
            "target": fact["target"],
            "target_category": fact["target_category"],
        },
    ).consume()


def _delete_fact(session, conversation_id: int, source: str, relation: str, target: str) -> None:
    session.run(
        """
        MATCH (:DynamicCaseEntity {conversation_id: $conversation_id, name: $source})-[rel:CASE_RELATION {
            conversation_id: $conversation_id,
            relation: $relation,
            source_name: $source,
            target_name: $target
        }]->(:DynamicCaseEntity {conversation_id: $conversation_id, name: $target})
        DELETE rel
        """,
        {
            "conversation_id": conversation_id,
            "source": source,
            "relation": relation,
            "target": target,
        },
    ).consume()


def _delete_node(session, conversation_id: int, target_name: str) -> None:
    session.run(
        """
        MATCH (n:DynamicCaseEntity)
        WHERE n.conversation_id = $conversation_id
          AND (n.name = $target_name OR n.name CONTAINS $target_name)
        DETACH DELETE n
        """,
        {
            "conversation_id": conversation_id,
            "target_name": target_name,
        },
    ).consume()

    session.run(
        """
        MATCH ()-[rel:CASE_RELATION {conversation_id: $conversation_id}]->()
        WHERE rel.relation = $target_name OR rel.relation CONTAINS $target_name
        DELETE rel
        """,
        {
            "conversation_id": conversation_id,
            "target_name": target_name,
        },
    ).consume()


def _execute_graph_actions_sync(conversation_id: int, actions: list[dict[str, str]]) -> dict[str, Any]:
    driver = _build_driver()
    applied = 0

    try:
        with driver.session() as session:
            for action in actions:
                action_type = action["action"]
                if action_type == "ADD":
                    _merge_fact(
                        session,
                        conversation_id,
                        {
                            "source": action["source"],
                            "source_category": action["source_category"],
                            "relation": action["relation"],
                            "target": action["target"],
                            "target_category": action["target_category"],
                        },
                    )
                    applied += 1
                elif action_type == "UPDATE":
                    _delete_fact(
                        session,
                        conversation_id,
                        action["source"],
                        action["relation"],
                        action["old_target"],
                    )
                    _merge_fact(
                        session,
                        conversation_id,
                        {
                            "source": action["source"],
                            "source_category": action["source_category"],
                            "relation": action["relation"],
                            "target": action["target"],
                            "target_category": action["target_category"],
                        },
                    )
                    applied += 1
                elif action_type == "DELETE":
                    if action["source"] and action["relation"]:
                        _delete_fact(
                            session,
                            conversation_id,
                            action["source"],
                            action["relation"],
                            action["old_target"] or action["target"],
                        )
                    else:
                        _delete_node(
                            session,
                            conversation_id,
                            action.get("target_name") or action["target"] or action["old_target"],
                        )
                    applied += 1

            _cleanup_orphans(session, conversation_id)
    finally:
        driver.close()

    return {
        "conversation_id": conversation_id,
        "actions_applied": applied,
        "actions": actions,
    }


def _fetch_case_graph_facts_sync(conversation_id: int) -> list[dict[str, str]]:
    driver = _build_driver()
    try:
        with driver.session() as session:
            records = session.run(FETCH_FACTS_QUERY, {"conversation_id": conversation_id}).data()
    finally:
        driver.close()

    facts: list[dict[str, str]] = []
    for record in records:
        source = str(record.get("source_name") or "").strip()
        relation = str(record.get("relation") or "").strip()
        target = str(record.get("target_name") or "").strip()
        if not source or not relation or not target:
            continue
        facts.append(
            {
                "source": source,
                "source_category": _normalize_category(record.get("source_category"), source),
                "relation": relation,
                "target": target,
                "target_category": _normalize_category(record.get("target_category"), target),
            }
        )
    return facts


def _fetch_case_graph_node_names_sync(conversation_id: int) -> list[str]:
    driver = _build_driver()
    try:
        with driver.session() as session:
            records = session.run(FETCH_NODE_NAMES_QUERY, {"conversation_id": conversation_id}).data()
    finally:
        driver.close()

    names: list[str] = []
    seen: set[str] = set()
    for record in records:
        name = _canonicalize_entity(str(record.get("name") or "").strip())
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def _delete_obsolete_nodes_sync(conversation_id: int, obsolete_nodes: list[str]) -> int:
    if not obsolete_nodes:
        return 0

    driver = _build_driver()
    try:
        with driver.session() as session:
            session.run(
                """
                UNWIND $obsolete_nodes AS target_name
                MATCH (n)
                WHERE n.conversation_id = $conversation_id AND n.name = target_name
                DETACH DELETE n
                """,
                {
                    "conversation_id": conversation_id,
                    "obsolete_nodes": obsolete_nodes,
                },
            ).consume()
    finally:
        driver.close()

    return len(obsolete_nodes)


async def extract_case_triples(text: str) -> list[dict[str, str]]:
    clean_text = (text or "").strip()
    if not clean_text:
        return []

    try:
        response = await _get_graph_llm().ainvoke(
            [
                SystemMessage(content=GRAPH_EXTRACTION_PROMPT),
                HumanMessage(content=f"输入文本：\n{clean_text}"),
            ]
        )
        content = response.content if isinstance(response.content, str) else str(response.content)
        return _normalize_triples(_safe_json_load(content, []))
    except Exception as exc:
        logger.warning("Graph extraction failed: %s", exc)
        return []


async def get_case_graph_facts(conversation_id: int) -> list[dict[str, str]]:
    try:
        return await asyncio.to_thread(_fetch_case_graph_facts_sync, conversation_id)
    except Exception as exc:
        logger.warning("Failed to load case graph facts for conversation %s: %s", conversation_id, exc)
        return []


async def get_case_graph_node_names(conversation_id: int) -> list[str]:
    try:
        return await asyncio.to_thread(_fetch_case_graph_node_names_sync, conversation_id)
    except Exception as exc:
        logger.warning("Failed to load case graph node names for conversation %s: %s", conversation_id, exc)
        return []


async def delete_obsolete_nodes(conversation_id: int, obsolete_nodes: list[str]) -> int:
    if not obsolete_nodes:
        return 0

    try:
        return await asyncio.to_thread(_delete_obsolete_nodes_sync, conversation_id, obsolete_nodes)
    except Exception as exc:
        logger.warning("Failed to delete obsolete graph nodes for conversation %s: %s", conversation_id, exc)
        return 0


async def format_current_graph_state(conversation_id: int) -> str:
    facts = await get_case_graph_facts(conversation_id)
    return json.dumps(facts, ensure_ascii=False)


async def _plan_graph_actions_legacy(current_graph: str, user_input: str) -> list[dict[str, str]]:
    clean_input = (user_input or "").strip()
    if not clean_input:
        return []

    try:
        response = await _get_graph_llm().ainvoke(
            [
                SystemMessage(content="你必须只输出合法 JSON。"),
                HumanMessage(
                    content=BELIEF_REVISION_PROMPT.format(
                        current_graph=current_graph or "[]",
                        user_input=clean_input,
                    )
                ),
            ]
        )
        content = response.content if isinstance(response.content, str) else str(response.content)
        return _normalize_actions(_safe_json_load(content, {"actions": []}))
    except Exception as exc:
        logger.warning("Belief revision planning failed: %s", exc)
        return []


async def plan_graph_actions(current_graph: str, user_input: str) -> list[dict[str, str]]:
    clean_input = (user_input or "").strip()
    if not clean_input:
        return []

    try:
        current_graph_payload = _safe_json_load(current_graph or "[]", [])
        existing_node_names = sorted(
            {
                str(fact.get("source", "")).strip()
                for fact in current_graph_payload
                if isinstance(fact, dict) and str(fact.get("source", "")).strip()
            }
            | {
                str(fact.get("target", "")).strip()
                for fact in current_graph_payload
                if isinstance(fact, dict) and str(fact.get("target", "")).strip()
            }
        )
        max_retries = 3
        feedback = ""
        existing_nodes_set = set(existing_node_names)

        for attempt in range(1, max_retries + 1):
            prompt = BELIEF_REVISION_PROMPT_V2.format(
                existing_nodes=json.dumps(existing_node_names, ensure_ascii=False),
                current_graph=current_graph or "[]",
                user_input=clean_input,
            )
            if feedback:
                prompt = (
                    f"{prompt}\n\n"
                    f"你刚才输出的 JSON 无法解析，报了以下错误：{feedback}。"
                    "请仔细检查格式和字段规范，并重新输出合法的 JSON。"
                )

            response = await _get_graph_llm().ainvoke(
                [
                    SystemMessage(content="你必须只输出合法 JSON。"),
                    HumanMessage(content=prompt),
                ]
            )
            content = response.content if isinstance(response.content, str) else str(response.content)

            try:
                return _parse_graph_action_response(content, existing_nodes_set)
            except (ValidationError, json.JSONDecodeError) as exc:
                feedback = str(exc)
                logger.warning(
                    "Belief revision response validation failed on attempt %s/%s: %s",
                    attempt,
                    max_retries,
                    exc,
                )

        logger.warning(
            "Belief revision planning failed after %s retries for input: %s",
            max_retries,
            clean_input,
        )
        return []
    except Exception as exc:
        logger.warning("Belief revision planning failed: %s", exc)
        return []


async def extract_incremental_graph_actions(user_input: str) -> list[dict[str, str]]:
    clean_input = (user_input or "").strip()
    if not clean_input:
        return []

    max_retries = 3
    feedback = ""

    for attempt in range(1, max_retries + 1):
        prompt = INCREMENTAL_EXTRACTION_PROMPT.format(user_input=clean_input)
        if feedback:
            prompt = (
                f"{prompt}\n\n"
                f"你刚才输出的 JSON 无法解析，报了以下错误：{feedback}。"
                "请仔细检查格式和字段规范，并重新输出只包含 ADD 动作的合法 JSON。"
            )

        try:
            response = await _get_graph_llm().ainvoke(
                [
                    SystemMessage(content="你必须只输出合法 JSON。"),
                    HumanMessage(content=prompt),
                ]
            )
            content = response.content if isinstance(response.content, str) else str(response.content)
            actions = _parse_graph_action_response(content)
            add_actions = [action for action in actions if action.get("action") == "ADD"]
            if add_actions:
                return add_actions
            if actions:
                logger.warning("Incremental extraction returned non-ADD actions; dropping them")
            return []
        except (ValidationError, json.JSONDecodeError) as exc:
            feedback = str(exc)
            logger.warning(
                "Incremental graph extraction validation failed on attempt %s/%s: %s",
                attempt,
                max_retries,
                exc,
            )
        except Exception as exc:
            logger.warning("Incremental graph extraction failed on attempt %s/%s: %s", attempt, max_retries, exc)
            return []

    logger.warning("Incremental graph extraction exhausted retries for input: %s", clean_input)
    return []


async def plan_obsolete_nodes(case_summary: str, current_nodes: list[str]) -> list[str]:
    summary_text = (case_summary or "").strip()
    if not summary_text or not current_nodes:
        return []

    max_retries = 3
    feedback = ""
    existing_nodes = set(current_nodes)

    for attempt in range(1, max_retries + 1):
        prompt = SEMANTIC_GC_PROMPT.format(
            case_summary=summary_text,
            current_nodes=json.dumps(current_nodes, ensure_ascii=False),
        )
        if feedback:
            prompt = (
                f"{prompt}\n\n"
                f"你刚才输出的 JSON 无法解析，报了以下错误：{feedback}。"
                "请仔细检查格式，并重新输出合法的 obsolete_nodes JSON。"
            )

        try:
            response = await _get_graph_llm().ainvoke(
                [
                    SystemMessage(content="你必须只输出合法 JSON。"),
                    HumanMessage(content=prompt),
                ]
            )
            content = response.content if isinstance(response.content, str) else str(response.content)
            return _parse_obsolete_nodes_response(content, existing_nodes)
        except (ValidationError, json.JSONDecodeError) as exc:
            feedback = str(exc)
            logger.warning(
                "Semantic GC validation failed on attempt %s/%s: %s",
                attempt,
                max_retries,
                exc,
            )
        except Exception as exc:
            logger.warning("Semantic GC planning failed on attempt %s/%s: %s", attempt, max_retries, exc)
            return []

    logger.warning("Semantic GC exhausted retries for summary: %s", summary_text)
    return []


async def execute_graph_actions(conversation_id: int, actions: list[dict[str, str]]) -> dict[str, Any]:
    if not actions:
        return {
            "conversation_id": conversation_id,
            "actions_applied": 0,
            "actions": [],
        }

    try:
        return await asyncio.to_thread(_execute_graph_actions_sync, conversation_id, actions)
    except Exception as exc:
        logger.warning("Graph action execution failed for conversation %s: %s", conversation_id, exc)
        return {
            "conversation_id": conversation_id,
            "actions_applied": 0,
            "actions": actions,
            "error": str(exc),
        }


async def upsert_case_graph(conversation_id: int, triples: list[dict[str, str]]) -> int:
    add_actions = [
        {
            "action": "ADD",
            "source": triple["source"],
            "source_category": triple["source_category"],
            "relation": triple["relation"],
            "target": triple["target"],
            "target_category": triple["target_category"],
            "old_target": "",
        }
        for triple in triples
    ]
    result = await execute_graph_actions(conversation_id, add_actions)
    return int(result.get("actions_applied", 0))


async def sync_case_graph_from_history(conversation_id: int, chat_history: str) -> int:
    triples = await extract_case_triples(chat_history)
    if not triples:
        return 0
    return await upsert_case_graph(conversation_id, triples)


async def refresh_case_graph(conversation_id: int, user_input: str, case_summary: str = "") -> dict[str, Any]:
    add_actions = await extract_incremental_graph_actions(user_input)

    if not add_actions:
        triples = await extract_case_triples(user_input)
        add_actions = [
            {
                "action": "ADD",
                "source": triple["source"],
                "source_category": triple["source_category"],
                "relation": triple["relation"],
                "target": triple["target"],
                "target_category": triple["target_category"],
                "old_target": "",
            }
            for triple in triples
        ]

    add_result = await execute_graph_actions(conversation_id, add_actions)
    current_nodes = await get_case_graph_node_names(conversation_id)
    obsolete_nodes = await plan_obsolete_nodes(case_summary, current_nodes)
    deleted_count = await delete_obsolete_nodes(conversation_id, obsolete_nodes)

    return {
        "conversation_id": conversation_id,
        "actions_applied": int(add_result.get("actions_applied", 0)) + deleted_count,
        "actions": add_result.get("actions", []),
        "added_actions": add_result.get("actions", []),
        "added_count": int(add_result.get("actions_applied", 0)),
        "obsolete_nodes": obsolete_nodes,
        "deleted_count": deleted_count,
    }


async def get_case_graph(conversation_id: int) -> dict[str, Any]:
    facts = await get_case_graph_facts(conversation_id)
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    for index, fact in enumerate(facts):
        source_name = fact["source"]
        target_name = fact["target"]
        source_category = fact["source_category"]
        target_category = fact["target_category"]
        relation = fact["relation"]
        source_id = f"{conversation_id}:node:{source_name}"
        target_id = f"{conversation_id}:node:{target_name}"

        if source_id not in nodes:
            nodes[source_id] = {
                "id": source_id,
                "label": source_name,
                "type": source_category,
                "category": source_category,
                "labels": ["DynamicCaseEntity", source_category],
                "properties": {
                    "conversation_id": conversation_id,
                    "name": source_name,
                    "category": source_category,
                },
            }

        if target_id not in nodes:
            nodes[target_id] = {
                "id": target_id,
                "label": target_name,
                "type": target_category,
                "category": target_category,
                "labels": ["DynamicCaseEntity", target_category],
                "properties": {
                    "conversation_id": conversation_id,
                    "name": target_name,
                    "category": target_category,
                },
            }

        edges.append(
            {
                "id": f"{conversation_id}:{index}:{relation}",
                "source": source_id,
                "target": target_id,
                "label": relation,
                "properties": {
                    "conversation_id": conversation_id,
                    "relation": relation,
                },
            }
        )

    return {
        "query": f"会话 {conversation_id} 案情图谱",
        "nodes": list(nodes.values()),
        "edges": edges,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def format_graph_memory(conversation_id: int, limit: int = 20) -> str:
    facts = await get_case_graph_facts(conversation_id)
    lines = [
        f"{fact['source']}（{fact['source_category']}）-{fact['relation']}-{fact['target']}（{fact['target_category']}）"
        for fact in facts[:limit]
    ]
    return "\n".join(lines)


def _delete_case_graph_sync(conversation_id: int) -> None:
    driver = _build_driver()
    try:
        with driver.session() as session:
            session.run(DELETE_GRAPH_QUERY, {"conversation_id": conversation_id}).consume()
    finally:
        driver.close()


async def delete_case_graph(conversation_id: int) -> None:
    try:
        await asyncio.to_thread(_delete_case_graph_sync, conversation_id)
    except Exception as exc:
        logger.warning("Failed to delete case graph for conversation %s: %s", conversation_id, exc)
