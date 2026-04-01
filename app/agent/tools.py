from __future__ import annotations

import json
from typing import Any

from langchain_chroma import Chroma
from langchain_core.tools import tool
from neo4j import GraphDatabase

from app.core.config import settings
from app.database.chroma_utils import resolve_persist_dir

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


def _serialize_documents(results: list[tuple[Any, float]]) -> str:
    payload = []
    for document, score in results:
        payload.append(
            {
                "content": document.page_content,
                "metadata": document.metadata,
                "score": score,
            }
        )
    return json.dumps(payload, ensure_ascii=False)


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
    results = _get_law_vectorstore().similarity_search_with_score(query, k=4)
    return _serialize_documents(results)


@tool
def search_case_text(query: str) -> str:
    """用于根据案情描述、作案手法等模糊信息查找相似的历史指导案例。"""
    results = _get_case_vectorstore().similarity_search_with_score(query, k=4)
    return _serialize_documents(results)


@tool
def query_neo4j_graph(entity_name: str) -> str:
    """当你需要查询特定的原告、被告名字，或者特定的罪名/案由（如“逃税”、“劳动争议”）的具体判决结果时，必须调用此工具。"""
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
