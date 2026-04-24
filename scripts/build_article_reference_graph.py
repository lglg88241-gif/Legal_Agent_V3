from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Iterable

from langchain_chroma import Chroma
from neo4j import GraphDatabase

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings
from app.database.chroma_utils import resolve_persist_dir

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

REFERENCE_PATTERN = re.compile(
    r"(?:本法|本条例|本规定|前款|本款)?第([零一二三四五六七八九十百千两\d]+)条"
)
ARTICLE_LABEL_PATTERN = re.compile(r"^第([零一二三四五六七八九十百千两\d]+)条")
TARGET_NEIGHBOR_LAW = "中华人民共和国劳动合同法"
TARGET_NEIGHBOR_ARTICLE_NUM = 87
WRITE_BATCH_SIZE = 500


def chinese_to_arabic(raw: str | None) -> int | None:
    if not raw:
        return None

    stripped = re.sub(r"\s+", "", raw)
    if not stripped:
        return None
    if stripped.isdigit():
        return int(stripped)

    digits = {
        "零": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    units = {"十": 10, "百": 100, "千": 1000}

    total = 0
    current = 0
    has_token = False
    for char in stripped:
        if char in digits:
            current = digits[char]
            has_token = True
            continue
        if char in units:
            total += (current or 1) * units[char]
            current = 0
            has_token = True
            continue
        return None

    if not has_token:
        return None
    return total + current


def parse_article_num(article_label: str | None) -> int | None:
    if not article_label:
        return None
    match = ARTICLE_LABEL_PATTERN.search(article_label.strip())
    if not match:
        return None
    return chinese_to_arabic(match.group(1))


def iter_batches(rows: list[dict], batch_size: int = WRITE_BATCH_SIZE) -> Iterable[list[dict]]:
    for start in range(0, len(rows), batch_size):
        yield rows[start : start + batch_size]


def get_law_vectorstore() -> Chroma:
    persist_dir = resolve_persist_dir(settings.CHROMA_PERSIST_DIR)
    return Chroma(
        collection_name=settings.CHROMA_COLLECTION_NAME,
        persist_directory=str(persist_dir),
    )


def load_law_article_chunks() -> list[dict[str, str | int]]:
    vectorstore = get_law_vectorstore()
    payload = vectorstore.get(include=["documents", "metadatas"])
    documents = payload.get("documents") or []
    metadatas = payload.get("metadatas") or [{} for _ in documents]

    rows: list[dict[str, str | int]] = []
    for document, metadata in zip(documents, metadatas):
        if not isinstance(document, str) or not document.strip():
            continue
        safe_metadata = metadata if isinstance(metadata, dict) else {}
        article_label = str(safe_metadata.get("article") or "").strip()
        law_title = str(safe_metadata.get("law_title") or "").strip()
        article_num = parse_article_num(article_label)
        if not law_title or not article_label or article_num is None:
            continue
        rows.append(
            {
                "law_title": law_title,
                "article_label": article_label,
                "article_num": article_num,
                "content": document.strip(),
            }
        )

    return rows


def build_nodes_and_edges(chunks: list[dict[str, str | int]]) -> tuple[list[dict], list[dict], dict[str, int]]:
    node_index: dict[tuple[str, int], dict] = {}
    max_article_num_by_law: dict[str, int] = {}

    for chunk in chunks:
        law_title = str(chunk["law_title"])
        article_num = int(chunk["article_num"])
        article_label = str(chunk["article_label"])
        node_index[(law_title, article_num)] = {
            "law_title": law_title,
            "article_num": article_num,
            "article_label": article_label,
        }
        max_article_num_by_law[law_title] = max(max_article_num_by_law.get(law_title, 0), article_num)

    edge_index: set[tuple[str, int, str, int]] = set()
    edge_rows: list[dict] = []

    for chunk in chunks:
        law_title = str(chunk["law_title"])
        article_num = int(chunk["article_num"])
        content = str(chunk["content"])
        max_article_num = max_article_num_by_law.get(law_title, 0)

        for match in REFERENCE_PATTERN.finditer(content):
            target_num = chinese_to_arabic(match.group(1))
            if target_num is None:
                continue
            if target_num == article_num:
                continue
            if max_article_num and target_num > max_article_num:
                continue

            target_key = (law_title, target_num)
            if target_key not in node_index:
                continue

            edge_key = (law_title, article_num, law_title, target_num)
            if edge_key in edge_index:
                continue
            edge_index.add(edge_key)
            edge_rows.append(
                {
                    "source_law_title": law_title,
                    "source_article_num": article_num,
                    "target_law_title": law_title,
                    "target_article_num": target_num,
                }
            )

    return list(node_index.values()), edge_rows, max_article_num_by_law


def clear_old_graph(driver) -> None:
    print("WARNING: deleting existing LawArticle nodes and REFERENCES edges before rebuild")
    with driver.session() as session:
        session.run("MATCH (n:LawArticle) DETACH DELETE n").consume()


def ensure_constraints(driver) -> None:
    with driver.session() as session:
        session.run(
            """
            CREATE CONSTRAINT law_article_unique IF NOT EXISTS
            FOR (n:LawArticle)
            REQUIRE (n.law_title, n.article_num) IS UNIQUE
            """
        ).consume()


def write_nodes(driver, rows: list[dict]) -> None:
    query = """
    UNWIND $rows AS row
    MERGE (n:LawArticle {law_title: row.law_title, article_num: row.article_num})
    SET n.article_label = row.article_label
    """
    with driver.session() as session:
        for batch in iter_batches(rows):
            session.run(query, rows=batch).consume()


def write_edges(driver, rows: list[dict]) -> None:
    query = """
    UNWIND $rows AS row
    MATCH (a:LawArticle {law_title: row.source_law_title, article_num: row.source_article_num})
    MATCH (b:LawArticle {law_title: row.target_law_title, article_num: row.target_article_num})
    MERGE (a)-[:REFERENCES]->(b)
    """
    with driver.session() as session:
        for batch in iter_batches(rows):
            session.run(query, rows=batch).consume()


def fetch_top_indegree(driver) -> list[dict]:
    with driver.session() as session:
        records = session.run(
            """
            MATCH (n:LawArticle)
            OPTIONAL MATCH (n)<-[:REFERENCES]-(m:LawArticle)
            RETURN n.law_title AS law_title,
                   n.article_label AS article_label,
                   n.article_num AS article_num,
                   count(m) AS indegree
            ORDER BY indegree DESC, law_title, article_num
            LIMIT 5
            """
        )
        return [record.data() for record in records]


def fetch_neighbors(driver, law_title: str, article_num: int) -> list[dict]:
    with driver.session() as session:
        records = session.run(
            """
            MATCH (a:LawArticle {law_title: $law_title, article_num: $article_num})-[:REFERENCES]-(b:LawArticle)
            RETURN b.law_title AS law_title,
                   b.article_label AS article_label,
                   b.article_num AS article_num
            ORDER BY b.article_num
            """,
            law_title=law_title,
            article_num=article_num,
        )
        return [record.data() for record in records]


def fetch_counts(driver) -> tuple[int, int]:
    with driver.session() as session:
        node_count = session.run("MATCH (n:LawArticle) RETURN count(n) AS count").single()["count"]
        edge_count = session.run("MATCH ()-[r:REFERENCES]->() RETURN count(r) AS count").single()["count"]
    return int(node_count), int(edge_count)


def print_stats(driver) -> None:
    node_count, edge_count = fetch_counts(driver)
    print(f"LawArticle 节点数: {node_count}")
    print(f"REFERENCES 边数: {edge_count}")

    print("入度 top 5:")
    for index, row in enumerate(fetch_top_indegree(driver), start=1):
        print(
            f"  {index}. {row['law_title']}{row['article_label']} "
            f"(article_num={row['article_num']}, indegree={row['indegree']})"
        )

    print(f"{TARGET_NEIGHBOR_LAW}第八十七条的所有邻居:")
    neighbors = fetch_neighbors(driver, TARGET_NEIGHBOR_LAW, TARGET_NEIGHBOR_ARTICLE_NUM)
    if not neighbors:
        print("  (none)")
        return
    for row in neighbors:
        print(f"  - {row['law_title']}{row['article_label']} (article_num={row['article_num']})")


def main() -> None:
    logger.info("Loading law article chunks from Chroma collection: %s", settings.CHROMA_COLLECTION_NAME)
    chunks = load_law_article_chunks()
    if not chunks:
        raise RuntimeError("No law article chunks found in vector store")

    nodes, edges, max_article_num_by_law = build_nodes_and_edges(chunks)
    logger.info("Prepared %s LawArticle nodes from %s chunks", len(nodes), len(chunks))
    logger.info("Prepared %s REFERENCES edges", len(edges))
    logger.info("Detected %s laws with max article bounds", len(max_article_num_by_law))

    driver = GraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
    )
    try:
        clear_old_graph(driver)
        ensure_constraints(driver)
        write_nodes(driver, nodes)
        write_edges(driver, edges)
        print_stats(driver)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
