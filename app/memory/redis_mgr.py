import json
import logging
import redis.asyncio as redis
from typing import Optional, Dict, Any
from langgraph.checkpoint.base import BaseCheckpointSaver, Checkpoint, CheckpointMetadata, CheckpointTuple
from langchain_core.runnables import RunnableConfig
from app.core.config import settings

logger = logging.getLogger(__name__)

class AsyncRedisSaver(BaseCheckpointSaver):
    def __init__(self):
        super().__init__()
        self.redis_url = settings.REDIS_URL
        self._client = None
        self.ttl = 3600
        
    async def _get_client(self):
        if self._client is None:
            self._client = redis.from_url(self.redis_url, decode_responses=True)
        return self._client
    
    async def aget_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        client = await self._get_client()
        thread_id = config["configurable"]["thread_id"]
        data = await client.get(f"checkpoint:{thread_id}")
        if data:
            try:
                parsed = json.loads(data)
                return CheckpointTuple(config, Checkpoint(**parsed["checkpoint"]), CheckpointMetadata(**parsed.get("metadata", {})))
            except: pass
        return None

    async def aput(self, config: RunnableConfig, checkpoint: Checkpoint, metadata: CheckpointMetadata, new_versions: Dict[str, str]) -> RunnableConfig:
        client = await self._get_client()
        thread_id = config["configurable"]["thread_id"]
        data = {"checkpoint": checkpoint, "metadata": metadata}
        await client.setex(f"checkpoint:{thread_id}", self.ttl, json.dumps(data, default=str))
        return config

memory_mgr = AsyncRedisSaver()
