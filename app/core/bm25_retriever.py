from __future__ import annotations

import os
import pickle
import threading
from typing import Any

import jieba
from rank_bm25 import BM25Okapi

from app.core.config import settings
from app.database.chroma_utils import resolve_persist_dir

INDEX_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "bm25_cache.pkl",
)


class BM25Retriever:
    _instance: "BM25Retriever | None" = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "BM25Retriever":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._indexes: dict[str, dict[str, Any]] = {}
                    cls._instance._index_lock = threading.Lock()
                    cls._instance._cache_lock = threading.Lock()
        return cls._instance

    def _get_vectorstore(self, corpus: str):
        from app.database.chroma_mgr import chroma_mgr
        from langchain_chroma import Chroma

        if corpus == "law":
            return chroma_mgr.law_vectorstore

        if getattr(chroma_mgr, "case_vectorstore", None) is not None:
            return chroma_mgr.case_vectorstore

        return Chroma(
            collection_name=settings.CHROMA_CASES_COLLECTION,
            embedding_function=chroma_mgr.embeddings,
            persist_directory=str(resolve_persist_dir(settings.CHROMA_PERSIST_DIR)),
        )

    def _build_index(self, corpus: str) -> dict[str, Any]:
        vectorstore = self._get_vectorstore(corpus)
        payload = vectorstore.get(include=["documents", "metadatas"])
        documents = payload.get("documents") or []
        metadatas = payload.get("metadatas") or [{} for _ in documents]

        records = []
        tokenized_corpus = []

        for document, metadata in zip(documents, metadatas):
            content = (document or "").strip()
            if not content:
                continue

            safe_metadata = metadata or {}
            records.append(
                {
                    "content": content,
                    "metadata": safe_metadata,
                    "source": safe_metadata.get("source", ""),
                }
            )
            tokenized_corpus.append(jieba.lcut(content))

        return {
            "bm25": BM25Okapi(tokenized_corpus) if tokenized_corpus else None,
            "records": records,
        }

    def _load_cached_indexes(self) -> None:
        if self._indexes:
            return
        if not os.path.exists(INDEX_PATH):
            return

        with self._cache_lock:
            if self._indexes or not os.path.exists(INDEX_PATH):
                return
            with open(INDEX_PATH, "rb") as cache_file:
                cached_indexes = pickle.load(cache_file)
            if isinstance(cached_indexes, dict):
                self._indexes = cached_indexes

    def _save_cached_indexes(self) -> None:
        with self._cache_lock:
            with open(INDEX_PATH, "wb") as cache_file:
                pickle.dump(self._indexes, cache_file)

    def _ensure_index(self, corpus: str) -> dict[str, Any]:
        self._load_cached_indexes()
        if corpus not in self._indexes:
            with self._index_lock:
                self._load_cached_indexes()
                if corpus not in self._indexes:
                    self._indexes[corpus] = self._build_index(corpus)
                    self._save_cached_indexes()
        return self._indexes[corpus]

    def search(self, query: str, top_k: int = 10, corpus: str = "law") -> list[str]:
        records = self.search_records(query=query, top_k=top_k, corpus=corpus)
        return [record["content"] for record in records]

    def search_records(
        self,
        query: str,
        top_k: int = 10,
        corpus: str = "law",
    ) -> list[dict[str, Any]]:
        normalized_query = query.strip()
        if not normalized_query:
            return []

        index_bundle = self._ensure_index(corpus)
        bm25 = index_bundle["bm25"]
        records = index_bundle["records"]

        if bm25 is None or not records:
            return []

        tokenized_query = jieba.lcut(normalized_query)
        scores = bm25.get_scores(tokenized_query)
        ranked_indices = sorted(
            range(len(scores)),
            key=lambda idx: float(scores[idx]),
            reverse=True,
        )[:top_k]

        return [
            {
                **records[index],
                "score": float(scores[index]),
                "retrieval": "sparse",
            }
            for index in ranked_indices
        ]
