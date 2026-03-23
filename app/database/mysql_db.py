# app/database/mysql_db.py
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

# 使用异步引擎
engine = create_async_engine(
    settings.MYSQL_URL, 
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True
)

# 异步会话工厂
AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

Base = declarative_base()

async def get_db():
    """FastAPI 依赖注入用"""
    async with AsyncSessionLocal() as session:
        yield session

async def init_db():
    """初始化数据库表结构"""
    async with engine.begin() as conn:
        # await conn.run_sync(Base.metadata.drop_all) # 危险操作，调试时慎用
        await conn.run_sync(Base.metadata.create_all)
    logger.info("✅ MySQL 异步数据库初始化完成")