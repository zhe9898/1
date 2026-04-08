from __future__ import annotations

import asyncio
import os
import re
import signal
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.api.deps import get_settings
from backend.kernel.extensions.extension_sdk import bootstrap_extension_runtime
from backend.core.redis_client import get_logger
from backend.core.runtime_support import connect_redis_with_retry

logger = get_logger("api")


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


@asynccontextmanager  # type: ignore[arg-type]
async def lifespan(app: FastAPI) -> object:
    logger.info("Starting API server")

    from backend.core.jwt import assert_jwt_runtime_ready
    from backend.core.rls import assert_rls_ready, validate_rls_runtime_mode
    from backend.db import _async_session_factory

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
            from backend.core.governance_facade import get_governance_facade

            async with _async_session_factory() as session:
                await get_governance_facade().load_tuner_state(session)
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
            logger.warning("Failed to restore scheduler tuner weights: %s", exc, exc_info=True)

    settings = get_settings()
    app.state.redis = await connect_redis_with_retry(settings, logger=logger)
    if app.state.redis is None:
        logger.error("Redis unavailable after retries; API will run with degraded capabilities")

    bootstrap_extension_runtime()

    def _sigterm_handler(signum: int, frame: object) -> None:
        logger.info("SIGTERM received (signal=%s), initiating graceful shutdown", signum)
        del frame

    original_handler = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, _sigterm_handler)

    from backend.capabilities import clear_lru_cache
    from backend.core.telemetry import init_telemetry, shutdown_telemetry

    init_telemetry(app)
    logger.info("API process is ingress-only; control-plane workers must run out of process")

    try:
        yield
    finally:
        logger.info("Shutting down API server and draining connections")

        if _async_session_factory is not None:
            try:
                from backend.core.governance_facade import get_governance_facade

                async with _async_session_factory() as session:
                    await get_governance_facade().save_tuner_state(session)
                    await session.commit()
            except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
                logger.warning("Failed to persist scheduler tuner weights on shutdown: %s", exc, exc_info=True)

        signal.signal(signal.SIGTERM, original_handler)
        shutdown_telemetry()
        clear_lru_cache()

        if getattr(app.state, "redis", None) is not None:
            try:
                await app.state.redis.close()
            except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
                logger.warning("Error closing Redis during shutdown: %s", exc)
            app.state.redis = None
        logger.info("Graceful shutdown complete")
