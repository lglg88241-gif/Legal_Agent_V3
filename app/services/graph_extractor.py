from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from json_repair import repair_json
from langchain_community.chat_models import ChatZhipuAI
from langchain_core.messages import HumanMessage, SystemMessage
from neo4j import GraphDatabase
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError

from app.core.config import settings
from app.core.llm_provider import get_llm_router
from app.agent.prompts.extractor import (  # noqa: E402
    GRAPH_EXTRACTION_PROMPT,
    INCREMENTAL_EXTRACTION_PROMPT,
    SEMANTIC_GC_PROMPT,
    BELIEF_REVISION_PROMPT,
    BELIEF_REVISION_PROMPT_V2,
)
from app.services.graph_reference_guard import evaluate_graph_action_reference_guard_async

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

FETCH_NODE_RECORDS_QUERY = """
MATCH (n:DynamicCaseEntity {conversation_id: $conversation_id})
RETURN n.name AS name,
       n.category AS category,
       n.node_type AS node_type,
       n.entity_type AS entity_type,
       n.role AS role,
       n.participant_role AS participant_role
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
GRAPH_OPENAI_MODEL = "gpt-5.1"
GRAPH_ZHIPU_FALLBACK_MODEL = "glm-4"
SELF_NAME_PATTERNS = (
    re.compile(r"(?:我叫|本人叫|本人名叫|我的名字叫|我名叫)([\u4e00-\u9fff]{2,6})"),
    re.compile(r"我是([\u4e00-\u9fff]{2,6})(?:[，,。；;]|，?是这起|，?是本案|，?作为|这起|本案)"),
)

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
}

SELF_ANCHOR_NAMES = {"我", "本人", "自己", "用户", "我们", "咱们", "当事人", "劳动者", "申请人"}
GENERIC_PERSON_NAMES = SELF_ANCHOR_NAMES | {"员工", "职工", "个人"}
ORG_ANCHOR_NAMES = {"公司", "单位", "用人单位", "企业", "老板公司", "所在公司", "任职公司"}
ORG_NAME_HINTS = {
    "有限责任公司",
    "股份有限公司",
    "集团有限公司",
    "集团公司",
    "有限公司",
    "公司",
    "科技",
    "集团",
    "企业",
    "单位",
    "工厂",
    "厂",
    "事务所",
    "银行",
    "医院",
    "学校",
    "机构",
}
RESPONSIBLE_ORG_ENTITY_HINTS = {
    "有限责任公司",
    "股份有限公司",
    "集团有限公司",
    "集团公司",
    "有限公司",
    "公司",
    "集团",
    "企业",
    "单位",
    "工厂",
    "厂",
    "事务所",
    "银行",
    "医院",
    "学校",
    "机构",
}
AUTHORITY_NAME_HINTS = {"法院", "仲裁委", "委员会", "人社局", "社保局", "劳动监察", "劳动局"}
ROLE_NAME_HINTS = {"HR", "hr", "人事", "经理", "主管", "主任", "负责人", "法人", "法定代表人", "代表", "代理人", "老板", "法务"}
EVIDENCE_NAME_HINTS = {
    "证据",
    "证明",
    "工资流水",
    "银行流水",
    "微信",
    "企业微信",
    "钉钉",
    "飞书",
    "短信",
    "邮件",
    "邮箱",
    "电话",
    "通话",
    "录音",
    "微信聊天",
    "微信聊天记录",
    "聊天记录",
    "聊天截图",
    "打卡记录",
    "考勤记录",
    "转账记录",
    "录音",
    "截图",
    "凭证",
    "证人证言",
}
COMMUNICATION_TOOL_NAME_HINTS = {
    "微信",
    "企业微信",
    "钉钉",
    "飞书",
    "QQ",
    "qq",
    "短信",
    "邮件",
    "邮箱",
    "电话",
    "手机",
    "通话",
    "语音",
    "视频会议",
    "会议软件",
    "聊天软件",
    "沟通软件",
    "办公软件",
}
OBJECT_NAME_HINTS = {
    "车辆",
    "合同",
    "劳动合同",
    "工资",
    "薪资",
    "薪酬",
    "社保",
    "社会保险",
    "岗位",
    "保险",
    "金额",
    "平台",
    "软件",
    "系统",
    "APP",
    "app",
    "客户端",
    "小程序",
    "咨询主题",
    "案由",
    "纠纷",
    "劳动纠纷",
    "争议类型",
    "案件类型",
}
EVENT_NAME_HINTS = {
    "事故",
    "争议",
    "纠纷",
    "仲裁申请",
    "仲裁",
    "申请",
    "解除",
    "辞退",
    "开除",
    "工伤认定",
    "索赔",
    "违约",
    "受伤",
    "拖欠",
    "欠薪",
    "未签",
    "未缴纳",
    "补缴",
    "通知",
}
CONSEQUENCE_NAME_HINTS = {
    "赔偿",
    "补偿",
    "损失",
    "经济损失",
    "医疗费",
    "工伤待遇",
    "伤残",
    "后果",
}
EVENT_NAME_HINTS = EVENT_NAME_HINTS | {
    "\u6253\u5361",
    "\u8003\u52e4",
    "\u51fa\u52e4",
    "\u7b7e\u5230",
    "\u7b7e\u9000",
    "\u8865\u7b7e",
    "\u4e0a\u73ed",
    "\u878d\u8d44\u5230\u8d26",
    "\u5230\u8d26",
}
OBJECT_NAME_HINTS = OBJECT_NAME_HINTS | {
    "\u52b3\u52a8\u5173\u7cfb",
    "\u52b3\u52a1\u5173\u7cfb",
    "\u7528\u5de5\u5173\u7cfb",
    "\u4e8b\u5b9e\u52b3\u52a8\u5173\u7cfb",
}
RESPONSIBLE_RELATION_HINTS = {
    "任职",
    "就职",
    "工作",
    "受雇",
    "雇佣",
    "用工",
    "劳动关系",
    "签约方",
    "发放工资",
    "缴纳社保",
    "拖欠工资",
    "欠薪",
    "未缴纳",
    "未签",
    "解除",
    "辞退",
    "开除",
    "通知",
    "实施",
    "管理",
}
IDENTITY_META_RELATIONS = {
    "姓名",
    "名字",
    "名称",
    "名叫",
    "叫",
    "自称",
    "本人叫",
    "实名",
    "身份",
    "别名",
    "昵称",
}
ATTRIBUTE_OBJECT_RELATIONS = {
    "工作地点",
    "办公地点",
    "所在地",
    "地点",
    "城市",
    "工作城市",
    "岗位",
    "职位",
    "职务",
    "工种",
    "工作内容",
    "从事工作",
    "咨询",
    "咨询事项",
    "咨询主题",
    "案由",
    "争议类型",
    "案件类型",
}
GRAPH_WRITE_GUARD_PROMPT = """
你是会话图谱写入前的语义守卫，不提供法律建议。

