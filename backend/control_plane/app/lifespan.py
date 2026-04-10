from __future__ import annotations

import asyncio
import os
import re
import signal
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Protocol, TypeAlias, cast

from fastapi import FastAPI

from backend.api.deps import get_settings
from backend.kernel.extensions.extension_sdk import bootstrap_extension_runtime
from backend.platform.events.runtime import connect_event_bus_with_retry, resolve_event_bus_backend, set_runtime_event_bus
from backend.platform.logging.structured import get_logger
from backend.platform.redis.runtime import connect_redis_with_retry

logger = get_logger("api")


SettingsProvider: TypeAlias = Callable[[], Mapping[str, object]]
RedisConnector: TypeAlias = Callable[..., Awaitable[Any]]
SignalNumber: TypeAlias = int | signal.Signals
LifespanContext: TypeAlias = AbstractAsyncContextManager[None, bool | None]
LifespanFactory: TypeAlias = Callable[[FastAPI], LifespanContext]


class SignalModule(Protocol):
    SIGTERM: SignalNumber

    def getsignal(self, signalnum: SignalNumber) -> Any: ...

    def signal(self, signalnum: SignalNumber, handler: Any) -> Any: ...


async def check_postgres_async(dsn: str | None) -> str:
    try:
        from backend.db import _async_session_factory

        if _async_session_factory:
            from sqlalchemy import text

            async with _async_session_factory() as session:
                await session.execute(text("SELECT 1"))
            return "ok"
    except ImportError as exc:
        logger.warning("Health check Postgres session factory import failed: %s", exc)
        return "error"
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
        logger.debug("Health check Postgres SELECT 1 failed: %s", exc)
    if not dsn or not dsn.strip():
        return "not_configured"
    try:
        match = re.search(r"@([^:/]+)(?::(\d+))?", dsn)
        if not match:
            return "error"
        host, port_str = match.group(1), match.group(2)
        port = int(port_str) if port_str else 5432
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=2.0)
        writer.close()
        await writer.wait_closed()
        return "tcp_only"
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
        logger.debug("Postgres TCP connectivity check failed: %s", exc)
        return "error"


def build_lifespan(
    *,
    settings_provider: SettingsProvider = get_settings,
    redis_connector: RedisConnector = connect_redis_with_retry,
    signal_module: SignalModule = cast(SignalModule, signal),
) -> LifespanFactory:
    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info("Starting API server")

        from backend.control_plane.auth.jwt import assert_jwt_runtime_ready
        from backend.db import _async_session_factory
        from backend.platform.db.rls import assert_rls_ready, validate_rls_runtime_mode

        assert_jwt_runtime_ready()
        validate_rls_runtime_mode()

        jwt_previous = os.getenv("JWT_SECRET_PREVIOUS", "")
        if jwt_previous:
            logger.info("JWT dual-track rotation active (PREVIOUS key loaded)")

        app.state.rls_ready = False
        if _async_session_factory is not None:
            async with _async_session_factory() as session:
                await assert_rls_ready(session)
            app.state.rls_ready = True
            logger.info("RLS runtime readiness verified before serving tenant traffic")

        if _async_session_factory is not None:
            try:
                from backend.kernel.scheduling.governance_facade import get_governance_facade

                async with _async_session_factory() as session:
                    await get_governance_facade().load_tuner_state(session)
            except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
                logger.warning("Failed to restore scheduler tuner weights: %s", exc, exc_info=True)

        settings = settings_provider()
        app.state.redis = await redis_connector(settings, logger=logger)
        if app.state.redis is None:
            logger.error("Redis unavailable after retries; API will run with degraded capabilities")
        app.state.event_bus = await connect_event_bus_with_retry(settings, redis=app.state.redis, logger=logger)
        set_runtime_event_bus(app.state.event_bus)
        if app.state.event_bus is None:
            configured_event_bus = resolve_event_bus_backend(settings)
            if configured_event_bus == "nats":
                raise RuntimeError("NATS event bus is required but unavailable")
            logger.error("Event bus unavailable after retries; realtime streams will run degraded")

        bootstrap_extension_runtime()

        def _sigterm_handler(signum: int, frame: object) -> None:
            logger.info("SIGTERM received (signal=%s), initiating graceful shutdown", signum)
            del frame

        original_handler = signal_module.getsignal(signal_module.SIGTERM)
        signal_module.signal(signal_module.SIGTERM, _sigterm_handler)

        from backend.capabilities import clear_lru_cache
        from backend.platform.telemetry.tracing import init_telemetry, shutdown_telemetry

        init_telemetry(app)
        logger.info("API process is ingress-only; control-plane workers must run out of process")

        try:
            yield
        finally:
            logger.info("Shutting down API server and draining connections")

            if _async_session_factory is not None:
                try:
                    from backend.kernel.scheduling.governance_facade import get_governance_facade

                    async with _async_session_factory() as session:
                        await get_governance_facade().save_tuner_state(session)
                        await session.commit()
                except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
                    logger.warning("Failed to persist scheduler tuner weights on shutdown: %s", exc, exc_info=True)

            signal_module.signal(signal_module.SIGTERM, original_handler)
            shutdown_telemetry()
            clear_lru_cache()

            if getattr(app.state, "redis", None) is not None:
                try:
                    await app.state.redis.close()
                except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
                    logger.warning("Error closing Redis during shutdown: %s", exc)
                app.state.redis = None
            if getattr(app.state, "event_bus", None) is not None:
                try:
                    await app.state.event_bus.close()
                except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
                    logger.warning("Error closing event bus during shutdown: %s", exc)
                app.state.event_bus = None
            set_runtime_event_bus(None)
            logger.info("Graceful shutdown complete")

    return cast(LifespanFactory, _lifespan)
