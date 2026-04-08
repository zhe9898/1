"""Unit tests for the AI router gateway."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestIdempotencyLock:
    @pytest.mark.asyncio
    async def test_lock_acquired_returns_true(self) -> None:
        from backend.ai_router import check_idempotency_lock

        redis = MagicMock()
        redis.kv = AsyncMock()
        redis.kv.set_if_absent = AsyncMock(return_value=True)

        request = MagicMock()
        request.app.state.redis = redis

        result = await check_idempotency_lock(request, "key-001")

        assert result is True
        redis.kv.set_if_absent.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_lock_already_held_returns_false(self) -> None:
        from backend.ai_router import check_idempotency_lock

        redis = MagicMock()
        redis.kv = AsyncMock()
        redis.kv.set_if_absent = AsyncMock(return_value=False)

        request = MagicMock()
        request.app.state.redis = redis

        result = await check_idempotency_lock(request, "key-002")

        assert result is False

    @pytest.mark.asyncio
    async def test_redis_adapter_unknown_state_returns_true_degraded(self) -> None:
        from backend.ai_router import check_idempotency_lock

        redis = MagicMock()
        redis.kv = AsyncMock()
        redis.kv.set_if_absent = AsyncMock(return_value=None)

        request = MagicMock()
        request.app.state.redis = redis

        result = await check_idempotency_lock(request, "key-003")

        assert result is True

    @pytest.mark.asyncio
    async def test_redis_none_returns_true_degraded(self) -> None:
        from backend.ai_router import check_idempotency_lock

        request = MagicMock()
        request.app.state.redis = None

        result = await check_idempotency_lock(request, "key-004")

        assert result is True

    @pytest.mark.asyncio
    async def test_redis_error_returns_true_degraded(self) -> None:
        from backend.ai_router import check_idempotency_lock

        redis = MagicMock()
        redis.kv = AsyncMock()
        redis.kv.set_if_absent = AsyncMock(side_effect=ConnectionError("redis down"))

        request = MagicMock()
        request.app.state.redis = redis

        result = await check_idempotency_lock(request, "key-005")

        assert result is True

    @pytest.mark.asyncio
    async def test_lock_key_format(self) -> None:
        from backend.ai_router import check_idempotency_lock

        redis = MagicMock()
        redis.kv = AsyncMock()
        redis.kv.set_if_absent = AsyncMock(return_value=True)

        request = MagicMock()
        request.app.state.redis = redis

        await check_idempotency_lock(request, "my-unique-key")

        assert redis.kv.set_if_absent.call_args.args[0] == "zen70:ai:idemp:my-unique-key"


class TestTimeout:
    def test_timeout_value_is_positive(self) -> None:
        from backend.ai_router import MULTIMODAL_TIMEOUT_SECONDS

        assert MULTIMODAL_TIMEOUT_SECONDS > 0

    def test_timeout_value_reasonable(self) -> None:
        from backend.ai_router import MULTIMODAL_TIMEOUT_SECONDS

        assert MULTIMODAL_TIMEOUT_SECONDS <= 60


class TestAIRouterConfig:
    def test_http_client_has_connection_limits(self) -> None:
        from backend.ai_router import http_client

        transport = getattr(http_client, "_transport", None)
        assert transport is not None, "http_client should expose an HTTP transport"

        pool = getattr(transport, "_pool", None)
        if pool is not None:
            max_connections = getattr(pool, "_max_connections", None)
            if max_connections is not None:
                assert max_connections <= 200

    def test_router_prefix(self) -> None:
        from backend.ai_router import router

        assert router.prefix == "/api/v1/ai"
