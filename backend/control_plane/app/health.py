from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import TypeAlias

from fastapi import FastAPI, Request

from backend.api.deps import get_settings
from backend.api.models import HealthResponse
from backend.control_plane.app.lifespan import check_postgres_async
from backend.kernel.contracts.runtime_version import get_runtime_version
from backend.platform.redis.client import RedisClient

SettingsProvider: TypeAlias = Callable[[], Mapping[str, object]]
PostgresChecker: TypeAlias = Callable[[str | None], Awaitable[str]]
HealthCheckHandler: TypeAlias = Callable[[Request], Awaitable[HealthResponse]]


async def _run_health_check(
    request: Request,
    *,
    settings_provider: SettingsProvider,
    postgres_checker: PostgresChecker,
) -> HealthResponse:
    redis_client: RedisClient | None = getattr(request.app.state, "redis", None)
    services: dict[str, str] = {}
    if redis_client:
        try:
            ok = await asyncio.wait_for(redis_client.ping(), timeout=2.0)
            services["redis"] = "ok" if ok else "error"
        except asyncio.TimeoutError:
            services["redis"] = "timeout"
        except (OSError, ValueError, KeyError, RuntimeError, TypeError):
            services["redis"] = "error"
    else:
        services["redis"] = "error"
    try:
        raw_postgres_dsn = settings_provider().get("postgres_dsn")
        postgres_dsn = raw_postgres_dsn if isinstance(raw_postgres_dsn, str) else None
        services["postgres"] = await asyncio.wait_for(postgres_checker(postgres_dsn), timeout=2.0)
    except asyncio.TimeoutError:
        services["postgres"] = "timeout"

    status = (
        "healthy"
        if services.get("redis") == "ok" and services.get("postgres") == "ok"
        else ("unhealthy" if services.get("redis") != "ok" and services.get("postgres") != "ok" else "degraded")
    )
    return HealthResponse(status=status, version=get_runtime_version(), services=services)


def build_health_check(
    *,
    settings_provider: SettingsProvider = get_settings,
    postgres_checker: PostgresChecker = check_postgres_async,
) -> HealthCheckHandler:
    async def _health_check(request: Request) -> HealthResponse:
        return await _run_health_check(
            request,
            settings_provider=settings_provider,
            postgres_checker=postgres_checker,
        )

    _health_check.__name__ = "health_check"
    return _health_check


def register_health_route(
    app: FastAPI,
    *,
    settings_provider: SettingsProvider = get_settings,
    postgres_checker: PostgresChecker = check_postgres_async,
) -> None:
    app.get(
        "/health",
        response_model=HealthResponse,
        summary="Health Check",
        operation_id="health_check_health_get",
    )(build_health_check(settings_provider=settings_provider, postgres_checker=postgres_checker))
