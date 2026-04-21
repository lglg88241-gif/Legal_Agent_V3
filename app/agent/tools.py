from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
from types import SimpleNamespace
from typing import Any

from json_repair import repair_json
from langchain_chroma import Chroma
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from neo4j import GraphDatabase

from app.core.bm25_retriever import BM25Retriever
from app.agent.article_graph import get_article_graph
from app.agent.guards import _chinese_to_arabic
from app.core.config import settings
from app.core.llm_factory import create_cloud_llm, create_query_expansion_llm
from app.core.reranker import BGEReranker
from app.agent.recall_booster import boost_law_recall
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

CHROMA_RECALL_K = 10
BM25_RECALL_K = 10
LAW_RERANK_K = 10
LAW_TOP_K = 5
CASE_TOP_K = 2

QUERY_EXPANSION_SYSTEM_PROMPT = """
你是一个法律检索的查询扩展助手。你的任务是把用户的口语化问题扩展为 3-5 个语义相近的检索查询。

你必须先做指代消解，再做语义重写。

【核心保留原则】
扩展 query 必须保留原问题中已经出现的核心法律要素关键词，不能把关键要素扩写丢失。
常见要素包括但不限于：未签劳动合同、违法解除、违法辞退、经济补偿、赔偿金、社保、公积金、加班费、试用期、工伤、欠薪。
原问题中出现了哪些要素，扩展后的每一条 query 都必须至少覆盖其中一个，不得把“权益清单”类问题偷换成单一的“仲裁流程”或“欠薪处理”问题。

【绝对禁止】
1. 严禁编造或推测任何法律条文编号，如“第四十六条”“第82条”。
2. 严禁猜测法律条文内容。
3. 只允许扩展自然语言关键词、同义词、相关法律概念。

【正确示例 1】
用户：公司拖欠工资 3 个月怎么办
输出：
["拖欠工资 劳动者权利 维权途径", "用人单位未支付工资 法律责任", "工资支付保障 劳动监察 投诉", "欠薪 劳动仲裁 申请条件", "工资追讨 法律救济"]

【正确示例 2】
用户：被公司突然辞退能要哪些赔偿
输出：
["违法辞退 赔偿项目 计算方式", "解除劳动合同 经济补偿金 赔偿金", "未提前通知辞退 劳动者救济", "违法解除劳动合同 仲裁请求", "辞退争议 劳动仲裁 证据准备"]

【正确示例 3】
用户：工作5年，月薪12000，没签合同，违法辞退，没缴社保，能主张什么
输出：
["未签劳动合同 二倍工资差额", "违法解除劳动合同 赔偿金 2N", "未缴社保 补缴 投诉", "经济补偿金 工作年限", "劳动者权益 主张范围"]

【错误示例（严禁）】
- "《劳动合同法》第八十五条 拖欠工资的赔偿"
- "《劳动法》第五十条 工资支付规定"

要求：
1. 只输出 JSON 数组，不要输出对象。
2. 生成 3 到 5 个互补查询，覆盖核心案由、法律概念、法律关系和维权路径。
3. 如果原问题中同时出现多个法律要素，例如“未签合同 + 违法辞退 + 社保 + 权益清单”，扩展结果必须把这些要素拆开覆盖，不能全部收缩成单一问题。
4. 结合传入的历史对话和用户最新回复进行指代消解。由于用户的回复可能极其简短，请先将“最新回复”代入“历史对话”的语境中还原完整案情，再提取法律专业查询词。
5. 如果用户只说“是”“对”“没有”“全责”这类短句，必须利用历史对话做指代消解，不能只围绕短句表面含义扩写。
6. 只输出 JSON 数组，不要任何解释、markdown、思考过程。
""".strip()

_query_expansion_llm: Any | None = None
_query_expansion_llm_lock = threading.Lock()
_query_expansion_fallback_llm: Any | None = None
_query_expansion_fallback_llm_lock = threading.Lock()
_ARTICLE_NUMBER_QUERY_PATTERN = re.compile(r"第[一二三四五六七八九十百零\d]+条")
_ARTICLE_LABEL_PATTERN = re.compile(r"^第([一二三四五六七八九十百千万零两\d]+)条$")


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
    rerank_k: int | None = None,
    boosted_minimum: int = 0,
) -> str:
    if not candidates:
        return json.dumps([], ensure_ascii=False)

    try:
        reranker = BGEReranker()
        ranked = reranker.rerank_with_scores(
            query=query,
            documents=[candidate["content"] for candidate in candidates],
            top_k=rerank_k or top_k,
        )
        ranked_subset = ranked[: max(rerank_k or top_k, top_k)]
        if boosted_minimum > 0:
            boosted_ranked = [
                item for item in ranked_subset
                if candidates[item["index"]].get("retrieval") == "boost"
            ]
            selected_indices = {
                id(item): item
                for item in boosted_ranked[:boosted_minimum]
            }
            for item in ranked_subset:
                selected_indices.setdefault(id(item), item)
                if len(selected_indices) >= top_k:
                    break
            selected_ranked = [item for item in ranked_subset if id(item) in selected_indices][:top_k]
        else:
            selected_ranked = ranked_subset[:top_k]
        payload = []
        for item in selected_ranked:
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
        fallback_limit = rerank_k or top_k
        for candidate in candidates[:fallback_limit][:top_k]:
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
                        "请先完成指代消解，再输出 3-5 个检索查询。"
                        "只输出 JSON 数组，不要输出对象，不要输出解释。"
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


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id", "")).strip()
        if not candidate_id or candidate_id in seen_ids:
            continue
        seen_ids.add(candidate_id)
        deduped.append(candidate)

    return deduped


