from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

import docx2txt
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings

from app.core.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

CORPUS_DIR = Path("data/corpus")
COLLECTION_NAME = "labor_law_collection"
ARTICLE_PATTERN = re.compile(
    r"(?m)^(第[零〇一二三四五六七八九十百千万两\d]+条(?:之[零〇一二三四五六七八九十百千万两\d]+)?)"
)


def resolve_embedding_model_path(model_name: str) -> tuple[str, bool]:
    configured_path = Path(model_name)
    if configured_path.exists():
        return str(configured_path), True

    hub_root = Path.home() / ".cache" / "huggingface" / "hub"
    repo_dir = hub_root / f"models--{model_name.replace('/', '--')}"
    ref_file = repo_dir / "refs" / "main"
    if ref_file.exists():
        snapshot_id = ref_file.read_text(encoding="utf-8").strip()
        snapshot_dir = repo_dir / "snapshots" / snapshot_id
        if snapshot_dir.exists():
            return str(snapshot_dir), True

    return model_name, False


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


def main() -> None:
    persist_dir = Path(settings.CHROMA_PERSIST_DIR)
    persist_dir.mkdir(parents=True, exist_ok=True)

    embedding_model_path, local_files_only = resolve_embedding_model_path(settings.EMBEDDING_MODEL_PATH)
    logger.info("加载词向量模型: %s", embedding_model_path)

    embeddings = HuggingFaceEmbeddings(
        model_name=embedding_model_path,
        model_kwargs={"device": "cpu", "local_files_only": local_files_only},
        encode_kwargs={"normalize_embeddings": True},
    )

    documents, file_count = build_documents(CORPUS_DIR)
    ids = build_document_ids(documents)

    vectorstore = Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=embeddings,
        persist_directory=str(persist_dir),
    )
    vectorstore.reset_collection()
    vectorstore.add_documents(documents=documents, ids=ids)

    logger.info(
        "成功处理了 %s 个文件，生成了 %s 个法条向量，并写入 %s/%s",
        file_count,
        len(documents),
        persist_dir,
        COLLECTION_NAME,
    )


if __name__ == "__main__":
    main()
