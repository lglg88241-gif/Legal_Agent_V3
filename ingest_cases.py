from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import stat
from pathlib import Path
from typing import Any

from langchain_chroma import Chroma
from langchain_community.chat_models import ChatZhipuAI
from langchain_community.document_loaders import PyPDFLoader
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from neo4j import GraphDatabase

from app.core.config import settings
from app.database.chroma_utils import PROJECT_ROOT, resolve_embedding_model_path, resolve_persist_dir

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

CORPUS_DIR = PROJECT_ROOT / "data" / "corpus"
CHROMA_CASES_COLLECTION = settings.CHROMA_CASES_COLLECTION
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100


def ensure_safe_workspace_dir(path: Path) -> None:
    if path.parent == path or path == PROJECT_ROOT:
        raise ValueError(f"Refusing to rebuild unsafe path: {path}")
    try:
        path.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise ValueError(f"Chroma persist directory must stay inside the project: {path}") from exc


def handle_remove_readonly(func, path, _exc_info) -> None:
    os.chmod(path, stat.S_IWRITE)
    func(path)


def rebuild_persist_dir(persist_dir: Path) -> None:
    ensure_safe_workspace_dir(persist_dir)
    if persist_dir.exists():
        shutil.rmtree(persist_dir, onerror=handle_remove_readonly)
    persist_dir.mkdir(parents=True, exist_ok=True)


def can_write_to_dir(path: Path) -> bool:
    probe_file = path / ".write_probe"
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe_file.write_text("ok", encoding="utf-8")
        probe_file.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def prepare_persist_dir(persist_dir: Path) -> None:
    ensure_safe_workspace_dir(persist_dir)
    if persist_dir.exists() and not can_write_to_dir(persist_dir):
        logger.warning("Detected a read-only or damaged Chroma directory, rebuilding: %s", persist_dir)
        rebuild_persist_dir(persist_dir)
    persist_dir.mkdir(parents=True, exist_ok=True)


def build_vector_id(source: str, page: int, chunk_index: int) -> str:
    digest = hashlib.sha1(f"{source}:{page}:{chunk_index}".encode("utf-8")).hexdigest()
    return f"case-{digest}"


def extract_pdf_chunks() -> tuple[list[Document], list[dict[str, Any]], int]:
    pdf_files = sorted(CORPUS_DIR.glob("*.pdf"))
    if not pdf_files:
        raise FileNotFoundError(f"No .pdf files found under {CORPUS_DIR}")

    splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    vector_documents: list[Document] = []
    graph_records: list[dict[str, Any]] = []

    for pdf_path in pdf_files:
        loader = PyPDFLoader(str(pdf_path))
        pages = loader.load()
        chunks = splitter.split_documents(pages)
        logger.info("Loaded %s pages from %s and generated %s chunks", len(pages), pdf_path.name, len(chunks))

        for chunk_index, chunk in enumerate(chunks, start=1):
            source_name = pdf_path.name
            page_number = int(chunk.metadata.get("page", 0)) + 1
            case_name = pdf_path.stem

            metadata = {
                "source": source_name,
                "case_name": case_name,
                "page": page_number,
                "chunk_index": chunk_index,
            }
            vector_documents.append(Document(page_content=chunk.page_content, metadata=metadata))
            graph_records.append(
                {
                    "source": source_name,
                    "case_name": case_name,
                    "page": page_number,
                    "chunk_index": chunk_index,
                    "text": chunk.page_content,
                }
            )

    return vector_documents, graph_records, len(pdf_files)


def init_embeddings() -> HuggingFaceEmbeddings:
    model_path, local_files_only = resolve_embedding_model_path(settings.EMBEDDING_MODEL_PATH)
    logger.info("Loading embedding model: %s", model_path)
    return HuggingFaceEmbeddings(
        model_name=model_path,
        model_kwargs={"device": "cpu", "local_files_only": local_files_only},
        encode_kwargs={"normalize_embeddings": True},
    )


def ingest_vectors(persist_dir: Path, vector_documents: list[Document], embeddings: HuggingFaceEmbeddings) -> None:
    ids = [
        build_vector_id(
            document.metadata["source"],
            document.metadata["page"],
            document.metadata["chunk_index"],
        )
        for document in vector_documents
    ]

    vectorstore = Chroma(
        collection_name=CHROMA_CASES_COLLECTION,
        embedding_function=embeddings,
        persist_directory=str(persist_dir),
    )
    vectorstore.reset_collection()
    vectorstore.add_documents(documents=vector_documents, ids=ids)


def build_extraction_prompt(record: dict[str, Any]) -> str:
    return f"""
你是法律案例信息抽取器。请阅读下面的案例文本切片，并严格只返回一个 JSON 对象。

要求：
1. 只能返回 JSON，不要输出解释、前后缀、Markdown 代码块。
2. 字段固定为：原告、被告、案由、核心判决。
3. 如果某字段无法从文本中确定，返回空字符串。
4. 核心判决请提炼成一句简洁中文。

案件名称：{record["case_name"]}
来源文件：{record["source"]}
页码：{record["page"]}

文本内容：
{record["text"]}
""".strip()


