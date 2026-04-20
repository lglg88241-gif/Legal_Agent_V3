import asyncio
import json
import logging
from typing import Optional, Dict, Any

import redis.asyncio as redis
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)

from app.core.config import settings

logger = logging.getLogger(__name__)


class AsyncRedisSaver(BaseCheckpointSaver):
    """
    基于 Redis 的 LangGraph checkpoint 存储器，带弹性降级保护。

    当 Redis 因内存压力触发 MISCONF（bgsave 失败锁死写入）或其他
    ResponseError 时，读写操作会被静默跳过并记录 WARNING，
    而不是让整个 Celery 任务链崩溃。

    这是一种"有损可用"策略：checkpoint 不可用时 agent 仍可正常
    无状态地完成当次对话，不影响主业务流程。
    """

    def __init__(self):
        super().__init__()
        self.redis_url = settings.REDIS_URL
        self._client: redis.Redis | None = None
        self._client_loop_id: int | None = None
        self.ttl = 3600

    async def _get_client(self) -> redis.Redis:
        loop_id = id(asyncio.get_running_loop())
        if self._client is None or self._client_loop_id != loop_id:
            if self._client is not None:
                try:
                    await self._client.aclose()
                except Exception:
                    pass
            self._client = redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=3,
                retry_on_timeout=False,
            )
            self._client_loop_id = loop_id
        return self._client

    def reset_client_for_new_loop(self) -> None:
        """Forget any cached client so the next loop creates its own Redis handle."""
        self._client = None
        self._client_loop_id = None

    async def aget_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        try:
            client = await self._get_client()
            thread_id = config["configurable"]["thread_id"]
            data = await client.get(f"checkpoint:{thread_id}")
            if data:
                parsed = json.loads(data)
                return CheckpointTuple(
                    config,
                    Checkpoint(**parsed["checkpoint"]),
                    CheckpointMetadata(**parsed.get("metadata", {})),
                )
        except Exception as exc:
            # Redis MISCONF / 连接失败时降级为无状态模式
            logger.warning("Redis checkpoint read failed (degraded mode): %s", exc)
        return None

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: Dict[str, str],
    ) -> RunnableConfig:
        try:
            client = await self._get_client()
            thread_id = config["configurable"]["thread_id"]
            data = {"checkpoint": checkpoint, "metadata": metadata}
            await client.setex(
                f"checkpoint:{thread_id}",
                self.ttl,
                json.dumps(data, default=str),
            )
        except Exception as exc:
            # Redis 写入失败时静默跳过，不阻塞 agent 正常响应
            logger.warning("Redis checkpoint write failed (degraded mode): %s", exc)
        return config

    async def alist(self, config: RunnableConfig, **kwargs):
        """列举历史 checkpoint，失败时返回空迭代器。"""
        try:
            client = await self._get_client()
            thread_id = config["configurable"]["thread_id"]
            data = await client.get(f"checkpoint:{thread_id}")
            if data:
                parsed = json.loads(data)
                yield CheckpointTuple(
                    config,
                    Checkpoint(**parsed["checkpoint"]),
                    CheckpointMetadata(**parsed.get("metadata", {})),
                )
        except Exception as exc:
            logger.warning("Redis checkpoint list failed (degraded mode): %s", exc)

    async def aput_writes(self, config: RunnableConfig, writes, task_id: str) -> None:
        """中间写入失败时静默跳过。"""
        try:
            client = await self._get_client()
            thread_id = config["configurable"]["thread_id"]
            key = f"checkpoint_writes:{thread_id}:{task_id}"
            await client.setex(key, self.ttl, json.dumps(writes, default=str))
        except Exception as exc:
            logger.warning("Redis checkpoint writes failed (degraded mode): %s", exc)


memory_mgr = AsyncRedisSaver()


def get_redis_saver_for_current_loop() -> AsyncRedisSaver:
    """
    Return the shared checkpoint saver after clearing any stale loop binding.

    Celery tasks use ``asyncio.run(...)`` repeatedly, so a Redis client created in
    a previous event loop must never be reused in the next one.
    """
    memory_mgr.reset_client_for_new_loop()
    return memory_mgr