def _parse_article_num_from_label(article_label: str | None) -> int | None:
    if not article_label:
        return None
    cleaned = str(article_label).strip()
    match = _ARTICLE_LABEL_PATTERN.match(cleaned)
    if not match:
        return None
    raw = match.group(1)
    if raw.isdigit():
        return int(raw)
    return _chinese_to_arabic(raw)


def _to_chinese_article_label(article_num: int) -> str:
    digits = "零一二三四五六七八九"

    def _int_to_chinese(num: int) -> str:
        if num < 10:
            return digits[num]
        if num < 20:
            return "十" if num == 10 else f"十{digits[num % 10]}"
        if num < 100:
            tens, ones = divmod(num, 10)
            return f"{digits[tens]}十" if ones == 0 else f"{digits[tens]}十{digits[ones]}"
        if num < 1000:
            hundreds, remainder = divmod(num, 100)
            prefix = f"{digits[hundreds]}百"
            if remainder == 0:
                return prefix
            if remainder < 10:
                return f"{prefix}零{digits[remainder]}"
            return f"{prefix}{_int_to_chinese(remainder)}"
        thousands, remainder = divmod(num, 1000)
        prefix = f"{digits[thousands]}千"
        if remainder == 0:
            return prefix
        if remainder < 100:
            return f"{prefix}零{_int_to_chinese(remainder)}"
        return f"{prefix}{_int_to_chinese(remainder)}"

    return f"第{_int_to_chinese(int(article_num))}条"


def _extract_seeds_from_candidates(candidates: list[dict[str, Any]]) -> list[tuple[str, int]]:
    seeds: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()

    for candidate in candidates:
        metadata = candidate.get("metadata") or {}
        law_title = str(metadata.get("law_title") or "").strip()
        article_num = _parse_article_num_from_label(metadata.get("article"))
        if not law_title or article_num is None:
            continue
        item = (law_title, article_num)
        if item in seen:
            continue
        seen.add(item)
        seeds.append(item)

    return seeds


def _coerce_exact_documents(documents: list[str], metadatas: list[dict[str, Any]]) -> list[Any]:
    exact_documents: list[Any] = []
    for document, metadata in zip(documents, metadatas):
        if not isinstance(document, str) or not document.strip():
            continue
        exact_documents.append(
            SimpleNamespace(
                page_content=document,
                metadata=dict(metadata or {}),
            )
        )
    return exact_documents


def _fetch_articles_by_metadata(article_refs: list[tuple[str, int]]) -> list[dict[str, Any]]:
    vectorstore = _get_law_vectorstore()
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()

    for law_title, article_num in article_refs:
        if not law_title or article_num is None:
            continue
        key = (law_title, int(article_num))
        if key in seen:
            continue
        seen.add(key)

        article_label = _to_chinese_article_label(int(article_num))
        exact_documents: list[Any] = []
        try:
            payload = vectorstore.get(
                where={"$and": [{"law_title": law_title}, {"article": article_label}]},
                limit=1,
                include=["documents", "metadatas"],
            )
            exact_documents = _coerce_exact_documents(
                payload.get("documents") or [],
                payload.get("metadatas") or [],
            )
        except Exception as exc:
            logger.debug(
                "Metadata lookup failed for [%s %s], fallback to similarity search: %s",
                law_title,
                article_label,
                exc,
            )

        if not exact_documents:
            try:
                results = vectorstore.similarity_search_with_score(
                    f"{law_title} {article_label}",
                    k=3,
                )
            except Exception as exc:
                logger.debug("Similarity fallback failed for [%s %s]: %s", law_title, article_label, exc)
                results = []

            for document, _score in results:
                metadata = document.metadata or {}
                if (
                    str(metadata.get("law_title") or "").strip() == law_title
                    and str(metadata.get("article") or "").strip() == article_label
                ):
                    exact_documents = [document]
                    break

        if not exact_documents:
            continue

        metadata = exact_documents[0].metadata if isinstance(getattr(exact_documents[0], "metadata", None), dict) else {}
        content = (getattr(exact_documents[0], "page_content", "") or "").strip()
        source = str(metadata.get("source", "")).strip()
        if not content:
            continue
        candidates.append(
            _build_candidate(
                content=content,
                source=source,
                metadata=dict(metadata),
                score=1.0,
                retrieval="boost",
                matched_query=f"{law_title} {article_label}",
            )
        )

    return candidates


