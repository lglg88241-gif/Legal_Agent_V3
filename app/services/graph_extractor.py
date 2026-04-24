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
from app.agent.prompts.extractor import (  # noqa: E402
    GRAPH_EXTRACTION_PROMPT,
    INCREMENTAL_EXTRACTION_PROMPT,
    SEMANTIC_GC_PROMPT,
    BELIEF_REVISION_PROMPT,
    BELIEF_REVISION_PROMPT_V2,
)

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


class GraphTriple(BaseModel):
    source: str
    relation: str
    target: str


class GraphGCOutput(BaseModel):
    obsolete_nodes: list[str] = Field(default_factory=list)
    obsolete_edges: list[GraphTriple] = Field(default_factory=list)


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

FETCH_USER_CASES_QUERY = """
MATCH (n:DynamicCaseEntity {user_id: $user_id})
WITH DISTINCT n.conversation_id AS case_id
MATCH (a:DynamicCaseEntity {user_id: $user_id, conversation_id: case_id})-[r:CASE_RELATION]-(b:DynamicCaseEntity {user_id: $user_id, conversation_id: case_id})
RETURN case_id,
       collect(DISTINCT a.name) AS source_names,
       collect(DISTINCT b.name) AS target_names,
       count(DISTINCT r) AS relation_count
ORDER BY case_id DESC
LIMIT 20
"""

FETCH_CROSS_CASE_ENTITIES_QUERY = """
MATCH (n:DynamicCaseEntity {user_id: $user_id})
WHERE n.name IS NOT NULL
WITH n.name AS shared_entity,
     n.category AS entity_category,
     collect(DISTINCT n.conversation_id) AS cases
WHERE size(cases) > 1
RETURN shared_entity, entity_category, cases
ORDER BY size(cases) DESC
LIMIT 20
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


def _parse_graph_gc_response(
    raw_content: str,
    existing_nodes: set[str] | None = None,
) -> dict[str, list[Any]]:
    repaired_content = repair_json(raw_content)
    json.loads(repaired_content)
    parsed = GraphGCOutput.model_validate_json(repaired_content)

    normalized_nodes: list[str] = []
    seen_nodes: set[str] = set()
    for node_name in parsed.obsolete_nodes:
        resolved_name = _resolve_existing_node_name(node_name, existing_nodes)
        if not resolved_name or resolved_name in seen_nodes:
            continue
        seen_nodes.add(resolved_name)
        normalized_nodes.append(resolved_name)

    normalized_edges: list[dict[str, str]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for edge in parsed.obsolete_edges:
        source = _resolve_existing_node_name(edge.source, existing_nodes)
        relation = _normalize_relation(edge.relation)
        target = _resolve_existing_node_name(edge.target, existing_nodes)
        if not source or not relation or not target:
            continue
        key = (source, relation, target)
        if key in seen_edges:
            continue
        seen_edges.add(key)
        normalized_edges.append(
            {
                "source": source,
                "relation": relation,
                "target": target,
            }
        )

    return {
        "obsolete_nodes": normalized_nodes,
        "obsolete_edges": normalized_edges,
    }


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


def _merge_fact(session, conversation_id: int, fact: dict[str, str], user_id: int = 0) -> None:
    session.run(
        """
        MERGE (source:DynamicCaseEntity {conversation_id: $conversation_id, name: $source})
        ON CREATE SET source.created_at = datetime(),
                      source.user_id = $user_id
        SET source.updated_at = datetime(),
            source.category = $source_category,
            source.node_type = $source_category,
            source.user_id = $user_id
        MERGE (target:DynamicCaseEntity {conversation_id: $conversation_id, name: $target})
        ON CREATE SET target.created_at = datetime(),
                      target.user_id = $user_id
        SET target.updated_at = datetime(),
            target.category = $target_category,
            target.node_type = $target_category,
            target.user_id = $user_id
        MERGE (source)-[rel:CASE_RELATION {
            conversation_id: $conversation_id,
            relation: $relation,
            source_name: $source,
            target_name: $target
        }]->(target)
        ON CREATE SET rel.created_at = datetime()
        SET rel.updated_at = datetime(),
            rel.user_id = $user_id
        """,
        {
            "conversation_id": conversation_id,
            "user_id": user_id,
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
          AND n.name = $target_name
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
        WHERE rel.relation = $target_name
        DELETE rel
        """,
        {
            "conversation_id": conversation_id,
            "target_name": target_name,
        },
    ).consume()


def _execute_graph_actions_sync(
    conversation_id: int,
    actions: list[dict[str, str]],
    user_id: int = 0,
) -> dict[str, Any]:
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
                        user_id=user_id,
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
                        user_id=user_id,
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


def _fetch_user_cases_sync(user_id: int) -> list[dict[str, Any]]:
    """获取某用户所有案件的图谱概览。"""
    driver = _build_driver()
    try:
        with driver.session() as session:
            records = session.run(FETCH_USER_CASES_QUERY, {"user_id": user_id}).data()
    finally:
        driver.close()
    return records


def _fetch_cross_case_entities_sync(user_id: int) -> list[dict[str, Any]]:
    """发现跨案件共享实体。"""
    driver = _build_driver()
    try:
        with driver.session() as session:
            records = session.run(FETCH_CROSS_CASE_ENTITIES_QUERY, {"user_id": user_id}).data()
    finally:
        driver.close()
    return records


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


