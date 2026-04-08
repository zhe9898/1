from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from backend.platform.redis._shared import AsyncRedisComponent, REDIS_OPERATION_ERRORS


class RedisStreamAdapter(AsyncRedisComponent):
    async def xadd(
        self,
        stream: str,
        fields: Mapping[str, Any],
        *,
        maxlen: int | None = None,
        approximate: bool | None = None,
    ) -> str | None:
        connection = await self._connection()
        if connection is None:
            return None
        kwargs: dict[str, Any] = {}
        if maxlen is not None:
            kwargs["maxlen"] = maxlen
        if approximate is not None:
            kwargs["approximate"] = approximate
        try:
            return await connection.xadd(stream, fields, **kwargs)
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("streams.xadd failed for %s: %s", stream, exc, exc_info=True)
            return None

    async def xack(self, stream: str, group: str, message_id: str) -> int:
        connection = await self._connection()
        if connection is None:
            return 0
        try:
            return int(await connection.xack(stream, group, message_id))
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("streams.xack failed for %s: %s", stream, exc, exc_info=True)
            return 0

    async def xreadgroup(
        self,
        *,
        groupname: str,
        consumername: str,
        streams: Mapping[str, str],
        count: int | None = None,
        block: int | None = None,
        noack: bool = False,
    ) -> Any:
        connection = await self._connection()
        if connection is None:
            return []
        try:
            return await connection.xreadgroup(
                groupname=groupname,
                consumername=consumername,
                streams=streams,
                count=count,
                block=block,
                noack=noack,
            )
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("streams.xreadgroup failed for %s: %s", streams, exc, exc_info=True)
            return []


__all__ = ("RedisStreamAdapter",)
