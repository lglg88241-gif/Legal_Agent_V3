import asyncio
import logging

from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

from app.core.config import settings
from app.database.chroma_utils import resolve_embedding_model_path, resolve_persist_dir

logger = logging.getLogger(__name__)


class ChromaManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ChromaManager, cls).__new__(cls)
            cls._instance._init_db()
        return cls._instance

    def _init_db(self):
        logger.info("Initializing ChromaDB with the configured embedding model")
        try:
            persist_directory = str(resolve_persist_dir(settings.CHROMA_PERSIST_DIR))
            embedding_model_path, local_files_only = resolve_embedding_model_path(
                settings.EMBEDDING_MODEL_PATH
            )
            self.embeddings = HuggingFaceEmbeddings(
                model_name=embedding_model_path,
                model_kwargs={"device": "cpu", "local_files_only": local_files_only},
                encode_kwargs={"normalize_embeddings": True},
            )

            self.law_vectorstore = Chroma(
                collection_name=settings.CHROMA_COLLECTION_NAME,
                embedding_function=self.embeddings,
                persist_directory=persist_directory,
            )

            self.case_vectorstore = Chroma(
                collection_name=settings.CHROMA_CASES_COLLECTION,
                embedding_function=self.embeddings,
                persist_directory=persist_directory,
            )
            logger.info("ChromaDB collections are ready")
        except Exception as exc:
            logger.error("Failed to initialize ChromaDB: %s", exc)
            raise

    async def async_similarity_search(self, query: str, k: int = 5):
        law_task = asyncio.to_thread(self.law_vectorstore.similarity_search, query, k=k)
        case_task = asyncio.to_thread(self.case_vectorstore.similarity_search, query, k=k)

        law_docs, case_docs = await asyncio.gather(law_task, case_task)
        return {"laws": law_docs, "cases": case_docs}


chroma_mgr = ChromaManager()
