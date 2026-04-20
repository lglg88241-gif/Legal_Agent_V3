from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from langgraph.checkpoint.base import empty_checkpoint

from app.memory.redis_mgr import AsyncRedisSaver


class _LoopBoundRedisClient:
    def __init__(self, storage: dict[str, str]) -> None:
        self._storage = storage
        self._loop_id = id(asyncio.get_running_loop())

    def _assert_loop(self) -> None:
        if id(asyncio.get_running_loop()) != self._loop_id:
            raise RuntimeError("Event loop is closed")

    async def get(self, key: str):
        self._assert_loop()
        return self._storage.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._assert_loop()
        self._storage[key] = value

    async def aclose(self) -> None:
        return None


def test_async_redis_saver_rebinds_client_per_event_loop(monkeypatch) -> None:
    storage: dict[str, str] = {}

    def fake_from_url(*args, **kwargs):
        return _LoopBoundRedisClient(storage)

    monkeypatch.setattr("app.memory.redis_mgr.redis.from_url", fake_from_url)

    saver = AsyncRedisSaver()
    config = {"configurable": {"thread_id": "thread-1"}}

    async def _write() -> None:
        await saver.aput(
            config,
            empty_checkpoint(),
            {"source": "input", "step": -1},
            {},
        )

    async def _read():
        return await saver.aget_tuple(config)

    asyncio.run(_write())
    checkpoint_tuple = asyncio.run(_read())

    assert checkpoint_tuple is not None
    assert checkpoint_tuple.config == config

