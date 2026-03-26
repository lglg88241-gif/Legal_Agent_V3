# app/database/chroma_mgr.py
import logging
import asyncio
# 🌟 换成全新的 langchain_chroma，消除烦人的警告
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from app.core.config import settings

logger = logging.getLogger(__name__)

class ChromaManager:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ChromaManager, cls).__new__(cls)
            cls._instance._init_db()
        return cls._instance
        
    def _init_db(self):
        logger.info("⏳ 正在初始化 ChromaDB 与 BGE 嵌入模型...")
        try:
            self.embeddings = HuggingFaceEmbeddings(
                model_name=settings.EMBEDDING_MODEL_PATH,
                model_kwargs={'device': 'cpu'}, # 如果有GPU可改为cuda
                encode_kwargs={'normalize_embeddings': True}
            )
            
            # 双 Collection 架构
            self.law_vectorstore = Chroma(
                collection_name="labor_law_collection",
                embedding_function=self.embeddings,
                persist_directory=settings.CHROMA_PERSIST_DIR
            )
            
            self.case_vectorstore = Chroma(
                collection_name="legal_cases_collection",
                embedding_function=self.embeddings,
                persist_directory=settings.CHROMA_PERSIST_DIR
            )
            logger.info("✅ ChromaDB 双 Collection 加载完成")
        except Exception as e:
            logger.error(f"❌ ChromaDB 初始化失败: {e}")
            raise e

    async def async_similarity_search(self, query: str, k: int = 5):
        """核心性能优化点：并发检索法条与判例"""
        law_task = asyncio.to_thread(self.law_vectorstore.similarity_search, query, k=k)
        case_task = asyncio.to_thread(self.case_vectorstore.similarity_search, query, k=k)
        
        law_docs, case_docs = await asyncio.gather(law_task, case_task)
        return {"laws": law_docs, "cases": case_docs}

chroma_mgr = ChromaManager()