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
from app.core.llm_factory import create_cloud_llm, create_query_expansion_llm
from app.core.reranker import BGEReranker
from app.database.chroma_utils import resolve_persist_dir

logger = logging.getLogger(__name__)

GRAPH_QUERY = """
MATCH (n)-[r]-(m)
WHERE coalesce(n.name, '') CONTAINS $entity_name
   OR coalesce(n.content, '') CONTAINS $entity_name
   OR coalesce(m.name, '') CONTAINS $entity_name
   OR coalesce(m.content, '') CONTAINS $entity_name
RETURN labels(n) AS source_labels,
       properties(n) AS source_node,
       type(r) AS relation,
       labels(m) AS target_labels,
       properties(m) AS target_node
LIMIT 5
"""

QUERY_EXPANSION_SYSTEM_PROMPT = """
你是一个法律检索扩写助手。你必须先做指代消解，再做语义重写。
要求：
1. 只返回 JSON，格式必须是 {"queries": ["查询1", "查询2", "查询3"]}。
2. 生成 3 到 5 个互补查询，覆盖核心案由、法条关键词和法律关系。
3. 结合传入的历史对话和用户最新回复进行指代消解。由于用户的回复可能极其简短，请先将“最新回复”代入“历史对话”的语境中还原完整案情，再提取法律专业查询词。
4. 如果用户只说“是”“对”“没有”“全责”这类短句，必须利用历史对话做指代消解，不能只围绕短句表面含义扩写。
5. 输出必须是 JSON，不要解释。
""".strip()

CHROMA_RECALL_K = 10
BM25_RECALL_K = 10
LAW_TOP_K = 3
CASE_TOP_K = 2

_query_expansion_llm: Any | None = None
_query_expansion_llm_lock = threading.Lock()
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


def _serialize_reranked_documents(
    query: str,
    candidates: list[dict[str, Any]],
    top_k: int,
) -> str:
    if not candidates:
        return json.dumps([], ensure_ascii=False)

    try:
        reranker = BGEReranker()
        ranked = reranker.rerank_with_scores(
            query=query,
            documents=[candidate["content"] for candidate in candidates],
            top_k=top_k,
        )
        payload = []
        for item in ranked:
            candidate = candidates[item["index"]]
            metadata = candidate.get("metadata") or {}
            payload.append(
                {
                    "content": candidate["content"],
                    "source": metadata.get("source", ""),
                    "metadata": metadata,
                    "score": item["score"],
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


def _get_query_expansion_llm() -> Any:
    global _query_expansion_llm

    if _query_expansion_llm is None:
        with _query_expansion_llm_lock:
            if _query_expansion_llm is None:
                _query_expansion_llm = create_query_expansion_llm()
    return _query_expansion_llm


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
        try:
            response = _get_query_expansion_llm().invoke(messages)
        except Exception as exc:
            logger.warning("Primary query expansion LLM failed, fallback to cloud: %s", exc)
            response = _get_query_expansion_fallback_llm().invoke(messages)
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
    rerank_query = " ".join(expanded_queries).strip() or query.strip()
    return _serialize_reranked_documents(rerank_query, candidates, top_k=LAW_TOP_K)


@tool
def search_case_text(query: str, chat_history: str = "") -> str:
    """用于根据案情描述、作案手法等模糊信息查找相似的历史指导案例。"""
    expanded_queries, candidates = _retrieve_candidates(
        query,
        _get_case_vectorstore(),
        corpus="case",
        chat_history=chat_history,
    )
    rerank_query = " ".join(expanded_queries).strip() or query.strip()
    return _serialize_reranked_documents(rerank_query, candidates, top_k=CASE_TOP_K)


@tool
def query_neo4j_graph(entity_name: str) -> str:
    """当你需要查询特定的原告、被告名字，或者特定的罪名/案由的具体判决结果时，必须调用此工具。"""
    driver = GraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
    )

    try:
        with driver.session() as session:
            records = session.run(GRAPH_QUERY, {"entity_name": entity_name}).data()
    finally:
        driver.close()

    return json.dumps(records, ensure_ascii=False)


AGENT_TOOLS = [search_law_articles, search_case_text, query_neo4j_graph]
