import asyncio
import json
import logging
from typing import Any

from json_repair import repair_json
from openai import AsyncOpenAI

from app.agent.tools import AGENT_TOOLS
from app.core.config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
你是一个专业的法律 AI 助手。

工作规则：
1. 遇到法条问题，必须调用 search_law_articles。
2. 遇到查询历史案例、案情比对、裁判思路分析时，必须综合使用 search_case_text 和 query_neo4j_graph。
3. query_neo4j_graph 只接受实体名、当事人名称、罪名或案由关键词，禁止自行编写 Cypher。
4. 最终回答必须严格基于工具返回结果作答；如果工具结果不足，明确说明证据不足或检索结果有限。
5. 回答保持严谨、简洁，优先引用检索到的法条要点、案例事实和裁判结论。
""".strip()

ROUTER_PROMPT = """
你是法律检索路由器。请根据用户问题决定要调用哪些工具，并严格返回 JSON。

可用工具：
1. search_law_articles(query: str)
2. search_case_text(query: str)
3. query_neo4j_graph(entity_name: str)

规则：
1. 法条、法规、量刑、法律依据问题，必须调用 search_law_articles。
2. 历史案例、类案检索、案情比对问题，必须调用 search_case_text。
3. 提到具体当事人、公司、罪名、案由、判决结果线索时，必须调用 query_neo4j_graph。
4. 需要多路召回时，可以返回多个工具调用。
5. 只能返回 JSON，不要输出解释。

返回格式：
{
  "tool_calls": [
    {"name": "search_law_articles", "arguments": {"query": "..."}},
    {"name": "search_case_text", "arguments": {"query": "..."}},
    {"name": "query_neo4j_graph", "arguments": {"entity_name": "..."}}
  ]
}
""".strip()

TOOL_REGISTRY = {tool.name: tool for tool in AGENT_TOOLS}


class LegalGraphAgent:
    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=settings.API_KEY,
            base_url=settings.BASE_URL,
        )
        self.model_name = settings.LLM_MODEL_NAME

    async def _chat(self, messages: list[dict[str, str]], max_tokens: int = 1024) -> str:
        response = await self.client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            temperature=0.1,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content or ""

    def _fallback_routes(self, question: str) -> list[dict[str, Any]]:
        routes: list[dict[str, Any]] = []
        lowered = question.lower()

        law_keywords = ["法条", "法律", "法规", "量刑", "依据", "民法典", "刑法", "劳动合同法", "消费者权益"]
        case_keywords = ["案例", "类案", "判决", "案情", "作案手法", "历史案例", "指导案例", "裁判"]
        graph_keywords = ["诉", "纠纷", "罪", "原告", "被告", "公司", "劳动争议", "逃税", "合同纠纷"]

        if any(keyword in question for keyword in law_keywords):
            routes.append({"name": "search_law_articles", "arguments": {"query": question}})
        if any(keyword in question for keyword in case_keywords):
            routes.append({"name": "search_case_text", "arguments": {"query": question}})
        if any(keyword in question for keyword in graph_keywords) or "case" in lowered:
            routes.append({"name": "query_neo4j_graph", "arguments": {"entity_name": question}})

        if not routes:
            routes.append({"name": "search_law_articles", "arguments": {"query": question}})
            routes.append({"name": "search_case_text", "arguments": {"query": question}})

        return routes

    async def _route_tools(self, question: str) -> list[dict[str, Any]]:
        try:
            content = await self._chat(
                [
                    {"role": "system", "content": ROUTER_PROMPT},
                    {"role": "user", "content": question},
                ],
                max_tokens=300,
            )
            parsed = json.loads(repair_json(content))
            tool_calls = parsed.get("tool_calls", [])
            valid_calls = []
            for call in tool_calls:
                name = call.get("name")
                arguments = call.get("arguments", {})
                if name in TOOL_REGISTRY and isinstance(arguments, dict):
                    valid_calls.append({"name": name, "arguments": arguments})
            if valid_calls:
                return valid_calls
        except Exception as exc:
            logger.warning("Tool routing failed, fallback to heuristic routes: %s", exc)

        return self._fallback_routes(question)

    async def _execute_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = TOOL_REGISTRY[tool_name]
        try:
            result = await asyncio.to_thread(tool.invoke, arguments)
            return {"tool": tool_name, "arguments": arguments, "result": result}
        except Exception as exc:
            logger.warning("Tool %s execution failed: %s", tool_name, exc)
            return {
                "tool": tool_name,
                "arguments": arguments,
                "result": json.dumps({"error": str(exc)}, ensure_ascii=False),
            }

    async def ask(self, session_id: str, question: str) -> str:
        del session_id

        tool_calls = await self._route_tools(question)
        tool_results = []
        for call in tool_calls:
            tool_results.append(await self._execute_tool_call(call["name"], call["arguments"]))

        answer_prompt = (
            f"用户问题：{question}\n\n"
            f"以下是检索工具返回结果（JSON）：\n"
            f"{json.dumps(tool_results, ensure_ascii=False)}\n\n"
            "请基于这些结果给出严谨的中文法律分析。如果结果不足，请明确指出。"
        )
        return await self._chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": answer_prompt},
            ]
        )


legal_agent = LegalGraphAgent()
