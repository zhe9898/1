from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from starlette.responses import Response

from backend.platform.logging.structured import get_logger
from backend.middleware import global_readonly_lock, limit_request_body

logger = get_logger("api")


def register_request_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def _readonly_lock(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        return await global_readonly_lock(request, call_next)

    @app.middleware("http")
    async def _limit_body(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        return await limit_request_body(request, call_next)

    @app.middleware("http")
    async def _log_request(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start
        request_id = getattr(request.state, "request_id", "unknown")
        response.headers["X-Process-Time"] = f"{duration:.3f}"
        logger.info(
            "%s %s %s %.3fs",
            request.method,
            request.url.path,
            response.status_code,
            duration,
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration": duration,
            },
        )
        return response
