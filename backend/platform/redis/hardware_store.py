from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, cast

from backend.kernel.contracts.events_schema import build_hardware_state_event
from backend.platform.events.publisher import AsyncEventPublisher, event_bus_settings_from_env
from backend.platform.redis._shared import REDIS_OPERATION_ERRORS, AsyncRedisComponent
from backend.platform.redis.constants import CHANNEL_HARDWARE_EVENTS, KEY_HW_PREFIX
from backend.platform.redis.serialization import as_redis_hset_mapping
from backend.platform.redis.types import HardwareState

if TYPE_CHECKING:
    from backend.platform.redis.client import RedisClient


def _hardware_key(path: str) -> str:
    return f"{KEY_HW_PREFIX}{path}"


class RedisHardwareStore(AsyncRedisComponent):
    async def get(self, path: str) -> HardwareState | None:
        connection = await self._connection()
        if connection is None:
            return None
        key = _hardware_key(path)
        try:
            data = await connection.hgetall(key)
            if not data:
                return None
            return {
                "path": data.get("path", path),
                "uuid": data.get("uuid"),
                "state": data.get("state", ""),
                "timestamp": float(data.get("timestamp", 0)),
                "reason": data.get("reason"),
            }
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("hardware.get failed for %s: %s", path, exc, exc_info=True)
            return None

    async def set(
        self,
        path: str,
        state: str,
        *,
        reason: str = "",
        uuid_val: str | None = None,
    ) -> bool:
        connection = await self._connection()
        if connection is None:
            return False
        key = _hardware_key(path)
        ts = time.time()
        payload: dict[str, str] = {
            "path": path,
            "uuid": uuid_val or "",
            "state": state,
            "timestamp": str(ts),
            "reason": reason,
        }
        event = build_hardware_state_event(path, state, reason=reason, uuid_val=uuid_val, timestamp=ts)
        publisher = AsyncEventPublisher(
            settings=event_bus_settings_from_env(),
            redis=cast("RedisClient", self._owner),
            logger=self.logger,
        )
        try:
            await connection.hset(key, mapping=as_redis_hset_mapping(payload))
            published = await publisher.publish_control(CHANNEL_HARDWARE_EVENTS, json.dumps(event))
            if not published:
                self.logger.warning("hardware.set stored state but control event publish did not complete for %s", path)
            return True
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("hardware.set failed for %s: %s", path, exc, exc_info=True)
            return False
        finally:
            await publisher.close()


__all__ = ("RedisHardwareStore",)
