from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Literal, cast

from backend.kernel.contracts.events_schema import build_switch_command_signal, build_switch_state_event
from backend.platform.events.channels import CHANNEL_SWITCH_COMMANDS
from backend.platform.events.publisher import AsyncEventPublisher, event_bus_settings_from_env
from backend.platform.redis._shared import REDIS_OPERATION_ERRORS, AsyncRedisComponent
from backend.platform.redis.constants import CHANNEL_SWITCH_EVENTS, KEY_SWITCH_PREFIX
from backend.platform.redis.serialization import as_redis_hset_mapping
from backend.platform.redis.types import SwitchState

if TYPE_CHECKING:
    from backend.platform.redis.client import RedisClient


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
        for key, data in zip(keys, results, strict=False):
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
        normalized_state = str(state).strip().upper()
        if normalized_state not in {"ON", "OFF", "PENDING"}:
            self.logger.error("switches.set rejected invalid state for %s: %s", name, state)
            return False
        control_state = cast(Literal["ON", "OFF", "PENDING"], normalized_state)
        key = _switch_key(name)
        payload: dict[str, str] = {
            "state": normalized_state,
            "reason": reason,
            "updated_at": str(time.time()),
            "updated_by": updated_by,
        }
        control_event = build_switch_state_event(
            name,
            control_state,
            reason=reason,
            updated_by=updated_by,
            updated_at=float(payload["updated_at"]),
        )
        publisher = AsyncEventPublisher(
            settings=event_bus_settings_from_env(),
            redis=cast("RedisClient", self._owner),
            logger=self.logger,
        )
        try:
            await connection.hset(key, mapping=as_redis_hset_mapping(payload))
            if normalized_state in {"ON", "OFF"}:
                command_state = cast(Literal["ON", "OFF"], normalized_state)
                command_signal = build_switch_command_signal(
                    name,
                    command_state,
                    reason=reason,
                    updated_by=updated_by,
                    updated_at=float(payload["updated_at"]),
                )
                receiver_count = await publisher.publish_signal(CHANNEL_SWITCH_COMMANDS, json.dumps(command_signal))
                if receiver_count == 0:
                    self.logger.warning("switches.set stored state but no Redis coordination subscriber observed for %s", name)
            published = await publisher.publish_control(CHANNEL_SWITCH_EVENTS, json.dumps(control_event))
            if not published:
                self.logger.warning("switches.set stored state but control event publish did not complete for %s", name)
            return True
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("switches.set failed for %s: %s", name, exc, exc_info=True)
            return False
        finally:
            await publisher.close()


__all__ = ("RedisSwitchStore",)
