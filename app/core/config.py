# app/core/config.py
import os
from dotenv import load_dotenv

# 加载项目根目录的 .env 文件
load_dotenv()

class Settings:
    # LLM & API 配置
    LLM_MODE = os.getenv("LLM_MODE", "mock")
    API_KEY = os.getenv("API_KEY", "EMPTY")
    BASE_URL = os.getenv("BASE_URL", "http://localhost:8000/v1")
    
    # Neo4j 配置
    NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
    NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
    
    # MySQL 配置
    MYSQL_URL = os.getenv("MYSQL_URL", "mysql+aiomysql://root:password@localhost:3307/legal_db")
    
    # Redis 配置
    REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    
    # ChromaDB & 向量模型配置
    CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", "./chroma_data")
    EMBEDDING_MODEL_PATH = os.getenv("EMBEDDING_MODEL_PATH", "BAAI/bge-small-zh-v1.5")
    
    # 精排模型配置
    RERANKER_MODEL_PATH = os.getenv("RERANKER_MODEL_PATH", "BAAI/bge-reranker-v2-m3")

# 实例化单例配置对象供全局调用
settings = Settings()