from __future__ import annotations

import threading
from typing import Any

from neo4j import GraphDatabase

from app.core.config import settings

_graph_singleton: "ArticleReferenceGraph | None" = None
_graph_lock = threading.Lock()


class ArticleReferenceGraph:
    def __init__(self, neo4j_driver: Any):
        self._driver = neo4j_driver

    def expand(
        self,
        seeds: list[tuple[str, int]],
        max_neighbors: int = 20,
    ) -> list[tuple[str, int]]:
        if not seeds:
            return []

        rows = [
            {"law_title": law_title, "article_num": int(article_num)}
            for law_title, article_num in seeds
            if law_title and article_num is not None
        ]
        if not rows:
            return []

        query = """
        UNWIND $seeds AS seed
        MATCH (a:LawArticle {law_title: seed.law_title, article_num: seed.article_num})-[:REFERENCES]-(b:LawArticle)
        RETURN DISTINCT b.law_title AS law_title, b.article_num AS article_num
        LIMIT $max_neighbors
        """

        neighbors: list[tuple[str, int]] = []
        seen: set[tuple[str, int]] = set(seeds)
        with self._driver.session() as session:
            records = session.run(query, seeds=rows, max_neighbors=max_neighbors)
            for record in records:
                law_title = str(record.get("law_title") or "").strip()
                article_num = record.get("article_num")
                if not law_title or not isinstance(article_num, int):
                    continue
                item = (law_title, article_num)
                if item in seen:
                    continue
                seen.add(item)
                neighbors.append(item)
        return neighbors


def get_article_graph() -> ArticleReferenceGraph:
    global _graph_singleton

    if _graph_singleton is None:
        with _graph_lock:
            if _graph_singleton is None:
                driver = GraphDatabase.driver(
                    settings.NEO4J_URI,
                    auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
                )
                _graph_singleton = ArticleReferenceGraph(driver)
    return _graph_singleton