你的任务：审查抽取器准备写入 Neo4j 会话图谱的 ADD 动作，保证图谱节点类型严谨、责任体不被事实/证据/时间/属性污染，并和主体确认流程配合。

核心契约：
1. 会话图谱可以保留事实细节节点，例如上海、后端开发、好几个月、微信、融资到账、工资流水、聊天记录。
2. 但“主体”只能是可承担法律、用工、支付、管理、侵权或履约责任的人、公司、单位、机构、负责人、HR、人事、经理、代理人、代表等。
3. 地点、城市、岗位、职位、工种、金额、日期、时长、证据、软件、沟通渠道、合同、协议、劳动关系、咨询主题、案由、纠纷/争议类型、事件、条件、后果都不能是“主体”。
4. 如果动作中出现“我/本人/当事人/劳动者/申请人/他/她/公司/单位/人事/经理”等未安全解析的责任体泛称，且原句或已有候选无法明确解析，必须 decision=clarify，并把整批写入阻断。
5. 如果原句同一句已经明确给出主体，例如“蓝海科技的老板叫李四”，李四和蓝海科技都是明确主体，不要要求用户解释它们。
6. 不要输出“张三-姓名-张三”“张三-叫-张三”等命名元关系或自环关系。
7. 你可以改正 source_category/target_category，可以删除无意义动作，但不要凭空新增原动作之外的新事实。
8. 审查前必须先阅读 payload.conversation_context.recent_messages、dossier_context、dossier_graph_context 和 existing_node_records。
9. 如果 payload.self_anchor_target 或 payload.org_anchor_target 已提供，说明系统已从当前句或当前会话上下文解析出唯一锚点；必须把“我/当事人/劳动者/申请人”改写为 self_anchor_target，把“公司/单位/用人单位/企业”改写为 org_anchor_target 后 allow，不要要求用户重复确认。
10. dossier_context、dossier_graph_context 中的其他会话事实只能作为跨会话候选，不能直接授权改写“我/当事人”。如果当前会话没有明确 self_anchor_target，但案卷上下文显示“我”可能是上一轮的张三，必须 decision=clarify，询问用户是否承接上一轮/同一案卷当事人；用户确认前不能写入张三。
11. 只有在当前会话没有唯一候选、跨会话承接未确认、或同一泛称存在多个可能主体时，才 decision=clarify。

类型：
- 主体：责任承担者。
- 客体：地点、岗位、金额、日期、时长、证据、文件、工具、渠道、合同、工资、社保、咨询主题、案由、纠纷/争议类型、属性值等。
- 事件：拖欠工资、未签合同、违法辞退、承诺发工资、融资到账、通知、仲裁申请等行为/过程/条件。
- 后果：经济损失、赔偿、补偿、损害后果等结果。