def extract_json_payload(content: Any) -> dict[str, str]:
    if isinstance(content, str):
        raw_text = content
    elif isinstance(content, list):
        raw_text = "".join(
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in content
        )
    else:
        raw_text = str(content)

    raw_text = raw_text.strip()
    raw_text = raw_text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    match = re.search(r"\{.*\}", raw_text, re.S)
    if not match:
        raise ValueError(f"No JSON object found in model response: {raw_text[:200]}")

    parsed = json.loads(match.group(0))
    return {
        "原告": str(parsed.get("原告", "") or "").strip(),
        "被告": str(parsed.get("被告", "") or "").strip(),
        "案由": str(parsed.get("案由", "") or "").strip(),
        "核心判决": str(parsed.get("核心判决", "") or "").strip(),
    }


def init_case_extractor() -> ChatZhipuAI | None:
    api_key = getattr(settings, "ZHIPUAI_API_KEY", "") or os.getenv("ZHIPUAI_API_KEY", "")
    if not api_key:
        logger.warning("ZHIPUAI_API_KEY is missing. Graph extraction will be skipped.")
        return None

    return ChatZhipuAI(api_key=api_key, model="glm-4-air", temperature=0, max_tokens=256)


def init_neo4j_driver():
    try:
        driver = GraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
        )
        driver.verify_connectivity()
        return driver
    except Exception as exc:
        logger.warning("Neo4j is unavailable. Graph ingestion will be skipped: %s", exc)
        return None


def upsert_graph_record(driver, record: dict[str, Any], payload: dict[str, str]) -> None:
    if driver is None:
        return

    cypher = """
    MERGE (c:Case {name: $case_name})
    SET c.source = $source
    FOREACH (_ IN CASE WHEN $plaintiff <> '' THEN [1] ELSE [] END |
        MERGE (p:Party {name: $plaintiff})
        MERGE (p)-[:PLAINTIFF_IN]->(c)
    )
    FOREACH (_ IN CASE WHEN $defendant <> '' THEN [1] ELSE [] END |
        MERGE (d:Party {name: $defendant})
        MERGE (d)-[:DEFENDANT_IN]->(c)
    )
    FOREACH (_ IN CASE WHEN $cause <> '' THEN [1] ELSE [] END |
        MERGE (cause:Cause {name: $cause})
        MERGE (c)-[:HAS_CAUSE]->(cause)
    )
    FOREACH (_ IN CASE WHEN $judgment <> '' THEN [1] ELSE [] END |
        MERGE (j:Judgment {content: $judgment})
        MERGE (c)-[:HAS_JUDGMENT]->(j)
    )
    """
    params = {
        "case_name": record["case_name"],
        "source": record["source"],
        "plaintiff": payload["原告"],
        "defendant": payload["被告"],
        "cause": payload["案由"],
        "judgment": payload["核心判决"],
    }

    with driver.session() as session:
        session.run(cypher, params).consume()


def ingest_graph(graph_records: list[dict[str, Any]]) -> tuple[int, int, int]:
    llm = init_case_extractor()
    driver = init_neo4j_driver()
    if llm is None or driver is None:
        if driver is not None:
            driver.close()
        return 0, 0, len(graph_records)

    success_count = 0
    llm_failures = 0
    graph_failures = 0

    try:
        for index, record in enumerate(graph_records, start=1):
            if index == 1 or index % 50 == 0:
                logger.info("Graph extraction progress: %s/%s chunks", index, len(graph_records))
            try:
                response = llm.invoke(build_extraction_prompt(record))
                payload = extract_json_payload(response.content)
            except Exception as exc:
                llm_failures += 1
                logger.warning(
                    "Skipping graph extraction for %s chunk %s due to LLM/JSON error: %s",
                    record["source"],
                    record["chunk_index"],
                    exc,
                )
                continue

            try:
                upsert_graph_record(driver, record, payload)
                success_count += 1
            except Exception as exc:
                graph_failures += 1
                logger.warning(
                    "Skipping Neo4j write for %s chunk %s due to graph error: %s",
                    record["source"],
                    record["chunk_index"],
                    exc,
                )
                continue
    finally:
        driver.close()

    return success_count, llm_failures, graph_failures


def main() -> None:
    persist_dir = resolve_persist_dir(settings.CHROMA_PERSIST_DIR)
    prepare_persist_dir(persist_dir)

    vector_documents, graph_records, pdf_count = extract_pdf_chunks()
    embeddings = init_embeddings()
    ingest_vectors(persist_dir, vector_documents, embeddings)

    graph_success, llm_failures, graph_failures = ingest_graph(graph_records)
    logger.info(
        "Processed %s PDF files, stored %s vector chunks into %s/%s, graph upserts=%s, llm_or_json_failures=%s, graph_failures=%s",
        pdf_count,
        len(vector_documents),
        persist_dir,
        CHROMA_CASES_COLLECTION,
        graph_success,
        llm_failures,
        graph_failures,
    )


if __name__ == "__main__":
    main()
