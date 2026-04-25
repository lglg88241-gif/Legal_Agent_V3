from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
from typing import Any

from json_repair import repair_json
from langchain_chroma import Chroma
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from neo4j import GraphDatabase

from app.core.bm25_retriever import BM25Retriever
from app.core.config import settings
from app.core import llm_factory as _llm_factory
from app.core.llm_factory import create_cloud_llm
from app.core.llm_provider import LLMProvider, get_llm_router
from app.core.reranker import BGEReranker
from app.database.chroma_utils import resolve_persist_dir

logger = logging.getLogger(__name__)

GRAPH_QUERY = """
MATCH (source:LawArticle)-[rel:REFERENCES]-(target:LawArticle)
WHERE any(token IN $tokens WHERE
    coalesce(source.law_title, '') CONTAINS token OR
    coalesce(source.article_label, '') CONTAINS token OR
    toString(coalesce(source.article_num, '')) CONTAINS token OR
    coalesce(target.law_title, '') CONTAINS token OR
    coalesce(target.article_label, '') CONTAINS token OR
    toString(coalesce(target.article_num, '')) CONTAINS token
)
RETURN labels(source) AS source_labels,
       properties(source) AS source_node,
       type(rel) AS relation,
       labels(target) AS target_labels,
       properties(target) AS target_node
LIMIT 5
"""


