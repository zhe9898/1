from __future__ import annotations

import json
import time

from backend.platform.redis._shared import REDIS_OPERATION_ERRORS, AsyncRedisComponent
from backend.platform.redis.constants import CHANNEL_SWITCH_EVENTS, KEY_SWITCH_PREFIX
from backend.platform.redis.serialization import as_redis_hset_mapping
from backend.platform.redis.types import SwitchState


def _switch_key(name: str) -> str:
    return f"{KEY_SWITCH_PREFIX}{name}"


class RedisSwitchStore(AsyncRedisComponent):
    async def get(self, name: str) -> SwitchState | None:
        connection = await self._connection()
        if connection is None:
            return None
        key = _switch_key(name)

        async def _get() -> SwitchState | None:
            data = await connection.hgetall(key)
            if not data:
                return None
            return {
                "state": data.get("state", ""),
                "reason": data.get("reason"),
                "updated_at": float(data.get("updated_at", 0)),
                "updated_by": data.get("updated_by"),
            }

        return await self._retry_once(_get, None, f"switches.get({name})")

    async def get_all(self) -> dict[str, SwitchState]:
        connection = await self._connection()
        if connection is None:
            return {}
        try:
            keys = [key async for key in connection.scan_iter(f"{KEY_SWITCH_PREFIX}*", count=100)]
            if not keys:
                return {}
            pipe = connection.pipeline()
            for key in keys:
                pipe.hgetall(key)
            results = await pipe.execute()
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("switches.get_all failed: %s", exc, exc_info=True)
            return {}

        out: dict[str, SwitchState] = {}
        prefix_length = len(KEY_SWITCH_PREFIX)
        for key, data in zip(keys, results):
            if not data:
                continue
            name = key[prefix_length:] if key.startswith(KEY_SWITCH_PREFIX) else key.split(":")[-1]
            out[name] = {
                "state": data.get("state", ""),
                "reason": data.get("reason"),
                "updated_at": float(data.get("updated_at", 0)),
                "updated_by": data.get("updated_by"),
            }
        return out

    async def set(
        self,
        name: str,
        state: str,
        *,
        reason: str = "",
        updated_by: str = "system",
    ) -> bool:
        connection = await self._connection()
        if connection is None:
            return False
        key = _switch_key(name)
        payload: dict[str, str] = {
            "state": state,
            "reason": reason,
            "updated_at": str(time.time()),
            "updated_by": updated_by,
        }
        event: dict[str, object] = {"switch": name, "name": name, **payload}
        try:
            pipe = connection.pipeline()
            pipe.hset(key, mapping=as_redis_hset_mapping(payload))
            pipe.publish(CHANNEL_SWITCH_EVENTS, json.dumps(event))
            await pipe.execute()
            return True
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("switches.set failed for %s: %s", name, exc, exc_info=True)
            return False


__all__ = ("RedisSwitchStore",)
