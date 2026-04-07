from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.api.main import app, lifespan


@pytest.mark.asyncio
async def test_lifespan_never_starts_inprocess_control_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZEN70_ENABLE_INPROCESS_WORKERS", "true")

    with (
        patch("backend.api.main.connect_redis_with_retry", new=AsyncMock(return_value=None)),
        patch("backend.api.main.signal.signal", return_value=MagicMock()),
        patch("backend.db._async_session_factory", None),
        patch("backend.core.jwt.assert_jwt_runtime_ready"),
        patch("backend.core.rls.validate_rls_runtime_mode"),
        patch("backend.capabilities.clear_lru_cache"),
    ):
        async with lifespan(app):
            assert getattr(app.state, "background_tasks", None) is None


@pytest.mark.asyncio
async def test_lifespan_keeps_ingress_process_limited_to_redis_and_cache_cleanup() -> None:
    redis_client = AsyncMock()
    rls_session = AsyncMock()
    rls_session.__aenter__.return_value = rls_session
    rls_session.__aexit__.return_value = None
    session_factory = MagicMock(return_value=rls_session)

    with (
        patch("backend.api.main.connect_redis_with_retry", new=AsyncMock(return_value=redis_client)),
        patch("backend.api.main.signal.signal", return_value=MagicMock()),
        patch("backend.db._async_session_factory", session_factory),
        patch("backend.core.jwt.assert_jwt_runtime_ready"),
        patch("backend.core.rls.validate_rls_runtime_mode"),
        patch("backend.core.rls.assert_rls_ready", new=AsyncMock()),
        patch("backend.capabilities.clear_lru_cache") as clear_cache,
    ):
        async with lifespan(app):
            assert app.state.redis is redis_client
            assert app.state.rls_ready is True

    redis_client.close.assert_awaited_once()
    clear_cache.assert_called_once()


@pytest.mark.asyncio
async def test_lifespan_fails_fast_when_jwt_runtime_is_not_ready() -> None:
    with (
        patch("backend.core.jwt.assert_jwt_runtime_ready", side_effect=RuntimeError("jwt not ready")),
        patch("backend.api.main.signal.signal", return_value=MagicMock()),
        patch("backend.db._async_session_factory", None),
    ):
        with pytest.raises(RuntimeError, match="jwt not ready"):
            async with lifespan(app):
                raise AssertionError("unreachable")
