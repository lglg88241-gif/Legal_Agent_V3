"""
Surgery #14: 为现有 DynamicCaseEntity 节点回填 user_id。
从 MySQL conversations 表读取 conversation_id → user_id 映射，
然后更新 Neo4j 中对应的 DynamicCaseEntity 节点。

用法:
    D:/Dev/python310/python.exe scripts/backfill_graph_user_id.py
"""
from __future__ import annotations

import os
import sys

from neo4j import GraphDatabase

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings


def main() -> None:
    print("[backfill] 开始回填 user_id...")

    driver = GraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
    )
    with driver.session() as session:
        records = session.run(
            """
            MATCH (n:DynamicCaseEntity)
            WHERE n.conversation_id IS NOT NULL AND (n.user_id IS NULL OR n.user_id = 0)
            RETURN DISTINCT n.conversation_id AS conversation_id
            """
        ).data()

    conversation_ids = [record["conversation_id"] for record in records]
    print(f"[backfill] 找到 {len(conversation_ids)} 个待回填的 conversation_id")

    if not conversation_ids:
        print("[backfill] 无需回填")
        driver.close()
        return

    try:
        import pymysql

        db_url = settings.DATABASE_URL.replace("mysql+aiomysql://", "")
        user_pass, host_db = db_url.split("@", 1)
        db_user, db_pass = user_pass.split(":", 1)
        host_port, db_name = host_db.split("/", 1)
        db_host, db_port = host_port.split(":", 1)

        conn = pymysql.connect(
            host=db_host,
            port=int(db_port),
            user=db_user,
            password=db_pass,
            database=db_name,
        )
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, user_id FROM conversations WHERE id IN (%s)"
            % ",".join(["%s"] * len(conversation_ids)),
            conversation_ids,
        )
        mapping = {row[0]: row[1] for row in cursor.fetchall()}
        cursor.close()
        conn.close()
        print(f"[backfill] 从 MySQL 获取到 {len(mapping)} 个映射")
    except ImportError:
        print("[backfill] ⚠️  pymysql 未安装，尝试使用默认 user_id=1")
        mapping = {cid: 1 for cid in conversation_ids}
    except Exception as exc:
        print(f"[backfill] ⚠️  MySQL 查询失败: {exc}，使用默认 user_id=1")
        mapping = {cid: 1 for cid in conversation_ids}

    updated = 0
    with driver.session() as session:
        for conv_id, user_id in mapping.items():
            result = session.run(
                """
                MATCH (n:DynamicCaseEntity {conversation_id: $conversation_id})
                WHERE n.user_id IS NULL OR n.user_id = 0
                SET n.user_id = $user_id
                RETURN count(n) AS updated
                """,
                {"conversation_id": conv_id, "user_id": user_id},
            ).single()
            count = result["updated"] if result else 0
            updated += count
            if count > 0:
                print(f"  conversation_id={conv_id} → user_id={user_id}: 更新 {count} 个节点")

        for conv_id, user_id in mapping.items():
            session.run(
                """
                MATCH ()-[r:CASE_RELATION {conversation_id: $conversation_id}]->()
                WHERE r.user_id IS NULL OR r.user_id = 0
                SET r.user_id = $user_id
                """,
                {"conversation_id": conv_id, "user_id": user_id},
            ).consume()

    driver.close()
    print(f"[backfill] ✅ 完成，共更新 {updated} 个节点")


if __name__ == "__main__":
    main()
