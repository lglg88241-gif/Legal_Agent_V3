from __future__ import annotations

import threading
from typing import Any

from sentence_transformers import CrossEncoder

from app.core.config import settings
from app.database.chroma_utils import resolve_embedding_model_path


class BGEReranker:
    _instance: "BGEReranker | None" = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "BGEReranker":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._model = None
                    cls._instance._model_lock = threading.Lock()
                    cls._instance._predict_lock = threading.Lock()
        return cls._instance

    def _get_model(self) -> CrossEncoder:
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    model_path, local_files_only = resolve_embedding_model_path(
                        settings.RERANKER_MODEL_PATH
                    )
                    self._model = CrossEncoder(
                        model_name_or_path=model_path,
                        local_files_only=local_files_only,
                    )
        return self._model

    def rerank(self, query: str, documents: list[str], top_k: int = 3) -> list[str]:
        ranked = self.rerank_with_scores(query, documents, top_k=top_k)
        return [item["document"] for item in ranked]

    def rerank_with_scores(
        self,
        query: str,
        documents: list[str],
        top_k: int = 3,
    ) -> list[dict[str, Any]]:
        if not query.strip() or not documents:
            return []

        model = self._get_model()
        pairs = [[query, document] for document in documents]

        with self._predict_lock:
            raw_scores = model.predict(pairs)

        scored_results = [
            {
                "index": index,
                "document": document,
                "score": float(score),
            }
            for index, (document, score) in enumerate(zip(documents, raw_scores))
        ]
        scored_results.sort(key=lambda item: item["score"], reverse=True)
        return scored_results[:top_k]
