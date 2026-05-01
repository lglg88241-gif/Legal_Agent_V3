# app/database/neo4j_mgr.py
import logging
import threading
from neo4j import GraphDatabase
from app.core.config import settings

logger = logging.getLogger(__name__)

class Neo4jManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(Neo4jManager, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
            
        self.uri = settings.NEO4J_URI
        self.user = settings.NEO4J_USER
        self.password = settings.NEO4J_PASSWORD
        self.driver = None
        
        try:
            self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
            self.driver.verify_connectivity()
            logger.info("✅ 成功连接到 Neo4j 数据库")
            self._initialized = True
        except Exception as e:
            logger.error(f"❌ Neo4j 连接失败: {e}")

    def close(self):
        if self.driver:
            self.driver.close()
            logger.info("🔌 Neo4j 连接已关闭")

    def query(self, cypher_query, parameters=None):
        if not self.driver:
            logger.warning("Neo4j driver not initialized.")
            return []
            
        with self.driver.session() as session:
            try:
                result = session.run(cypher_query, parameters)
                return [record.data() for record in result]
            except Exception as e:
                logger.error(f"Neo4j 查询执行失败: {e}")
                return []

    async def get_cross_references(self, law_ids=None, case_ids=None):
        """图谱交叉验证：异步获取关联的法条和判例 (这里保留了你的核心亮眼逻辑)"""
        import asyncio
        loop = asyncio.get_event_loop()
        
        query = """
        MATCH (n)-[:APPLY]-(m)
        WHERE (n:Law AND n.id IN $law_ids) OR (n:Case AND n.id IN $case_ids)
        RETURN DISTINCT m.id AS ref_id, labels(m)[0] AS type, m.name AS name
        """
        params = {
            "law_ids": law_ids or [],
            "case_ids": case_ids or []
        }
        
        def _run_query():
            return self.query(query, params)
            
        try:
            results = await loop.run_in_executor(None, _run_query)
            return results
        except Exception as e:
            logger.error(f"图谱交叉验证查询失败: {e}")
            return []

neo4j_mgr = Neo4jManager()