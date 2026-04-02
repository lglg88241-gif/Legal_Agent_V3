from __future__ import annotations

import json
import logging
from typing import Any

from langchain_chroma import Chroma
from langchain_core.tools import tool
from neo4j import GraphDatabase

from app.core.config import settings
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

CHROMA_RECALL_K = 10
LAW_TOP_K = 3
CASE_TOP_K = 2


def _serialize_documents(results: list[tuple[Any, float]]) -> str:
    payload = []
    for document, score in results:
        metadata = document.metadata or {}
        payload.append(
            {
                "content": document.page_content,
                "source": metadata.get("source", ""),
                "metadata": metadata,
                "score": score,
            }
        )
    return json.dumps(payload, ensure_ascii=False)


def _serialize_reranked_documents(query: str, results: list[tuple[Any, float]], top_k: int) -> str:
    if not results:
        return json.dumps([], ensure_ascii=False)

    try:
        reranker = BGEReranker()
        ranked = reranker.rerank_with_scores(
            query=query,
            documents=[document.page_content for document, _ in results],
            top_k=top_k,
        )
        payload = []
        for item in ranked:
            document, coarse_score = results[item["index"]]
            metadata = document.metadata or {}
            payload.append(
                {
                    "content": document.page_content,
                    "source": metadata.get("source", ""),
                    "metadata": metadata,
                    "score": item["score"],
                    "coarse_score": coarse_score,
                }
            )
        return json.dumps(payload, ensure_ascii=False)
    except Exception as exc:
        logger.warning("Reranker failed, fallback to coarse recall: %s", exc)
        return _serialize_documents(results[:top_k])


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


@tool
def search_law_articles(query: str) -> str:
    """用于查询法律条文、法规规定、量刑标准等纯法律理论问题。"""
    results = _get_law_vectorstore().similarity_search_with_score(query, k=CHROMA_RECALL_K)
    return _serialize_reranked_documents(query, results, top_k=LAW_TOP_K)


@tool
def search_case_text(query: str) -> str:
    """用于根据案情描述、作案手法等模糊信息查找相似的历史指导案例。"""
    results = _get_case_vectorstore().similarity_search_with_score(query, k=CHROMA_RECALL_K)
    return _serialize_reranked_documents(query, results, top_k=CASE_TOP_K)


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
