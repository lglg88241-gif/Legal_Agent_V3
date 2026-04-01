from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import stat
from pathlib import Path

import docx2txt
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings

from app.core.config import settings
from app.database.chroma_utils import PROJECT_ROOT, resolve_embedding_model_path, resolve_persist_dir

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

CORPUS_DIR = PROJECT_ROOT / "data" / "corpus"
ARTICLE_PATTERN = re.compile(
    r"(?m)^(第[零〇一二三四五六七八九十百千万两\d]+条(?:之[零〇一二三四五六七八九十百千万两\d]+)?)"
)


def ensure_safe_workspace_dir(path: Path) -> None:
    if path.parent == path or path == PROJECT_ROOT:
        raise ValueError(f"拒绝重建危险路径: {path}")
    try:
        path.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise ValueError(f"Chroma 持久化目录必须位于项目根目录内: {path}") from exc


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
        logger.warning("检测到不可写或损坏的 Chroma 目录，准备重建: %s", persist_dir)
        rebuild_persist_dir(persist_dir)

    persist_dir.mkdir(parents=True, exist_ok=True)
    if not can_write_to_dir(persist_dir):
        raise PermissionError(f"Chroma 持久化目录不可写: {persist_dir}")


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_law_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return fallback


def split_articles(text: str) -> list[tuple[str, str]]:
    matches = list(ARTICLE_PATTERN.finditer(text))
    articles: list[tuple[str, str]] = []

    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        article_label = match.group(1)
        article_text = text[start:end].strip()
        if article_text:
            articles.append((article_label, article_text))

    return articles


def build_documents(corpus_dir: Path) -> tuple[list[Document], int]:
    docx_files = sorted(corpus_dir.glob("*.docx"))
    if not docx_files:
        raise FileNotFoundError(f"未在 {corpus_dir} 找到任何 .docx 文件")

    documents: list[Document] = []

    for path in docx_files:
        raw_text = docx2txt.process(path)
        normalized_text = normalize_text(raw_text)
        law_title = extract_law_title(normalized_text, path.stem)
        articles = split_articles(normalized_text)

        if not articles:
            raise ValueError(f"{path.name} 未匹配到任何法条，请检查文本格式或正则规则")

        logger.info("已处理文件 %s，识别出 %s 条法条", path.name, len(articles))

        for article_label, article_text in articles:
            documents.append(
                Document(
                    page_content=f"{law_title}\n\n{article_text}",
                    metadata={
                        "source": path.name,
                        "article": article_label,
                        "law_title": law_title,
                    },
                )
            )

    return documents, len(docx_files)


def build_document_ids(documents: list[Document]) -> list[str]:
    ids: list[str] = []
    for document in documents:
        source = document.metadata["source"]
        article = document.metadata["article"]
        digest = hashlib.sha1(f"{source}:{article}".encode("utf-8")).hexdigest()
        ids.append(f"law-{digest}")
    return ids


def is_readonly_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "readonly" in message or "read-only" in message


def ingest_documents(
    persist_dir: Path,
    documents: list[Document],
    ids: list[str],
    embeddings: HuggingFaceEmbeddings,
) -> None:
    try:
        vectorstore = Chroma(
            collection_name=settings.CHROMA_COLLECTION_NAME,
            embedding_function=embeddings,
            persist_directory=str(persist_dir),
        )
        vectorstore.reset_collection()
        vectorstore.add_documents(documents=documents, ids=ids)
    except Exception as exc:
        if not is_readonly_error(exc):
            raise
        logger.warning("检测到只读的 Chroma 数据目录，正在重建后重试: %s", persist_dir)
        rebuild_persist_dir(persist_dir)
        vectorstore = Chroma(
            collection_name=settings.CHROMA_COLLECTION_NAME,
            embedding_function=embeddings,
            persist_directory=str(persist_dir),
        )
        vectorstore.reset_collection()
        vectorstore.add_documents(documents=documents, ids=ids)


def main() -> None:
    persist_dir = resolve_persist_dir(settings.CHROMA_PERSIST_DIR)
    prepare_persist_dir(persist_dir)

    embedding_model_path, local_files_only = resolve_embedding_model_path(settings.EMBEDDING_MODEL_PATH)
    logger.info("加载词向量模型: %s", embedding_model_path)

    embeddings = HuggingFaceEmbeddings(
        model_name=embedding_model_path,
        model_kwargs={"device": "cpu", "local_files_only": local_files_only},
        encode_kwargs={"normalize_embeddings": True},
    )

    documents, file_count = build_documents(CORPUS_DIR)
    ids = build_document_ids(documents)
    ingest_documents(persist_dir, documents, ids, embeddings)

    logger.info(
        "成功处理了 %s 个文件，生成了 %s 个法条向量，并写入 %s/%s",
        file_count,
        len(documents),
        persist_dir,
        settings.CHROMA_COLLECTION_NAME,
    )


if __name__ == "__main__":
    main()
