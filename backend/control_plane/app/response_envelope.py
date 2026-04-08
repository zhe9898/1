from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from starlette.responses import Response

from backend.core.redis_client import get_logger

logger = get_logger("api")

_ENVELOPE_SKIP_PATHS = {"/health", "/openapi.json", "/api/docs", "/api/redoc", "/metrics"}
_ENVELOPE_MAX_BODY_BYTES = 10 * 1024 * 1024


def register_success_envelope(app: FastAPI) -> None:
    @app.middleware("http")
    async def success_envelope(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)

        if request.url.path in _ENVELOPE_SKIP_PATHS:
            return response
        content_type = response.headers.get("content-type", "")
        if response.status_code < 200 or response.status_code >= 300:
            return response
        if "application/json" not in content_type:
            return response

        body_iterator = getattr(response, "body_iterator", None)
        if body_iterator is None:
            return response

        body_chunks: list[bytes] = []
        body_size = 0
        oversized = False
        async for chunk in body_iterator:
            raw = chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
            body_size += len(raw)
            body_chunks.append(raw)
            if body_size > _ENVELOPE_MAX_BODY_BYTES:
                oversized = True
                break

        if oversized:
            async for remaining in body_iterator:
                body_chunks.append(remaining if isinstance(remaining, bytes) else remaining.encode("utf-8"))
            logger.warning(
                "Response body exceeds %d bytes for %s, skip envelope wrapping",
                _ENVELOPE_MAX_BODY_BYTES,
                request.url.path,
            )
            return Response(
                content=b"".join(body_chunks),
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )

        body_bytes = b"".join(body_chunks)
        try:
            original = json.loads(body_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return Response(
                content=body_bytes,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )

        if isinstance(original, dict) and original.get("code") == "ZEN-OK-0":
            return Response(
                content=body_bytes,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )

        envelope = {"code": "ZEN-OK-0", "message": "ok", "data": original}
        new_body = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        new_headers = dict(response.headers)
        new_headers["content-length"] = str(len(new_body))
        return Response(
            content=new_body,
            status_code=response.status_code,
            headers=new_headers,
            media_type="application/json",
        )
