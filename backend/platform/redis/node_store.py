from __future__ import annotations

import json
import time
from typing import cast

from backend.platform.redis._shared import AsyncRedisComponent, REDIS_OPERATION_ERRORS
from backend.platform.redis.constants import KEY_NODE_PREFIX
from backend.platform.redis.serialization import as_redis_hset_mapping, node_to_redis, redis_to_node
from backend.platform.redis.types import NodeInfo


class RedisNodeStore(AsyncRedisComponent):
    async def register(self, node_id: str, info: NodeInfo) -> bool:
        connection = await self._connection()
        if connection is None:
            return False
        key = f"{KEY_NODE_PREFIX}{node_id}"
        try:
            info_dict: dict[str, object] = dict(info)
            info_dict["last_seen"] = time.time()
            mapping = node_to_redis(cast(NodeInfo, info_dict))
            await connection.hset(key, mapping=as_redis_hset_mapping(mapping))
            await connection.expire(key, 60)
            return True
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("nodes.register failed for %s: %s", node_id, exc, exc_info=True)
            return False

    async def get(self, node_id: str) -> NodeInfo | None:
        connection = await self._connection()
        if connection is None:
            return None
        key = f"{KEY_NODE_PREFIX}{node_id}"
        try:
            data = await connection.hgetall(key)
            if not data:
                return None
            return redis_to_node(data)
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("nodes.get failed for %s: %s", node_id, exc, exc_info=True)
            return None

    async def get_all(self) -> dict[str, NodeInfo]:
        connection = await self._connection()
        if connection is None:
            return {}
        try:
            keys = [key async for key in connection.scan_iter(f"{KEY_NODE_PREFIX}*", count=100)]
            if not keys:
                return {}
            pipe = connection.pipeline()
            for key in keys:
                pipe.hgetall(key)
            results = await pipe.execute()
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("nodes.get_all failed: %s", exc, exc_info=True)
            return {}

        node_ids = [key[len(KEY_NODE_PREFIX) :] for key in keys]
        out: dict[str, NodeInfo] = {}
        for node_id, data in zip(node_ids, results):
            if not data:
                continue
            try:
                out[node_id] = redis_to_node(data)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self.logger.warning("Failed to parse node %s: %s", node_id, exc)
        return out

    async def heartbeat(self, node_id: str, load: dict[str, float]) -> bool:
        connection = await self._connection()
        if connection is None:
            return False
        key = f"{KEY_NODE_PREFIX}{node_id}"
        try:
            pipe = connection.pipeline()
            pipe.hset(key, "last_seen", str(time.time()))
            pipe.hset(key, "load", json.dumps(load))
            pipe.expire(key, 60)
            await pipe.execute()
            return True
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("nodes.heartbeat failed for %s: %s", node_id, exc, exc_info=True)
            return False


__all__ = ("RedisNodeStore",)
