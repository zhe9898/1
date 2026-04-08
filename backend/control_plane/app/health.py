from __future__ import annotations

import asyncio

from fastapi import FastAPI, Request

from backend.api.deps import get_settings
from backend.api.models import HealthResponse
from backend.control_plane.app.lifespan import check_postgres_async
from backend.kernel.contracts.runtime_version import get_runtime_version
from backend.platform.redis.client import RedisClient


async def health_check(request: Request) -> HealthResponse:
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
        postgres_dsn = get_settings().get("postgres_dsn")
        services["postgres"] = await asyncio.wait_for(check_postgres_async(postgres_dsn), timeout=2.0)  # type: ignore[arg-type]
    except asyncio.TimeoutError:
        services["postgres"] = "timeout"

    status = (
        "healthy"
        if services.get("redis") == "ok" and services.get("postgres") == "ok"
        else ("unhealthy" if services.get("redis") != "ok" and services.get("postgres") != "ok" else "degraded")
    )
    return HealthResponse(status=status, version=get_runtime_version(), services=services)


def register_health_route(app: FastAPI) -> None:
    app.get("/health", response_model=HealthResponse)(health_check)