def _dedupe_by_metadata(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, int] | str] = set()

    for candidate in candidates:
        metadata = candidate.get("metadata") or {}
        law_title = str(metadata.get("law_title") or "").strip()
        article_num = _parse_article_num_from_label(metadata.get("article"))
        if law_title and article_num is not None:
            key: tuple[str, int] | str = (law_title, article_num)
        else:
            key = str(candidate.get("candidate_id", "")).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)

    return deduped


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

    for candidates in query_results:
        merged_candidates.extend(candidates)

    return expanded_queries, _dedupe_candidates(merged_candidates)


def _retrieve_candidates(
    query: str,
    vectorstore: Chroma,
    corpus: str,
    chat_history: str = "",
) -> tuple[list[str], list[dict[str, Any]]]:
    return asyncio.run(_merge_hybrid_candidates(query, vectorstore, corpus, chat_history=chat_history))


def _search_law_documents_for_boost(query: str, top_k: int = 1) -> list[Any]:
    vectorstore = _get_law_vectorstore()
    exact_match = re.match(
        r"^(?P<law>.+?)\s+(?P<article>第[一二三四五六七八九十百千万零两\d]+条)$",
        (query or "").strip(),
    )
    if exact_match:
        law_title = exact_match.group("law").strip()
        article = exact_match.group("article").strip()
        try:
            payload = vectorstore.get(
                where={"$and": [{"law_title": law_title}, {"article": article}]},
                include=["documents", "metadatas"],
            )
            documents = payload.get("documents") or []
            metadatas = payload.get("metadatas") or []
            exact_documents = []
            for document, metadata in zip(documents, metadatas):
                if not isinstance(document, str) or not document.strip():
                    continue
                exact_documents.append(
                    SimpleNamespace(
                        page_content=document,
                        metadata=dict(metadata or {}),
                    )
                )
            if exact_documents:
                return exact_documents[:top_k]
        except Exception as exc:
            logger.debug("Exact booster lookup failed for [%s]: %s", query, exc)

    sparse_hits = BM25Retriever().search_records(query=query, top_k=max(top_k, 10), corpus="law")
    sparse_documents = []
    for hit in sparse_hits:
        content = hit.get("content")
        metadata = hit.get("metadata") or {}
        if not isinstance(content, str) or not content.strip():
            continue
        sparse_documents.append(
            SimpleNamespace(
                page_content=content,
                metadata=dict(metadata),
            )
        )
    if sparse_documents:
        return sparse_documents[:top_k]

    results = vectorstore.similarity_search_with_score(query, k=top_k)
    return [document for document, _score in results]


def _coerce_boosted_law_candidates(boosted_documents: list[Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for document in boosted_documents:
        metadata = document.metadata if isinstance(getattr(document, "metadata", None), dict) else {}
        content = (getattr(document, "page_content", "") or "").strip()
        source = str(metadata.get("source", "")).strip()
        if not content:
            continue
        candidates.append(
            _build_candidate(
                content=content,
                source=source,
                metadata=metadata,
                score=1.0,
                retrieval="boost",
                matched_query=str(metadata.get("_booster_query") or "").strip() or source or "boost",
            )
        )
    return candidates


@tool
def search_law_articles(query: str, chat_history: str = "") -> str:
    """用于查询法律条文、法规规定、量刑标准等纯法律理论问题。"""
    expanded_queries, base_candidates = _retrieve_candidates(
        query,
        _get_law_vectorstore(),
        corpus="law",
        chat_history=chat_history,
    )
    boosted_documents = boost_law_recall(
        query,
        expanded_queries,
        search_func=_search_law_documents_for_boost,
    )
    boosted_candidates = _coerce_boosted_law_candidates(boosted_documents)

    graph_candidates: list[dict[str, Any]] = []
    try:
        seeds = _extract_seeds_from_candidates(base_candidates)
        neighbors = get_article_graph().expand(seeds, max_neighbors=20)
        graph_candidates = _fetch_articles_by_metadata(neighbors)
    except Exception as exc:
        logger.warning("article graph expansion failed: %s, fallback to base only", exc)

    merged_candidates = _dedupe_by_metadata([*graph_candidates, *boosted_candidates, *base_candidates])
    rerank_query = query.strip() or " ".join(expanded_queries).strip()
    return _serialize_reranked_documents(
        rerank_query,
        merged_candidates,
        top_k=LAW_TOP_K,
        rerank_k=LAW_RERANK_K,
        boosted_minimum=min(
            3,
            len([candidate for candidate in merged_candidates if candidate.get("retrieval") == "boost"]),
        ),
    )


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