def _extract_graph_query_tokens(entity_name: str) -> list[str]:
    cleaned = " ".join((entity_name or "").strip().split())
    if not cleaned:
        return []

    tokens = [cleaned]
    tokens.extend(re.findall(r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,12}", cleaned))

    deduped: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        value = token.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped[:8]

QUERY_EXPANSION_SYSTEM_PROMPT = """
你是一个法律检索扩写助手。你必须先做指代消解，再做语义重写，最后做法律术语化。

要求：
1. 只返回 JSON，格式必须是 {"queries": ["查询1", "查询2", "查询3"]}。
2. 生成 3 到 5 个互补查询，覆盖核心案由、法律术语和检索意图（赔偿计算 / 程序 / 时效）。
3. 结合传入的历史对话和用户最新回复进行指代消解。用户回复可能极其简短，请先将"最新回复"代入"历史对话"的语境中还原完整案情，再提取法律专业查询词。
4. 如果用户只说"是""对""没有""全责"这类短句，必须利用历史对话做指代消解,不能只围绕短句表面含义扩写。
5. **法律术语化（核心要求）**：用户描述常用口语，但法条原文使用法言法语。至少 1 到 2 条扩展查询必须把用户的口语化纠纷描述重写成法律术语，便于检索到法条原文。
6. 严禁在扩展查询中编造具体法条编号（如"第八十七条"）。
7. 输出必须是 JSON，不要解释，不要 Markdown。

【俗语→法律术语映射示例】
- "违法辞退" → "违法解除劳动合同 / 用人单位违反本法规定解除劳动合同"
- "没签合同" → "未签订书面劳动合同 / 二倍工资"
- "没缴社保" → "未依法缴纳社会保险费"
- "拖欠工资" → "未足额支付劳动报酬 / 逾期支付劳动报酬"
- "高利贷" → "民间借贷利率上限 / 超过法律保护的利率"
- "醉驾" → "危险驾驶罪 / 醉酒驾驶机动车"

【输入输出示范】
输入: "我没签合同被违法辞退了,能要多少钱"
输出: {"queries": ["违法解除劳动合同赔偿金", "用人单位违反本法规定解除劳动合同 二倍赔偿", "未签订书面劳动合同 二倍工资", "经济补偿 工作年限计算", "劳动争议仲裁时效"]}

输入: "邻居高利贷不还该怎么办"
输出: {"queries": ["民间借贷纠纷起诉", "民间借贷利率上限保护", "超过法律保护的利率", "借贷合同效力", "民事诉讼时效"]}

输入: "朋友醉驾撞人怎么办"
输出: {"queries": ["危险驾驶罪 醉酒驾驶机动车", "交通肇事罪量刑", "刑事附带民事赔偿", "醉驾刑事辩护", "交通事故责任认定"]}
""".strip()

CHROMA_RECALL_K = 10
BM25_RECALL_K = 10
LAW_TOP_K = 6
CASE_TOP_K = 2

_query_expansion_local_llm: Any | None = None
_query_expansion_local_llm_lock = threading.Lock()
_query_expansion_fallback_llm: Any | None = None
_query_expansion_fallback_llm_lock = threading.Lock()
_ARTICLE_NUMBER_QUERY_PATTERN = re.compile(r"第[一二三四五六七八九十百零\d]+条")


def _extract_query_array_payload(content: str) -> str:
    """Normalize fenced or chatty LLM output into a JSON array payload."""
    cleaned = (content or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start != -1 and end != -1 and end > start:
        return cleaned[start : end + 1]
    return cleaned


def _coerce_expansion_queries(items: list[Any]) -> list[str]:
    queries: list[str] = []
    for item in items:
        if isinstance(item, str):
            queries.append(item)
            continue
        if isinstance(item, dict):
            value = item.get("query") or item.get("text") or item.get("content")
            if isinstance(value, str):
                queries.append(value)
    return queries


def _parse_expansion_queries(content: str) -> list[str]:
    payload = _extract_query_array_payload(content)
    parsed = json.loads(repair_json(payload))
    if isinstance(parsed, dict):
        queries = parsed.get("queries", [])
    elif isinstance(parsed, list):
        queries = parsed
    else:
        queries = []
    return _coerce_expansion_queries(queries)


def _multi_query_rerank_with_rrf(
    queries: list[str],
    candidates: list[dict[str, Any]],
    top_k: int,
    rrf_k: int = 60,
) -> list[dict[str, Any]]:
    """
    Per-query rerank + Reciprocal Rank Fusion.

    Why: joining multiple expanded queries into one long string dilutes
    the cross-encoder's attention. Each expansion typically captures one
    sub-intent; reranking them independently and fusing by reciprocal
    rank preserves each sub-intent's top hits.

    RRF formula: score(d) = Σ 1 / (rrf_k + rank_q(d))
    rrf_k=60 is the standard TREC choice.
    """
    if not candidates or not queries:
        return []

    reranker = BGEReranker()
    documents = [candidate["content"] for candidate in candidates]
    total_candidates = len(candidates)

    fused_scores: dict[int, float] = {
        index: 0.0 for index in range(total_candidates)
    }
    best_rerank_score: dict[int, float] = {
        index: float("-inf") for index in range(total_candidates)
    }
    used_queries = 0

    for query in queries:
        normalized_query = query.strip()
        if not normalized_query:
            continue
        used_queries += 1
        ranked = reranker.rerank_with_scores(
            normalized_query,
            documents,
            top_k=total_candidates,
        )
        for rank, item in enumerate(ranked, start=1):
            candidate_index = item["index"]
            fused_scores[candidate_index] += 1.0 / (rrf_k + rank)
            if item["score"] > best_rerank_score[candidate_index]:
                best_rerank_score[candidate_index] = item["score"]

    if used_queries == 0:
        return []

    ordered_indexes = sorted(
        range(total_candidates),
        key=lambda index: (
            fused_scores[index],
            best_rerank_score[index],
            -index,
        ),
        reverse=True,
    )

    ranked_results: list[dict[str, Any]] = []
    for candidate_index in ordered_indexes[:top_k]:
        score_value = best_rerank_score[candidate_index]
        ranked_results.append(
            {
                "index": candidate_index,
                "candidate": candidates[candidate_index],
                "fused_score": fused_scores[candidate_index],
                "rerank_score": 0.0 if score_value == float("-inf") else score_value,
            }
        )
    return ranked_results


def _serialize_reranked_documents(
    queries: list[str],
    candidates: list[dict[str, Any]],
    top_k: int,
) -> str:
    if not candidates:
        return json.dumps([], ensure_ascii=False)

    try:
        ranked = _multi_query_rerank_with_rrf(queries, candidates, top_k=top_k)
        payload = []
        for item in ranked:
            candidate = item["candidate"]
            metadata = candidate.get("metadata") or {}
            payload.append(
                {
                    "content": candidate["content"],
                    "source": metadata.get("source", ""),
                    "metadata": metadata,
                    "score": item["rerank_score"],
                    "fused_score": item["fused_score"],
                    "coarse_score": candidate.get("score"),
                    "retrieval": candidate.get("retrieval"),
                    "candidate_id": candidate.get("candidate_id"),
                    "matched_query": candidate.get("matched_query"),
                }
            )
        return json.dumps(payload, ensure_ascii=False)
    except Exception as exc:
        logger.warning("Reranker failed, fallback to hybrid recall: %s", exc)
        fallback_payload = []
        for candidate in candidates[:top_k]:
            metadata = candidate.get("metadata") or {}
            fallback_payload.append(
                {
                    "content": candidate["content"],
                    "source": metadata.get("source", ""),
                    "metadata": metadata,
                    "score": candidate.get("score"),
                    "retrieval": candidate.get("retrieval"),
                    "candidate_id": candidate.get("candidate_id"),
                    "matched_query": candidate.get("matched_query"),
                }
            )
        return json.dumps(fallback_payload, ensure_ascii=False)


def _get_query_expansion_local_llm() -> Any:
    global _query_expansion_local_llm

    if _query_expansion_local_llm is None:
        with _query_expansion_local_llm_lock:
            if _query_expansion_local_llm is None:
                _query_expansion_local_llm = _llm_factory.create_local_llm(
                    temperature=0.3,
                    max_tokens=512,
                )
    return _query_expansion_local_llm


def _get_query_expansion_fallback_llm() -> Any:
    global _query_expansion_fallback_llm

    if _query_expansion_fallback_llm is None:
        with _query_expansion_fallback_llm_lock:
            if _query_expansion_fallback_llm is None:
                _query_expansion_fallback_llm = create_cloud_llm(
                    temperature=0.3,
                    max_tokens=512,
                )
    return _query_expansion_fallback_llm


def _describe_query_expansion_target(llm: Any) -> tuple[str, str]:
    model_name = str(
        getattr(llm, "model_name", None)
        or getattr(llm, "model", None)
        or ""
    ).strip()
    base_url = str(
        getattr(llm, "openai_api_base", None)
        or getattr(llm, "base_url", None)
        or ""
    ).strip()
    return model_name, base_url


def _invoke_query_expansion_model(messages: list[Any]) -> Any:
    llm_router = get_llm_router()
    provider = llm_router.get_provider("query_expansion")
    if provider == LLMProvider.LOCAL_VLLM:
        model_name, base_url = _describe_query_expansion_target(_get_query_expansion_local_llm())
    else:
        model_name, base_url = _describe_query_expansion_target(_get_query_expansion_fallback_llm())
    logger.info(
        "Query expansion | provider=%s model=%s base_url=%s",
        provider.value,
        model_name or "unknown",
        base_url or "unknown",
    )

    if provider == LLMProvider.LOCAL_VLLM:
        try:
            response = _get_query_expansion_local_llm().invoke(messages)
            content = response.content if isinstance(response.content, str) else str(response.content)
            if _parse_expansion_queries(content):
                return response
            logger.warning("本地模型返回无效，fallback 到智谱")
        except Exception as exc:
            logger.warning("本地模型返回无效，fallback 到智谱: %s", exc)

    return _get_query_expansion_fallback_llm().invoke(messages)


def _normalize_query_list(original_query: str, queries: list[Any]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()

    for item in queries:
        if not isinstance(item, str):
            continue
        cleaned = " ".join(item.strip().split())
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)

    return normalized or [original_query]


def _sanitize_expanded_queries(original_query: str, queries: list[str]) -> list[str]:
    """Drop expanded queries that hallucinate article numbers."""
    filtered = [
        query for query in queries
        if not _ARTICLE_NUMBER_QUERY_PATTERN.search(query)
    ]
    filtered = _normalize_query_list(original_query, filtered)
    if len(filtered) < 2:
        return [original_query]
    return filtered


def _expand_queries(query: str, chat_history: str = "") -> list[str]:
    normalized_query = query.strip()
    if not normalized_query:
        return [query]

    try:
        history_text = (chat_history or "").strip() or "无历史对话"
        messages = [
                SystemMessage(content=QUERY_EXPANSION_SYSTEM_PROMPT),
                HumanMessage(
                    content=(
                        f"这是之前的聊天记录：\n{history_text}\n\n"
                        f"用户最新回复：{normalized_query}\n\n"
                        "由于用户的回复可能极其简短，请你先将“最新回复”代入“历史对话”的语境中还原完整案情，"
                        "然后再提取 3-5 个法律专业查询词。"
                        "例如：如果用户说“是”，结合上文你应提取“工伤认定、试用期解除合同、违法解除赔偿”等词。"
                        "必须以 JSON 数组格式返回。"
                    )
                ),
            ]
        response = _invoke_query_expansion_model(messages)
        content = response.content if isinstance(response.content, str) else str(response.content)
        queries = _parse_expansion_queries(content)
        expanded_queries = _sanitize_expanded_queries(
            normalized_query,
            _normalize_query_list(normalized_query, queries),
        )
        logger.info("Expanded query [%s] into %s", normalized_query, expanded_queries)
        return expanded_queries
    except Exception as exc:
        logger.warning("Query expansion failed, fallback to original query: %s", exc)
        return [normalized_query]


def _get_law_vectorstore() -> Chroma:
    from app.database.chroma_mgr import chroma_mgr

    return chroma_mgr.law_vectorstore


def _get_case_vectorstore() -> Chroma:
    from app.database.chroma_mgr import chroma_mgr

    if getattr(chroma_mgr, "case_vectorstore", None) is not None:
        return chroma_mgr.case_vectorstore

    return Chroma(
        collection_name=settings.CHROMA_CASES_COLLECTION,
        embedding_function=chroma_mgr.embeddings,
        persist_directory=str(resolve_persist_dir(settings.CHROMA_PERSIST_DIR)),
    )


def _build_candidate_id(content: str, source: str, metadata: dict[str, Any]) -> str:
    explicit_id = metadata.get("id") or metadata.get("document_id") or metadata.get("chunk_id")
    if isinstance(explicit_id, str) and explicit_id.strip():
        return explicit_id.strip()

    raw = json.dumps(
        {
            "source": source,
            "article": metadata.get("article"),
            "page": metadata.get("page"),
            "content": content,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _build_candidate(
    *,
    content: str,
    source: str,
    metadata: dict[str, Any],
    score: float,
    retrieval: str,
    matched_query: str,
) -> dict[str, Any]:
    candidate_id = _build_candidate_id(content, source, metadata)
    return {
        "candidate_id": candidate_id,
        "content": content,
        "source": source,
        "metadata": metadata,
        "score": score,
        "retrieval": retrieval,
        "matched_query": matched_query,
    }


def _collect_dense_candidates(query: str, vectorstore: Chroma) -> list[dict[str, Any]]:
    results = vectorstore.similarity_search_with_score(query, k=CHROMA_RECALL_K)
    candidates = []
    for document, score in results:
        metadata = document.metadata or {}
        content = (document.page_content or "").strip()
        source = str(metadata.get("source", "")).strip()
        if not content:
            continue
        candidates.append(
            _build_candidate(
                content=content,
                source=source,
                metadata=metadata,
                score=float(score),
                retrieval="dense",
                matched_query=query,
            )
        )
    return candidates


async def _collect_query_candidates(
    query: str,
    vectorstore: Chroma,
    corpus: str,
) -> list[dict[str, Any]]:
    dense_task = asyncio.to_thread(_collect_dense_candidates, query, vectorstore)
    sparse_task = asyncio.to_thread(
        BM25Retriever().search_records,
        query,
        BM25_RECALL_K,
        corpus,
    )
    dense_candidates, sparse_candidates = await asyncio.gather(dense_task, sparse_task)

    normalized_sparse_candidates = []
    for candidate in sparse_candidates:
        metadata = candidate.get("metadata") or {}
        content = (candidate.get("content") or "").strip()
        source = str(candidate.get("source", "")).strip()
        if not content:
            continue
        normalized_sparse_candidates.append(
            _build_candidate(
                content=content,
                source=source,
                metadata=metadata,
                score=float(candidate.get("score", 0.0)),
                retrieval="sparse",
                matched_query=query,
            )
        )

    return [*dense_candidates, *normalized_sparse_candidates]


async def _merge_hybrid_candidates(
    query: str,
    vectorstore: Chroma,
    corpus: str,
    chat_history: str = "",
) -> tuple[list[str], list[dict[str, Any]]]:
    expanded_queries = _expand_queries(query, chat_history=chat_history)
    retrieval_tasks = [
        _collect_query_candidates(expanded_query, vectorstore, corpus)
        for expanded_query in expanded_queries
    ]
    query_results = await asyncio.gather(*retrieval_tasks)

    merged_candidates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for candidates in query_results:
        for candidate in candidates:
            candidate_id = str(candidate.get("candidate_id", "")).strip()
            if not candidate_id or candidate_id in seen_ids:
                continue
            seen_ids.add(candidate_id)
            merged_candidates.append(candidate)

    return expanded_queries, merged_candidates


def _retrieve_candidates(
    query: str,
    vectorstore: Chroma,
    corpus: str,
    chat_history: str = "",
) -> tuple[list[str], list[dict[str, Any]]]:
    return asyncio.run(_merge_hybrid_candidates(query, vectorstore, corpus, chat_history=chat_history))


@tool
def search_law_articles(query: str, chat_history: str = "") -> str:
    """用于查询法律条文、法规规定、量刑标准等纯法律理论问题。"""
    expanded_queries, candidates = _retrieve_candidates(
        query,
        _get_law_vectorstore(),
        corpus="law",
        chat_history=chat_history,
    )
    queries = expanded_queries or [query.strip()]
    return _serialize_reranked_documents(queries, candidates, top_k=LAW_TOP_K)


@tool
def search_case_text(query: str, chat_history: str = "") -> str:
    """用于根据案情描述、作案手法等模糊信息查找相似的历史指导案例。"""
    expanded_queries, candidates = _retrieve_candidates(
        query,
        _get_case_vectorstore(),
        corpus="case",
        chat_history=chat_history,
    )
    queries = expanded_queries or [query.strip()]
    return _serialize_reranked_documents(queries, candidates, top_k=CASE_TOP_K)


@tool
def query_neo4j_graph(entity_name: str) -> str:
    """当你需要查询特定的原告、被告名字，或者特定的罪名/案由的具体判决结果时，必须调用此工具。"""
    tokens = _extract_graph_query_tokens(entity_name)
    if not tokens:
        return json.dumps([], ensure_ascii=False)

    driver = GraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
    )

    try:
        with driver.session() as session:
            records = session.run(GRAPH_QUERY, {"tokens": tokens}).data()
    finally:
        driver.close()

    return json.dumps(records, ensure_ascii=False)


AGENT_TOOLS = [search_law_articles, search_case_text, query_neo4j_graph]