只输出 JSON object：
{
  "decision": "allow" | "clarify" | "block",
  "confidence": "high" | "medium" | "low",
  "reason": "...",
  "clarification_questions": [],
  "reviewed_actions": [
    {
      "action": "ADD",
      "source": "...",
      "source_category": "主体|客体|事件|后果",
      "relation": "...",
      "target": "...",
      "target_category": "主体|客体|事件|后果",
      "old_target": "",
      "target_name": ""
    }
  ]
}
""".strip()


def _get_graph_llm() -> ChatZhipuAI:
    global _graph_llm

    if _graph_llm is None:
        with _graph_lock:
            if _graph_llm is None:
                _graph_llm = ChatZhipuAI(
                    model=GRAPH_ZHIPU_FALLBACK_MODEL,
                    api_key=settings.ZHIPUAI_API_KEY,
                    temperature=0.1,
                )
    return _graph_llm


async def _call_openai_graph_model(messages: list[dict[str, str]], *, max_tokens: int = 2048) -> dict[str, str]:
    router = get_llm_router()
    client = AsyncOpenAI(
        api_key=router.openai_api_key,
        base_url=router.openai_base_url,
        timeout=60.0,
    )
    try:
        response = await client.chat.completions.create(
            model=GRAPH_OPENAI_MODEL,
            messages=messages,
            temperature=0.1,
            max_tokens=max_tokens,
        )
        content = response.choices[0].message.content if response.choices else ""
        return {
            "provider": "openai",
            "model": GRAPH_OPENAI_MODEL,
            "content": content if isinstance(content, str) else str(content or ""),
        }
    finally:
        await client.close()


async def _call_zhipu_graph_model(messages: list[Any], *, max_tokens: int = 2048) -> dict[str, str]:
    llm = _get_graph_llm()
    if hasattr(llm, "max_tokens"):
        try:
            llm.max_tokens = max_tokens
        except Exception:
            pass
    response = await llm.ainvoke(messages)
    content = response.content if hasattr(response, "content") else response
    return {
        "provider": "zhipu",
        "model": GRAPH_ZHIPU_FALLBACK_MODEL,
        "content": content if isinstance(content, str) else str(content or ""),
    }


def _messages_for_openai(messages: list[Any]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for message in messages:
        if isinstance(message, dict):
            role = str(message.get("role") or "user").lower()
            content = message.get("content", "")
        else:
            role = str(getattr(message, "type", type(message).__name__.replace("Message", ""))).lower()
            content = getattr(message, "content", "")
        if role in {"human", "user"}:
            role = "user"
        elif role in {"ai", "assistant"}:
            role = "assistant"
        elif role != "system":
            role = "user"
        normalized.append({"role": role, "content": content if isinstance(content, str) else str(content or "")})
    return normalized


async def _call_graph_model(messages: list[Any], *, max_tokens: int = 2048) -> dict[str, str]:
    router = get_llm_router()
    if router.openai_api_key:
        try:
            result = await _call_openai_graph_model(
                _messages_for_openai(messages),
                max_tokens=max_tokens,
            )
            logger.info("Graph LLM dispatch | provider=%s model=%s", result["provider"], result["model"])
            return result
        except Exception as exc:
            logger.warning("OpenAI graph model failed, fallback to Zhipu %s: %s", GRAPH_ZHIPU_FALLBACK_MODEL, exc)
    else:
        logger.warning("OPENAI_API_KEY not set, fallback to Zhipu for graph updater")

    result = await _call_zhipu_graph_model(messages, max_tokens=max_tokens)
    logger.info("Graph LLM dispatch | provider=%s model=%s", result["provider"], result["model"])
    return result


def _build_driver():
    return GraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
    )


def _canonicalize_entity(name: str) -> str:
    cleaned = " ".join((name or "").strip().split())
    if not cleaned:
        return ""
    # Keep explicit organization/person names intact. Only collapse a small
    # set of stable self/pronoun aliases and a few object aliases.
    return ENTITY_ALIAS_MAP.get(cleaned, cleaned)


def _contains_any(value: str, hints: set[str]) -> bool:
    return any(hint in value for hint in hints)


def _extract_self_anchor_target_from_text(text: str) -> str:
    normalized = " ".join(str(text or "").strip().split())
    for pattern in SELF_NAME_PATTERNS:
        match = pattern.search(normalized)
        if not match:
            continue
        candidate = " ".join(str(match.group(1) or "").strip().split())
        if candidate and _looks_like_specific_person_name(candidate):
            return candidate
    return ""


def _extract_self_anchor_target_from_context(context: dict[str, Any] | None) -> str:
    if not isinstance(context, dict):
        return ""
    for message in list(context.get("recent_messages") or []):
        if not isinstance(message, dict) or str(message.get("role") or "") != "user":
            continue
        target = _extract_self_anchor_target_from_text(str(message.get("content") or ""))
        if target:
            return target
    for record in list(context.get("existing_node_records") or []):
        if not isinstance(record, dict):
            continue
        name = str(record.get("name") or record.get("label") or "").strip()
        role = str(record.get("role") or record.get("participant_role") or "").strip()
        if role in {"current_party", "当事人", "劳动者", "申请人"} and _looks_like_specific_person_name(name):
            return name
    return ""


def _extract_cross_conversation_self_anchor_candidates(context: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(context, dict):
        return []
    candidates: list[dict[str, Any]] = []
    dossier_context = context.get("dossier_context")
    if isinstance(dossier_context, dict):
        for item in list(dossier_context.get("confirmed_participants") or []):
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or item.get("canonical_label") or "").strip()
            role = str(item.get("role") or item.get("participant_role") or "").strip()
            side = str(item.get("party_side") or "").strip()
            entity_type = str(item.get("entity_type") or item.get("type") or "").strip()
            if not _looks_like_specific_person_name(label):
                continue
            if (
                role in {"current_party", "当事人", "劳动者", "申请人", "员工", "本人"}
                or side in {"claimant", "primary", "current_party"}
                or entity_type in {"person", "natural_person", "human"}
            ):
                source_conversation_ids = []
                for mention in list(dossier_context.get("participant_mentions") or []):
                    if not isinstance(mention, dict):
                        continue
                    mention_label = str(
                        mention.get("participant_label")
                        or mention.get("graph_node_label")
                        or mention.get("raw_label")
                        or ""
                    ).strip()
                    if mention_label != label:
                        continue
                    try:
                        mention_conversation_id = int(mention.get("conversation_id") or 0)
                    except (TypeError, ValueError):
                        mention_conversation_id = 0
                    if mention_conversation_id > 0:
                        source_conversation_ids.append(mention_conversation_id)
                candidates.append(
                    {
                        "label": label,
                        "role": role,
                        "party_side": side,
                        "entity_type": entity_type,
                        "source_conversation_ids": sorted(set(source_conversation_ids)),
                    }
                )
    unique: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        label = str(candidate.get("label") or "").strip()
        if not label:
            continue
        existing = unique.get(label)
        if existing:
            existing_ids = set(existing.get("source_conversation_ids") or [])
            existing["source_conversation_ids"] = sorted(existing_ids | set(candidate.get("source_conversation_ids") or []))
            continue
        unique[label] = candidate
    return [unique[key] for key in sorted(unique)]


ORG_NAME_PATTERNS = (
    re.compile(r"(?:一家叫|一间叫|叫做|名为|名称为)([\u4e00-\u9fffA-Za-z0-9（）()·\-]{2,30}?)(?:的)?(?:公司|单位|企业)"),
    re.compile(r"([\u4e00-\u9fffA-Za-z0-9（）()·\-]{2,30}?)(?:公司|单位|企业)(?:从|一直|没有|未|拖欠|通知|要求|安排|辞退|解除|补签|发放|支付|不给|不给我|的人事|的老板|的经理)"),
)


def _looks_like_specific_org_name(name: str) -> bool:
    cleaned = " ".join(str(name or "").strip().split())
    if not cleaned or cleaned in ORG_ANCHOR_NAMES:
        return False
    if _looks_like_non_responsible_name(cleaned):
        return False
    if _contains_any(cleaned, AUTHORITY_NAME_HINTS):
        return False
    return _contains_any(cleaned, ORG_NAME_HINTS | RESPONSIBLE_ORG_ENTITY_HINTS) or (
        len(cleaned) >= 2
        and len(cleaned) <= 30
        and any("\u4e00" <= char <= "\u9fff" for char in cleaned)
        and not _looks_like_specific_person_name(cleaned)
    )


def _extract_org_anchor_target_from_text(text: str) -> str:
    normalized = " ".join(str(text or "").strip().split())
    for pattern in ORG_NAME_PATTERNS:
        match = pattern.search(normalized)
        if not match:
            continue
        candidate = " ".join(str(match.group(1) or "").strip().split())
        candidate = re.sub(r"^(?:在|到|去|这家|那家|一家)", "", candidate).strip()
        candidate = re.sub(r"(?:这家|那家|一家)$", "", candidate).strip()
        if candidate and _looks_like_specific_org_name(candidate):
            return candidate
    return ""


def _extract_org_anchor_target_from_context(context: dict[str, Any] | None) -> str:
    if not isinstance(context, dict):
        return ""
    for message in list(context.get("recent_messages") or []):
        if not isinstance(message, dict) or str(message.get("role") or "") != "user":
            continue
        target = _extract_org_anchor_target_from_text(str(message.get("content") or ""))
        if target:
            return target
    candidates: list[str] = []
    for record in list(context.get("existing_node_records") or []):
        if not isinstance(record, dict):
            continue
        name = str(record.get("name") or record.get("label") or "").strip()
        category = str(record.get("category") or record.get("node_type") or "").strip()
        if category and category != CATEGORY_SUBJECT:
            continue
        if _looks_like_specific_org_name(name):
            candidates.append(name)
    unique_candidates = sorted(set(candidates))
    return unique_candidates[0] if len(unique_candidates) == 1 else ""


NON_RESPONSIBLE_LABEL_HINTS = (
    EVIDENCE_NAME_HINTS
    | COMMUNICATION_TOOL_NAME_HINTS
    | OBJECT_NAME_HINTS
    | EVENT_NAME_HINTS
    | CONSEQUENCE_NAME_HINTS
    | {
        "\u5173\u7cfb",
        "\u5408\u540c",
        "\u534f\u8bae",
        "\u5de5\u8d44",
        "\u85aa\u8d44",
        "\u8865\u507f",
        "\u8d54\u507f",
        "\u635f\u5931",
        "\u8bc1\u636e",
        "\u8bb0\u5f55",
        "\u6d41\u6c34",
        "\u622a\u56fe",
        "\u5f55\u97f3",
        "\u901a\u77e5",
        "\u627f\u8bfa",
        "\u53d1\u653e",
        "\u62d6\u6b20",
        "\u6b20\u85aa",
        "\u8f9e\u9000",
        "\u89e3\u9664",
        "\u79bb\u804c",
        "\u5165\u804c",
        "\u7b7e\u7ea6",
        "\u8865\u7b7e",
        "\u7f34\u7eb3",
        "\u672a\u7b7e",
        "\u672a\u7f34",
        "\u5230\u8d26",
        "\u878d\u8d44",
        "\u6761\u4ef6",
        "\u539f\u56e0",
        "\u7ed3\u679c",
        "\u540e\u679c",
        "\u4e8b\u5b9e",
        "\u60c5\u51b5",
        "\u95ee\u9898",
        "\u8bf7\u6c42",
        "\u4e3b\u5f20",
        "\u7533\u8bf7",
        "\u4ef2\u88c1",
        "\u8bc9\u8bbc",
        "\u91d1\u989d",
        "\u65e5\u671f",
        "\u65f6\u95f4",
        "\u6708\u85aa",
        "\u793e\u4fdd",
        "\u5c97\u4f4d",
        "\u8003\u52e4",
        "\u51fa\u52e4",
        "\u6253\u5361",
    }
)


def _looks_like_human_name_shape(name: str) -> bool:
    cleaned = " ".join(str(name or "").strip().split())
    if not cleaned:
        return False
    if len(cleaned) in {2, 3} and all("\u4e00" <= char <= "\u9fff" for char in cleaned):
        return True
    return len(cleaned) <= 8 and any(char.isalpha() for char in cleaned) and any("\u4e00" <= char <= "\u9fff" for char in cleaned)


def _looks_like_specific_person_name(name: str) -> bool:
    cleaned = " ".join(str(name or "").strip().split())
    if not cleaned or cleaned in GENERIC_PERSON_NAMES:
        return False
    if _contains_any(
        cleaned,
        ORG_NAME_HINTS
        | AUTHORITY_NAME_HINTS
        | ROLE_NAME_HINTS
        | EVIDENCE_NAME_HINTS
        | COMMUNICATION_TOOL_NAME_HINTS
        | OBJECT_NAME_HINTS
        | EVENT_NAME_HINTS
        | CONSEQUENCE_NAME_HINTS,
    ):
        return False
    return _looks_like_human_name_shape(cleaned)


def _looks_like_responsible_entity_name(name: str) -> bool:
    cleaned = " ".join(str(name or "").strip().split())
    if not cleaned:
        return False
    if _looks_like_non_responsible_name(cleaned):
        return False
    if cleaned in SELF_ANCHOR_NAMES:
        return True
    if _contains_any(cleaned, ORG_NAME_HINTS | AUTHORITY_NAME_HINTS | ROLE_NAME_HINTS):
        return True
    return _looks_like_specific_person_name(cleaned)


def _looks_like_amount_or_time_name(name: str) -> bool:
    cleaned = " ".join(str(name or "").strip().split())
    if not cleaned:
        return False
    if any(char.isdigit() for char in cleaned) and _contains_any(
        cleaned,
        {"元", "万元", "块", "人民币", "年", "月", "日", "号", "天", "个月", "%", "％"},
    ):
        return True
    if re.fullmatch(r"(?:好几|好些|多个|数个|几个|若干|很多|许多|多|数|半|一|两|三|四|五|六|七|八|九|十)[年月日天周星期个]*", cleaned):
        return True
    if re.fullmatch(r".*(?:几个月|数月|多月|好几个月|好些个月|若干个月|很久|长期|一段时间|一阵子|一段日子).*", cleaned):
        return True
    return False


def _looks_like_communication_tool_name(name: str) -> bool:
    cleaned = " ".join(str(name or "").strip().split())
    if not cleaned:
        return False
    if _contains_any(cleaned, RESPONSIBLE_ORG_ENTITY_HINTS | AUTHORITY_NAME_HINTS):
        return False
    return _contains_any(cleaned, COMMUNICATION_TOOL_NAME_HINTS)


def _looks_like_non_responsible_name(name: str) -> bool:
    cleaned = " ".join(str(name or "").strip().split())
    if not cleaned:
        return False
    if _contains_any(cleaned, RESPONSIBLE_ORG_ENTITY_HINTS | AUTHORITY_NAME_HINTS):
        return False
    if _looks_like_communication_tool_name(cleaned):
        return True
    if _looks_like_amount_or_time_name(cleaned):
        return True
    return _contains_any(
        cleaned,
        NON_RESPONSIBLE_LABEL_HINTS,
    )


def _is_self_anchor_name(name: Any) -> bool:
    return _canonicalize_entity(str(name or "")) in SELF_ANCHOR_NAMES


def _resolve_self_anchor_name(name: str, self_anchor_target: str = "") -> str:
    canonical = _canonicalize_entity(name)
    if self_anchor_target and canonical in SELF_ANCHOR_NAMES:
        return self_anchor_target
    return canonical


def _normalize_category(value: Any, entity_name: str = "") -> str:
    name = entity_name.strip()
    if not name:
        return CATEGORY_OBJECT

    explicit_category = value.strip() if isinstance(value, str) else ""
    if _looks_like_amount_or_time_name(name):
        return CATEGORY_OBJECT
    if _looks_like_communication_tool_name(name):
        return CATEGORY_OBJECT
    if _looks_like_non_responsible_name(name):
        if _contains_any(name, CONSEQUENCE_NAME_HINTS):
            return CATEGORY_CONSEQUENCE
        if _contains_any(name, EVENT_NAME_HINTS):
            return CATEGORY_EVENT
        return CATEGORY_OBJECT
    if _looks_like_responsible_entity_name(name):
        return CATEGORY_SUBJECT
    if _contains_any(name, CONSEQUENCE_NAME_HINTS):
        return CATEGORY_CONSEQUENCE
    if _contains_any(name, EVENT_NAME_HINTS):
        return CATEGORY_EVENT
    if _contains_any(name, EVIDENCE_NAME_HINTS | OBJECT_NAME_HINTS):
        return CATEGORY_OBJECT
    if explicit_category in ALLOWED_CATEGORIES:
        return explicit_category
    return CATEGORY_OBJECT


def _normalize_fact_categories(
    *,
    source: str,
    source_category: Any,
    relation: str,
    target: str,
    target_category: Any,
) -> tuple[str, str]:
    normalized_source_category = _normalize_category(source_category, source)
    normalized_target_category = _normalize_category(target_category, target)
    relation_text = _normalize_relation(relation)
    relation_implies_responsible = _contains_any(relation_text, RESPONSIBLE_RELATION_HINTS)

    if relation_implies_responsible and _looks_like_responsible_entity_name(source):
        normalized_source_category = CATEGORY_SUBJECT
    if relation_implies_responsible and _looks_like_responsible_entity_name(target):
        normalized_target_category = CATEGORY_SUBJECT
    if relation_text in ATTRIBUTE_OBJECT_RELATIONS:
        normalized_target_category = CATEGORY_OBJECT
    if _looks_like_non_responsible_name(source):
        normalized_source_category = _normalize_category("", source)
    if _looks_like_non_responsible_name(target):
        normalized_target_category = _normalize_category("", target)
    if relation_text in ATTRIBUTE_OBJECT_RELATIONS:
        normalized_target_category = CATEGORY_OBJECT
    return normalized_source_category, normalized_target_category


def _normalize_relation(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _is_degenerate_graph_fact(source: str, relation: str, target: str) -> bool:
    normalized_relation = _normalize_relation(relation)
    if source and target and _canonicalize_entity(source) == _canonicalize_entity(target):
        return True
    return normalized_relation in IDENTITY_META_RELATIONS


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


def _parse_graph_write_guard_response(
    raw_content: str,
    fallback_actions: list[dict[str, str]],
    *,
    self_anchor_target: str = "",
    org_anchor_target: str = "",
) -> dict[str, Any]:
    parsed = _safe_json_load(raw_content, {})
    if not isinstance(parsed, dict):
        parsed = {}
    decision = str(parsed.get("decision") or "allow").strip().lower()
    if decision not in {"allow", "clarify", "block"}:
        decision = "allow"
    raw_reviewed_actions = [
        dict(action)
        for action in list(parsed.get("reviewed_actions") or fallback_actions)
        if isinstance(action, dict)
    ]
    reviewed_actions = _normalize_actions(
        {"actions": raw_reviewed_actions},
        self_anchor_target=self_anchor_target,
        org_anchor_target=org_anchor_target,
    )
    validation_errors = _validate_graph_write_guard_actions(
        raw_reviewed_actions,
        reviewed_actions,
        fallback_actions=fallback_actions,
        self_anchor_target=self_anchor_target,
    )
    return {
        "source": "graph_write_guard",
        "provider": "",
        "model": "",
        "decision": decision,
        "confidence": str(parsed.get("confidence") or "medium").strip().lower() or "medium",
        "reason": str(parsed.get("reason") or "graph_write_guard_reviewed").strip(),
        "needs_clarification": decision == "clarify",
        "clarification_questions": [
            str(item).strip()
            for item in list(parsed.get("clarification_questions") or [])
            if str(item).strip()
        ],
        "validation_errors": validation_errors,
        "raw_reviewed_actions": raw_reviewed_actions,
        "allowed_actions": reviewed_actions if decision == "allow" else [],
        "blocked_actions": fallback_actions if decision in {"clarify", "block"} else [],
    }


def _validate_graph_write_guard_actions(
    raw_actions: list[dict[str, Any]],
    normalized_actions: list[dict[str, str]],
    *,
    fallback_actions: list[dict[str, str]] | None = None,
    self_anchor_target: str = "",
) -> list[str]:
    errors: list[str] = []
    if len(raw_actions) != len(normalized_actions):
        errors.append("规则校验删除了无效动作：存在自环、命名元关系、缺字段或非法 action。")
    for index, raw in enumerate(raw_actions):
        source = _resolve_action_entity_name(raw.get("source", ""))
        relation = _normalize_relation(raw.get("relation", ""))
        target = _resolve_action_entity_name(raw.get("new_target", "") or raw.get("target", ""))
        raw_source_category = str(raw.get("source_category") or "").strip()
        raw_target_category = str(raw.get("target_category") or "").strip()
        if source and relation and target and _is_degenerate_graph_fact(source, relation, target):
            errors.append(f"第{index + 1}条违反规则：不能输出自环或命名元关系 `{source}-{relation}-{target}`。")
        if source and raw_source_category == CATEGORY_SUBJECT and _looks_like_non_responsible_name(source):
            errors.append(f"第{index + 1}条违反规则：source `{source}` 是事实、证据、工具、时间、时长、条件、后果或属性值，不能标为主体。")
        if target and raw_target_category == CATEGORY_SUBJECT and _looks_like_non_responsible_name(target):
            errors.append(f"第{index + 1}条违反规则：target `{target}` 是事实、证据、工具、时间、时长、条件、后果或属性值，不能标为主体。")
        if relation in ATTRIBUTE_OBJECT_RELATIONS and raw_target_category == CATEGORY_SUBJECT:
            errors.append(f"第{index + 1}条违反规则：关系 `{relation}` 的 target `{target}` 是属性值，必须标为客体。")
    if not self_anchor_target:
        fallback_list = [dict(action) for action in list(fallback_actions or []) if isinstance(action, dict)]
        for index, fallback in enumerate(fallback_list):
            reviewed = raw_actions[index] if index < len(raw_actions) else {}
            fallback_source = _resolve_action_entity_name(fallback.get("source", ""))
            fallback_target = _resolve_action_entity_name(fallback.get("new_target", "") or fallback.get("target", ""))
            reviewed_source = _resolve_action_entity_name(reviewed.get("source", "")) if isinstance(reviewed, dict) else ""
            reviewed_target = (
                _resolve_action_entity_name(reviewed.get("new_target", "") or reviewed.get("target", ""))
                if isinstance(reviewed, dict)
                else ""
            )
            if _is_self_anchor_name(fallback_source) and reviewed_source and not _is_self_anchor_name(reviewed_source):
                errors.append(
                    f"第{index + 1}条违反规则：当前会话尚未确认“我/当事人”的姓名，不能把 source `{fallback_source}` 直接改写为 `{reviewed_source}`。"
                )
            if _is_self_anchor_name(fallback_target) and reviewed_target and not _is_self_anchor_name(reviewed_target):
                errors.append(
                    f"第{index + 1}条违反规则：当前会话尚未确认“我/当事人”的姓名，不能把 target `{fallback_target}` 直接改写为 `{reviewed_target}`。"
                )
    return list(dict.fromkeys(errors))


def _actions_contain_self_anchor(actions: list[dict[str, Any]] | None) -> bool:
    for action in list(actions or []):
        if not isinstance(action, dict):
            continue
        source = _resolve_action_entity_name(action.get("source", ""))
        target = _resolve_action_entity_name(action.get("new_target", "") or action.get("target", ""))
        if _is_self_anchor_name(source) or _is_self_anchor_name(target):
            return True
    return False


def _build_cross_conversation_self_clarification_result(
    *,
    normalized_actions: list[dict[str, str]],
    validation_errors: list[str],
    self_anchor_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    candidates = [item for item in list(self_anchor_candidates or []) if isinstance(item, dict) and item.get("label")]
    first_candidate = candidates[0] if candidates else {}
    first_label = str(first_candidate.get("label") or "").strip()
    first_source_ids = [int(item) for item in list(first_candidate.get("source_conversation_ids") or []) if int(item or 0) > 0]
    source_text = f"，来源会话 ID：{', '.join(str(item) for item in first_source_ids)}" if first_source_ids else ""
    candidate_text = f"（例如：{first_label}{source_text}）" if first_label else ""
    return {
        "source": "graph_write_guard",
        "provider": "rules",
        "model": "graph-write-cross-conversation-context-contract-v1",
        "decision": "clarify",
        "confidence": "high",
        "reason": "cross_conversation_self_anchor_requires_user_confirmation",
        "needs_clarification": True,
        "clarification_questions": [
            f"这条补充里的“我/当事人”是否承接同一案卷上一轮已确认的当事人{candidate_text}？如果是，请回复“是，承接上一轮”；如果不是，请直接说明这里的“我”是谁。"
        ],
        "validation_errors": validation_errors,
        "raw_reviewed_actions": [],
        "allowed_actions": [],
        "blocked_actions": normalized_actions,
        "clarification_mode": "cross_conversation_self_anchor",
        "target_mention": "我/当事人",
        "reference_type": "self",
        "cross_conversation_candidates": candidates,
        "source_conversation_ids": first_source_ids,
        "suggested_entity_name": first_label,
        "pre_resolved_anchors": {
            "self_anchor_target": "",
            "org_anchor_target": "",
        },
    }


async def evaluate_graph_write_guard_async(
    *,
    user_input: str,
    actions: list[dict[str, str]],
    existing_node_records: list[dict[str, Any]] | None = None,
    conversation_context: dict[str, Any] | None = None,
    model_caller: Any | None = None,
) -> dict[str, Any]:
    context = conversation_context if isinstance(conversation_context, dict) else {}
    self_anchor_target = _extract_self_anchor_target_from_text(user_input) or _extract_self_anchor_target_from_context(
        {**context, "existing_node_records": list(existing_node_records or [])}
    )
    cross_conversation_self_candidates = _extract_cross_conversation_self_anchor_candidates(context)
    org_anchor_target = _extract_org_anchor_target_from_text(user_input) or _extract_org_anchor_target_from_context(
        {**context, "existing_node_records": list(existing_node_records or [])}
    )
    normalized_actions = _normalize_actions(
        {"actions": actions},
        self_anchor_target=self_anchor_target,
        org_anchor_target=org_anchor_target,
    )
    if not normalized_actions:
        return {
            "source": "graph_write_guard",
            "decision": "allow",
            "confidence": "high",
            "reason": "no_actions",
            "needs_clarification": False,
            "clarification_questions": [],
            "allowed_actions": [],
            "blocked_actions": [],
        }
    payload = {
        "user_input": str(user_input or "").strip(),
        "existing_node_records": list(existing_node_records or []),
        "conversation_context": conversation_context if isinstance(conversation_context, dict) else {},
        "proposed_actions": normalized_actions,
        "self_anchor_target": self_anchor_target,
        "org_anchor_target": org_anchor_target,
    }
    caller = model_caller
    try:
        last_result: dict[str, Any] | None = None
        feedback: list[str] = []
        for attempt in range(1, 4):
            attempt_payload = dict(payload)
            if feedback:
                attempt_payload["previous_validation_errors"] = feedback
                attempt_payload["instruction"] = "上一轮输出未通过确定性规则校验。请根据 previous_validation_errors 修正 reviewed_actions，只输出合法 JSON。"
            if caller is not None:
                raw = await caller(attempt_payload)
            else:
                raw = await _call_graph_model(
                    [
                        SystemMessage(content=GRAPH_WRITE_GUARD_PROMPT),
                        HumanMessage(content=json.dumps(attempt_payload, ensure_ascii=False, indent=2)),
                    ],
                    max_tokens=2400,
                )
            provider = str(raw.get("provider") or "unknown").strip() if isinstance(raw, dict) else "unknown"
            model = str(raw.get("model") or "unknown").strip() if isinstance(raw, dict) else "unknown"
            content = raw.get("content") if isinstance(raw, dict) else raw
            result = _parse_graph_write_guard_response(
                str(content or ""),
                normalized_actions,
                self_anchor_target=self_anchor_target,
                org_anchor_target=org_anchor_target,
            )
            result["provider"] = provider
            result["model"] = model
            result["reflection_attempts"] = attempt
            result["pre_resolved_anchors"] = {
                "self_anchor_target": self_anchor_target,
                "org_anchor_target": org_anchor_target,
            }
            last_result = result
            validation_errors = list(result.get("validation_errors") or [])
            if validation_errors and not self_anchor_target and _actions_contain_self_anchor(actions):
                if any("当前会话尚未确认" in error for error in validation_errors):
                    clarification = _build_cross_conversation_self_clarification_result(
                        normalized_actions=normalized_actions,
                        validation_errors=validation_errors,
                        self_anchor_candidates=cross_conversation_self_candidates,
                    )
                    clarification["provider"] = provider
                    clarification["model"] = model
                    clarification["reflection_attempts"] = attempt
                    clarification["pre_resolved_anchors"] = {
                        "self_anchor_target": "",
                        "org_anchor_target": org_anchor_target,
                    }
                    return clarification
            if not validation_errors or result.get("decision") in {"clarify", "block"}:
                return result
            feedback = validation_errors
            logger.warning(
                "Graph write guard validation failed on attempt %s/3: %s",
                attempt,
                "; ".join(validation_errors),
            )
        if last_result is not None:
            validation_errors = list(last_result.get("validation_errors") or [])
            if validation_errors and not self_anchor_target and _actions_contain_self_anchor(actions):
                if any("当前会话尚未确认" in error for error in validation_errors):
                    clarification = _build_cross_conversation_self_clarification_result(
                        normalized_actions=normalized_actions,
                        validation_errors=validation_errors,
                        self_anchor_candidates=cross_conversation_self_candidates,
                    )
                    clarification["reflection_attempts"] = 3
                    clarification["pre_resolved_anchors"] = {
                        "self_anchor_target": "",
                        "org_anchor_target": org_anchor_target,
                    }
                    return clarification
            last_result["reason"] = f"{last_result.get('reason')}; deterministic_rules_applied_after_reflection_exhausted"
            last_result["confidence"] = "low"
            last_result["pre_resolved_anchors"] = {
                "self_anchor_target": self_anchor_target,
                "org_anchor_target": org_anchor_target,
            }
            last_result["allowed_actions"] = _normalize_actions(
                {"actions": last_result.get("raw_reviewed_actions") or normalized_actions},
                self_anchor_target=self_anchor_target,
                org_anchor_target=org_anchor_target,
            )
            return last_result
    except Exception as exc:
        logger.warning("Graph write guard failed; using normalized actions fallback: %s", exc)
        return {
            "source": "graph_write_guard",
            "provider": "fallback",
            "model": "graph-write-guard-fallback",
            "decision": "allow",
            "confidence": "low",
            "reason": "guard_unavailable_normalized_fallback",
            "needs_clarification": False,
            "clarification_questions": [],
            "allowed_actions": normalized_actions,
            "blocked_actions": [],
            "pre_resolved_anchors": {
                "self_anchor_target": self_anchor_target,
                "org_anchor_target": org_anchor_target,
            },
        }


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
        if _is_degenerate_graph_fact(source, relation, target):
            continue

        key = (source, relation, target)
        if key in seen:
            continue
        seen.add(key)
        source_category, target_category = _normalize_fact_categories(
            source=source,
            source_category=item.get("source_category"),
            relation=relation,
            target=target,
            target_category=item.get("target_category"),
        )

        normalized.append(
            {
                "source": source,
                "source_category": source_category,
                "relation": relation,
                "target": target,
                "target_category": target_category,
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


def _resolve_action_entity_name(
    name: Any,
    existing_nodes: set[str] | None = None,
    self_anchor_target: str = "",
    org_anchor_target: str = "",
) -> str:
    cleaned = " ".join(str(name or "").strip().split())
    if not cleaned:
        return ""
    if self_anchor_target and _is_self_anchor_name(cleaned):
        return self_anchor_target
    if org_anchor_target and _canonicalize_entity(cleaned) in ORG_ANCHOR_NAMES:
        return org_anchor_target
    return _resolve_existing_node_name(cleaned, existing_nodes)


def _normalize_actions(
    raw_payload: Any,
    existing_nodes: set[str] | None = None,
    self_anchor_target: str = "",
    org_anchor_target: str = "",
) -> list[dict[str, str]]:
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

        source = _resolve_action_entity_name(
            item.get("source", ""),
            existing_nodes,
            self_anchor_target,
            org_anchor_target,
        )
        relation = _normalize_relation(item.get("relation", ""))
        target = _resolve_action_entity_name(
            item.get("new_target", "") or item.get("target", ""),
            existing_nodes,
            self_anchor_target,
            org_anchor_target,
        )
        old_target = _resolve_action_entity_name(
            item.get("old_target", "") or item.get("target", ""),
            existing_nodes,
            self_anchor_target,
            org_anchor_target,
        )
        target_name = _resolve_action_entity_name(
            item.get("target_name", "") or item.get("target", "") or item.get("old_target", ""),
            existing_nodes,
            self_anchor_target,
            org_anchor_target,
        )

        if action == "ADD" and (not source or not relation or not target):
            continue
        if action == "ADD" and _is_degenerate_graph_fact(source, relation, target):
            continue
        if action == "UPDATE" and (not source or not relation or not old_target or not target):
            continue
        if action == "DELETE" and not (target_name or (source and relation and old_target)):
            continue
        source_category, target_category = _normalize_fact_categories(
            source=source,
            source_category=item.get("source_category"),
            relation=relation,
            target=target,
            target_category=item.get("target_category"),
        )

        normalized.append(
            {
                "action": action,
                "source": source,
                "source_category": source_category,
                "relation": relation,
                "target": target if action != "DELETE" else target_name,
                "target_category": target_category,
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


def _infer_self_anchor_target_from_records(records: list[dict[str, Any]]) -> str:
    candidates: list[str] = []
    for record in records:
        name = _canonicalize_entity(str(record.get("name") or "").strip())
        if not _looks_like_specific_person_name(name):
            continue
        category = str(record.get("category") or record.get("node_type") or "").strip()
        role = str(record.get("role") or record.get("participant_role") or "").strip()
        if category and category != CATEGORY_SUBJECT:
            continue
        if role in {"current_party", "劳动者", "申请人", "当事人"}:
            return name
        candidates.append(name)

    unique_candidates = sorted(set(candidates))
    return unique_candidates[0] if len(unique_candidates) == 1 else ""


def _get_self_anchor_target_sync(session, conversation_id: int) -> str:
    result = session.run(
        """
        MATCH (n:DynamicCaseEntity {conversation_id: $conversation_id})
        RETURN n.name AS name,
               n.category AS category,
               n.node_type AS node_type,
               n.role AS role,
               n.participant_role AS participant_role
        """,
        {"conversation_id": conversation_id},
    )
    records = [dict(record) for record in result]
    return _infer_self_anchor_target_from_records(records)


def _execute_graph_actions_sync(
    conversation_id: int,
    actions: list[dict[str, str]],
    user_id: int = 0,
) -> dict[str, Any]:
    driver = _build_driver()
    applied = 0

    try:
        with driver.session() as session:
            self_anchor_target = _get_self_anchor_target_sync(session, conversation_id)
            actions_to_apply = (
                _normalize_actions({"actions": actions}, self_anchor_target=self_anchor_target)
                if self_anchor_target
                else actions
            )
            for action in actions_to_apply:
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
        "actions": actions_to_apply if "actions_to_apply" in locals() else actions,
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
        source_category, target_category = _normalize_fact_categories(
            source=source,
            source_category=record.get("source_category"),
            relation=relation,
            target=target,
            target_category=record.get("target_category"),
        )
        facts.append(
            {
                "source": source,
                "source_category": source_category,
                "relation": relation,
                "target": target,
                "target_category": target_category,
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


def _fetch_case_graph_node_records_sync(conversation_id: int) -> list[dict[str, Any]]:
    driver = _build_driver()
    try:
        with driver.session() as session:
            return session.run(FETCH_NODE_RECORDS_QUERY, {"conversation_id": conversation_id}).data()
    finally:
        driver.close()


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
        result = await _call_graph_model(
            [
                SystemMessage(content=GRAPH_EXTRACTION_PROMPT),
                HumanMessage(content=f"输入文本：\n{clean_text}"),
            ],
            max_tokens=2048,
        )
        content = result["content"]
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


async def get_case_graph_node_records(conversation_id: int) -> list[dict[str, Any]]:
    try:
        return await asyncio.to_thread(_fetch_case_graph_node_records_sync, conversation_id)
    except Exception as exc:
        logger.warning("Failed to load case graph node records for conversation %s: %s", conversation_id, exc)
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
            result = await _call_graph_model(
                [
                    SystemMessage(content="你必须只输出合法 JSON。"),
                    HumanMessage(content=prompt),
                ],
                max_tokens=2048,
            )
            content = result["content"]
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
            result = await _call_graph_model(
                [
                    SystemMessage(content="你必须只输出合法 JSON。"),
                    HumanMessage(content=prompt),
                ],
                max_tokens=2048,
            )
            content = result["content"]
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
    prior_reference_guard: dict[str, Any] | None = None,
    conversation_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_user_input = " ".join((user_input or "").strip().split())
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

    node_records = await get_case_graph_node_records(conversation_id)
    write_guard_result = await evaluate_graph_write_guard_async(
        user_input=normalized_user_input,
        actions=add_actions,
        existing_node_records=node_records,
        conversation_context=conversation_context,
    )
    if write_guard_result.get("needs_clarification"):
        clarification_questions = write_guard_result.get("clarification_questions", [])
        question = (clarification_questions or ["请确认待写入的责任主体。"])[0]
        pending_clarification = {
            "type": "subject_reference_resolution",
            "source": "graph_write_guard",
            "target_mention": write_guard_result.get("target_mention") or "这个主体",
            "reference_type": write_guard_result.get("reference_type") or "unknown",
            "previous_question": question,
            "scope_attempted": ["current_conversation_graph", "graph_write_guard"],
            "expected_response": "entity_name_or_context",
            "pending_graph_update_input_text": normalized_user_input,
        }
        for key in (
            "clarification_mode",
            "cross_conversation_candidates",
            "source_conversation_ids",
            "suggested_entity_name",
        ):
            if key in write_guard_result:
                pending_clarification[key] = write_guard_result.get(key)
        return {
            "conversation_id": conversation_id,
            "actions_applied": 0,
            "actions": [],
            "added_actions": [],
            "added_count": 0,
            "obsolete_nodes": [],
            "obsolete_edges": [],
            "deleted_count": 0,
            "deleted_edge_count": 0,
            "needs_clarification": True,
            "clarification_questions": clarification_questions or [question],
            "pending_clarification": pending_clarification,
            "reference_guard": write_guard_result,
            "graph_write_guard": write_guard_result,
        }
    add_actions = [
        action
        for action in write_guard_result.get("allowed_actions", [])
        if isinstance(action, dict)
    ]
    guard_result = await evaluate_graph_action_reference_guard_async(
        user_input=normalized_user_input,
        actions=add_actions,
        existing_node_records=node_records,
        prior_reference_guard=prior_reference_guard,
    )
    if guard_result.get("needs_clarification"):
        return {
            "conversation_id": conversation_id,
            "actions_applied": 0,
            "actions": [],
            "added_actions": [],
            "added_count": 0,
            "obsolete_nodes": [],
            "obsolete_edges": [],
            "deleted_count": 0,
            "deleted_edge_count": 0,
            "needs_clarification": True,
            "clarification_questions": guard_result.get("clarification_questions", []),
            "pending_clarification": guard_result.get("pending_clarification"),
            "reference_guard": guard_result,
            "graph_write_guard": write_guard_result,
        }
    add_actions = [
        action
        for action in guard_result.get("allowed_actions", [])
        if isinstance(action, dict)
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
        "needs_clarification": bool(guard_result.get("needs_clarification")),
        "clarification_questions": guard_result.get("clarification_questions", []),
        "pending_clarification": guard_result.get("pending_clarification"),
        "reference_guard": guard_result,
        "graph_write_guard": write_guard_result,
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


async def delete_case_graph(conversation_id: int) -> dict[str, Any]:
    try:
        await asyncio.to_thread(_delete_case_graph_sync, conversation_id)
        return {"success": True, "error": None}
    except Exception as exc:
        logger.warning("Failed to delete case graph for conversation %s: %s", conversation_id, exc)
        return {"success": False, "error": str(exc)}


