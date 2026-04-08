from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, Protocol, TypeVar

try:
    from redis.exceptions import RedisError
except ImportError:  # pragma: no cover - redis may be absent in minimal test envs

    class RedisError(OSError):
        pass


_T = TypeVar("_T")
REDIS_OPERATION_ERRORS = (OSError, ValueError, KeyError, RuntimeError, TypeError, RedisError)


class AsyncRedisOwner(Protocol):
    logger: Any

    async def _require_connection(self) -> Any | None: ...

    async def _retry_once(
        self,
        coro: Callable[[], Coroutine[Any, Any, _T]],
        fallback: _T,
        op_name: str = "op",
    ) -> _T: ...


class AsyncRedisComponent:
    def __init__(self, owner: AsyncRedisOwner) -> None:
        self._owner = owner

    @property
    def logger(self) -> Any:
        return self._owner.logger

    async def _connection(self) -> Any | None:
        return await self._owner._require_connection()

    async def _retry_once(
        self,
        coro: Callable[[], Coroutine[Any, Any, _T]],
        fallback: _T,
        op_name: str = "op",
    ) -> _T:
        return await self._owner._retry_once(coro, fallback, op_name)


class SyncRedisOwner(Protocol):
    logger: Any

    def _require_connection(self) -> Any | None: ...


class SyncRedisComponent:
    def __init__(self, owner: SyncRedisOwner) -> None:
        self._owner = owner

    @property
    def logger(self) -> Any:
        return self._owner.logger

    def _connection(self) -> Any | None:
        return self._owner._require_connection()


async def retry_once(
    logger: Any,
    coro: Callable[[], Coroutine[Any, Any, _T]],
    fallback: _T,
    op_name: str = "op",
) -> _T:
    try:
        return await coro()
    except REDIS_OPERATION_ERRORS as exc:
        logger.warning("%s failed, retrying once: %s", op_name, exc)
        await asyncio.sleep(0.1)
    try:
        return await coro()
    except REDIS_OPERATION_ERRORS as exc:
        logger.error("%s failed after retry: %s", op_name, exc, exc_info=True)
        return fallback


__all__ = (
    "AsyncRedisComponent",
    "AsyncRedisOwner",
    "REDIS_OPERATION_ERRORS",
    "SyncRedisComponent",
    "SyncRedisOwner",
    "retry_once",
)
