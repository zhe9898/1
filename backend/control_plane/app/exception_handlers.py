from __future__ import annotations

from typing import cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.api.models import ErrorResponse
from backend.core.redis_client import get_logger

logger = get_logger("api")


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unknown")
        detail = cast(object, exc.detail)
        if isinstance(detail, dict) and "code" in detail:
            return JSONResponse(status_code=exc.status_code, content=detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                code=f"ZEN-HTTP-{exc.status_code}",
                message=str(exc.detail) if exc.detail else "HTTP error",
                recovery_hint="Check the request and retry",
                details={"request_id": request_id},
            ).model_dump(mode="json"),
        )

    @app.exception_handler(StarletteHTTPException)
    async def starlette_http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unknown")
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                code=f"ZEN-HTTP-{exc.status_code}",
                message=str(exc.detail) if exc.detail else "HTTP error",
                recovery_hint="Check the URL and retry",
                details={"request_id": request_id},
            ).model_dump(mode="json"),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unknown")
        return JSONResponse(
            status_code=422,
            content=ErrorResponse(
                code="ZEN-VAL-422",
                message="Validation error",
                recovery_hint="Check request payload and retry",
                details={"request_id": request_id, "errors": exc.errors()},
            ).model_dump(mode="json"),
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unknown")
        logger.error("Unhandled exception: %s", exc, exc_info=True, extra={"request_id": request_id})
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                code="ZEN-INT-5000",
                message="Internal server error",
                recovery_hint="Retry later or contact administrator",
                details={"request_id": request_id},
            ).model_dump(mode="json"),
        )
