import asyncio
import logging
import threading

from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

from app.core.config import settings
from app.database.chroma_utils import resolve_embedding_model_path, resolve_persist_dir

logger = logging.getLogger(__name__)


class ChromaManager:
    """
    延迟初始化的 ChromaDB 管理器。

    模型权重（HuggingFaceEmbeddings）在首次实际调用时才加载，
    而非在模块 import / 进程启动时立即占用内存。
    这对 Celery Worker 进程尤其重要——worker 可以完成启动、
    注册任务、连接 broker，而不需要预先持有 ~1.5 GB 的向量模型。
    """

    _instance: "ChromaManager | None" = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "ChromaManager":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._initialized = False
                    instance._init_lock = threading.Lock()
                    instance._embeddings: HuggingFaceEmbeddings | None = None
                    instance._law_vectorstore: Chroma | None = None
                    instance._case_vectorstore: Chroma | None = None
                    cls._instance = instance
        return cls._instance

    def _ensure_initialized(self) -> None:
        """仅在首次实际访问时才加载模型（双重检查加锁）。"""
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            logger.info("Lazy-initializing ChromaDB embedding model (first access)")
            try:
                persist_directory = str(resolve_persist_dir(settings.CHROMA_PERSIST_DIR))
                embedding_model_path, local_files_only = resolve_embedding_model_path(
                    settings.EMBEDDING_MODEL_PATH
                )
                self._embeddings = HuggingFaceEmbeddings(
                    model_name=embedding_model_path,
                    model_kwargs={"device": "cpu", "local_files_only": local_files_only},
                    encode_kwargs={"normalize_embeddings": True},
                )
                self._law_vectorstore = Chroma(
                    collection_name=settings.CHROMA_COLLECTION_NAME,
                    embedding_function=self._embeddings,
                    persist_directory=persist_directory,
                )
                self._case_vectorstore = Chroma(
                    collection_name=settings.CHROMA_CASES_COLLECTION,
                    embedding_function=self._embeddings,
                    persist_directory=persist_directory,
                )
                self._initialized = True
                logger.info("ChromaDB collections are ready")
            except Exception as exc:
                logger.error("Failed to initialize ChromaDB: %s", exc)
                raise

    @property
    def embeddings(self) -> HuggingFaceEmbeddings:
        self._ensure_initialized()
        return self._embeddings  # type: ignore[return-value]

    @property
    def law_vectorstore(self) -> Chroma:
        self._ensure_initialized()
        return self._law_vectorstore  # type: ignore[return-value]

    @property
    def case_vectorstore(self) -> Chroma:
        self._ensure_initialized()
        return self._case_vectorstore  # type: ignore[return-value]

    async def async_similarity_search(self, query: str, k: int = 5):
        law_task = asyncio.to_thread(self.law_vectorstore.similarity_search, query, k=k)
        case_task = asyncio.to_thread(self.case_vectorstore.similarity_search, query, k=k)
        law_docs, case_docs = await asyncio.gather(law_task, case_task)
        return {"laws": law_docs, "cases": case_docs}


chroma_mgr = ChromaManager()