def _delete_obsolete_edges_sync(conversation_id: int, obsolete_edges: list[dict[str, str]]) -> int:
    if not obsolete_edges:
        return 0

    driver = _build_driver()
    deleted_count = 0
    try:
        with driver.session() as session:
            for edge in obsolete_edges:
                source = edge.get("source", "").strip()
                relation = edge.get("relation", "").strip()
                target = edge.get("target", "").strip()
                if not source or not relation or not target:
                    continue
                _delete_fact(session, conversation_id, source, relation, target)
                deleted_count += 1
            _cleanup_orphans(session, conversation_id)
    finally:
        driver.close()

    return deleted_count


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


async def get_user_cases_graph_overview(user_id: int) -> list[dict[str, Any]]:
    """获取用户所有案件的图谱概览（异步）。"""
    if not user_id:
        return []
    try:
        return await asyncio.to_thread(_fetch_user_cases_sync, user_id)
    except Exception as exc:
        logger.warning("Failed to load user cases graph for user %s: %s", user_id, exc)
        return []


async def get_cross_case_entities(user_id: int) -> list[dict[str, Any]]:
    """发现用户跨案件的共享实体（异步）。"""
    if not user_id:
        return []
    try:
        return await asyncio.to_thread(_fetch_cross_case_entities_sync, user_id)
    except Exception as exc:
        logger.warning("Failed to find cross-case entities for user %s: %s", user_id, exc)
        return []


async def delete_obsolete_nodes(conversation_id: int, obsolete_nodes: list[str]) -> int:
    if not obsolete_nodes:
        return 0

    try:
        return await asyncio.to_thread(_delete_obsolete_nodes_sync, conversation_id, obsolete_nodes)
    except Exception as exc:
        logger.warning("Failed to delete obsolete graph nodes for conversation %s: %s", conversation_id, exc)
        return 0


async def delete_obsolete_edges(conversation_id: int, obsolete_edges: list[dict[str, str]]) -> int:
    if not obsolete_edges:
        return 0

    try:
        return await asyncio.to_thread(_delete_obsolete_edges_sync, conversation_id, obsolete_edges)
    except Exception as exc:
        logger.warning("Failed to delete obsolete graph edges for conversation %s: %s", conversation_id, exc)
        return 0


async def format_current_graph_state(conversation_id: int) -> str:
    facts = await get_case_graph_facts(conversation_id)
    return json.dumps(facts, ensure_ascii=False)


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


async def plan_graph_gc(case_summary: str, current_nodes: list[str], current_graph: str) -> dict[str, list[Any]]:
    summary_text = (case_summary or "").strip()
    if not summary_text or not current_nodes:
        return {"obsolete_nodes": [], "obsolete_edges": []}

    max_retries = 3
    feedback = ""
    existing_nodes = set(current_nodes)

    for attempt in range(1, max_retries + 1):
        prompt = SEMANTIC_GC_PROMPT.format(
            case_summary=summary_text,
            current_nodes=json.dumps(current_nodes, ensure_ascii=False),
            current_graph=current_graph or "[]",
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
            return _parse_graph_gc_response(content, existing_nodes)
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
            return {"obsolete_nodes": [], "obsolete_edges": []}

    logger.warning("Semantic GC exhausted retries for summary: %s", summary_text)
    return {"obsolete_nodes": [], "obsolete_edges": []}


async def execute_graph_actions(
    conversation_id: int,
    actions: list[dict[str, str]],
    user_id: int = 0,
) -> dict[str, Any]:
    if not actions:
        return {
            "conversation_id": conversation_id,
            "actions_applied": 0,
            "actions": [],
        }

    try:
        return await asyncio.to_thread(_execute_graph_actions_sync, conversation_id, actions, user_id)
    except Exception as exc:
        logger.warning("Graph action execution failed for conversation %s: %s", conversation_id, exc)
        return {
            "conversation_id": conversation_id,
            "actions_applied": 0,
            "actions": actions,
            "error": str(exc),
        }


async def upsert_case_graph(conversation_id: int, triples: list[dict[str, str]], user_id: int = 0) -> int:
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
    result = await execute_graph_actions(conversation_id, add_actions, user_id=user_id)
    return int(result.get("actions_applied", 0))


async def sync_case_graph_from_history(conversation_id: int, chat_history: str, user_id: int = 0) -> int:
    triples = await extract_case_triples(chat_history)
    if not triples:
        return 0
    return await upsert_case_graph(conversation_id, triples, user_id=user_id)


async def refresh_case_graph(
    conversation_id: int,
    user_input: str,
    case_summary: str = "",
    user_id: int = 0,
) -> dict[str, Any]:
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

    add_result = await execute_graph_actions(conversation_id, add_actions, user_id=user_id)
    current_nodes = await get_case_graph_node_names(conversation_id)
    current_graph = await format_current_graph_state(conversation_id)
    gc_result = await plan_graph_gc(case_summary, current_nodes, current_graph)
    obsolete_nodes = gc_result.get("obsolete_nodes", [])
    obsolete_edges = gc_result.get("obsolete_edges", [])
    deleted_count = await delete_obsolete_nodes(conversation_id, obsolete_nodes)
    deleted_edge_count = await delete_obsolete_edges(conversation_id, obsolete_edges)

    return {
        "conversation_id": conversation_id,
        "actions_applied": int(add_result.get("actions_applied", 0)) + deleted_count + deleted_edge_count,
        "actions": add_result.get("actions", []),
        "added_actions": add_result.get("actions", []),
        "added_count": int(add_result.get("actions_applied", 0)),
        "obsolete_nodes": obsolete_nodes,
        "obsolete_edges": obsolete_edges,
        "deleted_count": deleted_count,
        "deleted_edge_count": deleted_edge_count,
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


