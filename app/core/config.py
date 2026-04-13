import os

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from dotenv import load_dotenv

load_dotenv()

class Settings:
    LLM_MODE = os.getenv("LLM_MODE", "mock")
    API_KEY = os.getenv("API_KEY", "EMPTY")
    BASE_URL = os.getenv("BASE_URL", "http://localhost:8001/v1")
    LLM_MODEL_NAME = os.getenv("LLM_MODEL_NAME", "Qwen/Qwen1.5-1.8B-Chat-AWQ")

    NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
    NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "12345678")
    POSTGRES_URL = os.getenv("POSTGRES_URL", "")
    MYSQL_URL = os.getenv("MYSQL_URL", "mysql+aiomysql://root:200467@127.0.0.1:3306/legal_db")
    DATABASE_URL = os.getenv("DATABASE_URL") or POSTGRES_URL or MYSQL_URL
    REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", "./chroma_data")
    CHROMA_COLLECTION_NAME = os.getenv("CHROMA_COLLECTION_NAME", "legal_knowledge_base")
    CHROMA_CASES_COLLECTION = os.getenv("CHROMA_CASES_COLLECTION", "legal_cases_collection")
    EMBEDDING_MODEL_PATH = os.getenv("EMBEDDING_MODEL_PATH", "BAAI/bge-small-zh-v1.5")
    RERANKER_MODEL_PATH = os.getenv("RERANKER_MODEL_PATH", "BAAI/bge-reranker-v2-m3")
    ZHIPUAI_API_KEY = os.getenv("ZHIPUAI_API_KEY", "")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "change-this-secret-in-production")
    JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
    JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "1440"))

settings = Settings()
