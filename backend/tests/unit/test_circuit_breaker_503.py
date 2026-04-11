"""Unit tests for 503 degradation paths and WebAuthn rate limits."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from backend.control_plane.app.health import build_health_check


class TestRequireDbRedis:
    """Guard rails for missing DB/Redis dependencies."""

    def test_redis_none_raises_503(self) -> None:
        from backend.control_plane.auth.auth_helpers import require_db_redis

        fake_db = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            require_db_redis(fake_db, None)

        assert exc_info.value.status_code == 503
        assert "ZEN-AUTH-503" in str(exc_info.value.detail)

    def test_db_none_raises_503(self) -> None:
        from backend.control_plane.auth.auth_helpers import require_db_redis

        fake_redis = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            require_db_redis(None, fake_redis)

        assert exc_info.value.status_code == 503
        assert "ZEN-AUTH-503" in str(exc_info.value.detail)

    def test_both_valid_passes(self) -> None:
        from backend.control_plane.auth.auth_helpers import require_db_redis

        fake_db = MagicMock()
        fake_redis = MagicMock()

        require_db_redis(fake_db, fake_redis)

    def test_both_none_raises_503_for_db_first(self) -> None:
        from backend.control_plane.auth.auth_helpers import require_db_redis

        with pytest.raises(HTTPException) as exc_info:
            require_db_redis(None, None)

        assert exc_info.value.status_code == 503
        assert "Database" in str(exc_info.value.detail) or "ZEN-AUTH-503" in str(exc_info.value.detail)


class TestZenErrorBuilder:
    """Canonical error-envelope builder behavior."""

    def test_zen_creates_correct_envelope(self) -> None:
        from backend.kernel.contracts.errors import zen

        exc = zen("ZEN-TEST-001", "test message", 418, recovery_hint="try again")

        assert isinstance(exc, HTTPException)
        assert exc.status_code == 418
        detail = exc.detail
        assert detail["code"] == "ZEN-TEST-001"  # type: ignore[index]
        assert detail["message"] == "test message"  # type: ignore[index]
        assert detail["recovery_hint"] == "try again"  # type: ignore[index]
        assert isinstance(detail["details"], dict)  # type: ignore[index]

    def test_zen_with_enum_code(self) -> None:
        from backend.kernel.contracts.errors import ZenErrorCode, zen

        exc = zen(ZenErrorCode.AUTH_FORBIDDEN, "forbidden", 403)

        assert exc.detail["code"] == "ZEN-AUTH-403"  # type: ignore[index]

    def test_zen_extra_details_merged(self) -> None:
        from backend.kernel.contracts.errors import zen

        exc = zen(
            "ZEN-TEST-002",
            "msg",
            400,
            extra_details={"field": "username", "reason": "too_short"},
        )

        assert exc.detail["details"]["field"] == "username"  # type: ignore[index]
        assert exc.detail["details"]["reason"] == "too_short"  # type: ignore[index]


class TestHealthDegradation:
    """Control-plane health endpoint degradation behavior."""

    @staticmethod
    async def _run_health_check(
        *,
        redis: AsyncMock | None,
        postgres_dsn: str | None,
        postgres_status: str,
    ):
        request = MagicMock()
        request.app.state.redis = redis
        health_check = build_health_check(
            settings_provider=lambda: {"postgres_dsn": postgres_dsn},
            postgres_checker=AsyncMock(return_value=postgres_status),
        )
        return await health_check(request)

    @pytest.mark.asyncio
    async def test_redis_ping_false_returns_unhealthy(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=False)
        result = await self._run_health_check(
            redis=mock_redis,
            postgres_dsn=None,
            postgres_status="not_configured",
        )

        assert result.status == "unhealthy"
        assert result.services["redis"] == "error"

    @pytest.mark.asyncio
    async def test_redis_none_returns_unhealthy(self) -> None:
        result = await self._run_health_check(
            redis=None,
            postgres_dsn=None,
            postgres_status="not_configured",
        )

        assert result.status == "unhealthy"
        assert result.services["redis"] == "error"

    @pytest.mark.asyncio
    async def test_redis_ping_timeout_returns_timeout(self) -> None:
        import asyncio

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=asyncio.TimeoutError())
        result = await self._run_health_check(
            redis=mock_redis,
            postgres_dsn=None,
            postgres_status="not_configured",
        )

        assert result.status == "unhealthy"
        assert result.services["redis"] == "timeout"

    @pytest.mark.asyncio
    async def test_redis_ok_postgres_error_returns_degraded(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)
        result = await self._run_health_check(
            redis=mock_redis,
            postgres_dsn="postgresql://localhost/zen70",
            postgres_status="error",
        )

        assert result.status == "degraded"
        assert result.services["redis"] == "ok"
        assert result.services["postgres"] == "error"

    @pytest.mark.asyncio
    async def test_redis_error_postgres_ok_returns_degraded(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=False)
        result = await self._run_health_check(
            redis=mock_redis,
            postgres_dsn="postgresql://localhost/zen70",
            postgres_status="ok",
        )

        assert result.status == "degraded"
        assert result.services["redis"] == "error"
        assert result.services["postgres"] == "ok"

    @pytest.mark.asyncio
    async def test_both_ok_returns_healthy(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)
        result = await self._run_health_check(
            redis=mock_redis,
            postgres_dsn="postgresql://localhost/zen70",
            postgres_status="ok",
        )

        assert result.status == "healthy"
        assert result.services["redis"] == "ok"
        assert result.services["postgres"] == "ok"


class TestWebAuthnRateLimit:
    """WebAuthn IP rate limiting uses the platform Redis contract."""

    @pytest.mark.asyncio
    async def test_under_limit_passes(self) -> None:
        from backend.control_plane.auth.auth_helpers import check_webauthn_rate_limit

        redis = AsyncMock()
        redis.kv = AsyncMock()
        redis.kv.incr = AsyncMock(return_value=5)
        redis.kv.expire = AsyncMock()

        await check_webauthn_rate_limit(redis, "192.168.1.1", "rid-001")

        redis.kv.incr.assert_awaited_once()
        redis.kv.expire.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_over_limit_raises_429(self) -> None:
        from backend.control_plane.auth.auth_helpers import WEBAUTHN_RATE_MAX, check_webauthn_rate_limit

        redis = AsyncMock()
        redis.kv = AsyncMock()
        redis.kv.incr = AsyncMock(return_value=WEBAUTHN_RATE_MAX + 1)
        redis.kv.expire = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await check_webauthn_rate_limit(redis, "10.0.0.1", "rid-002")

        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_redis_none_passes(self) -> None:
        from backend.control_plane.auth.auth_helpers import check_webauthn_rate_limit

        await check_webauthn_rate_limit(None, "10.0.0.1", "rid-003")
