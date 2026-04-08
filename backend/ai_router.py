"""Gateway AI proxy with centralized prompt and route policy."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Mapping
from typing import cast

import httpx
from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse

from backend.control_plane.auth.ai_policy import apply_prompt_override, resolve_ai_proxy_policy
from backend.control_plane.auth.jwt import decode_token

AI_BACKEND_URL = os.getenv("AI_BACKEND_URL", "").rstrip("/")
if not AI_BACKEND_URL:
    logging.warning("AI_BACKEND_URL is not set. AI router will return 503 errors if accessed.")

MULTIMODAL_TIMEOUT_SECONDS = 30.0

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])

http_client = httpx.AsyncClient(
    limits=httpx.Limits(max_keepalive_connections=50, max_connections=100),
    timeout=httpx.Timeout(connect=2.0, read=None, write=None, pool=2.0),
)


async def check_idempotency_lock(request: Request, idempotency_key: str) -> bool:
    """Acquire a bounded Redis idempotency lock for heavy AI requests."""
    redis_client = getattr(request.app.state, "redis", None)
    if redis_client is None:
        return True

    try:
        lock_key = f"zen70:ai:idemp:{idempotency_key}"
        acquired = cast("bool | None", await redis_client.kv.set_if_absent(lock_key, "1", ttl_seconds=60))
        if acquired is None:
            return True
        return bool(acquired)
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
        logging.error("Failed to check idempotency lock: %s", exc)
        return True


async def _decode_current_user(request: Request) -> Mapping[str, object]:
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return {}

    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        return {}

    try:
        redis_client = getattr(request.app.state, "redis", None)
        payload, _ = await decode_token(token, redis_conn=redis_client.kv if redis_client else None)
        return payload
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
        logging.getLogger("zen70.ai_router").debug("AI router token decode failed: %s", exc)
        return {}


async def _apply_proxy_policy(
    request: Request,
    *,
    path: str,
    content: bytes,
    headers: dict[str, str],
    public_cloud_url: str,
    public_cloud_key: str,
) -> tuple[str, bytes]:
    current_user = await _decode_current_user(request)
    policy = resolve_ai_proxy_policy(current_user, method=request.method, path=path)

    target_url = ""
    if policy.route_preference == "cloud" and public_cloud_url and public_cloud_key:
        target_url = f"{public_cloud_url}/{path}"
        headers["authorization"] = f"Bearer {public_cloud_key}"
        headers.pop("host", None)

    if policy.prompt_override is not None:
        content = apply_prompt_override(content, policy.prompt_override)
        headers["content-length"] = str(len(content))

    return target_url, content


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def universal_ai_proxy(
    request: Request,
    path: str,
    x_idempotency_key: str = Header(None, description="Optional idempotency key for heavy AI requests"),
    x_capability_target: str = Header(
        ...,
        alias="X-Capability-Target",
        description="Requested compute capability target, such as ai_vision or gpu_nvenc_v1",
    ),
) -> StreamingResponse:
    from backend.capabilities import get_capabilities_matrix, raise_503_if_pending
    from backend.kernel.contracts.errors import zen as _zen

    matrix = await get_capabilities_matrix(request)
    raise_503_if_pending(x_capability_target, matrix)

    if request.method in ("POST", "PUT") and x_idempotency_key:
        lock_acquired = await check_idempotency_lock(request, x_idempotency_key)
        if not lock_acquired:
            raise _zen(
                "ZEN-AI-4090",
                "An identical AI request is already in flight",
                status_code=409,
                recovery_hint="Wait for the original response or generate a new idempotency key",
                details={"idempotency_key": x_idempotency_key},
            )

    target_url = f"{AI_BACKEND_URL}/{path}"
    headers = dict(request.headers)
    headers.pop("host", None)
    content = await request.body()

    public_cloud_url = os.getenv("EXTERNAL_OPENAI_URL", "").rstrip("/")
    public_cloud_key = os.getenv("EXTERNAL_OPENAI_KEY", "")
    override_url, content = await _apply_proxy_policy(
        request,
        path=path,
        content=content,
        headers=headers,
        public_cloud_url=public_cloud_url,
        public_cloud_key=public_cloud_key,
    )
    if override_url:
        target_url = override_url

    async def _forward_request() -> StreamingResponse:
        req = http_client.build_request(
            method=request.method,
            url=target_url,
            headers=headers,
            content=content,
        )
        resp = await http_client.send(req, stream=True)

        async def stream_generator() -> AsyncIterator[bytes]:
            try:
                async for chunk in resp.aiter_bytes():
                    yield chunk
            finally:
                await resp.aclose()

        return StreamingResponse(
            stream_generator(),
            status_code=resp.status_code,
            headers={k: v for k, v in resp.headers.items() if k.lower() not in ("content-length", "content-encoding")},
        )

    try:
        return await asyncio.wait_for(_forward_request(), timeout=MULTIMODAL_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:

        async def fallback_stream() -> AsyncIterator[bytes]:
            yield (
                f"\n\n[ZEN70: AI proxy request timed out after {MULTIMODAL_TIMEOUT_SECONDS:.0f}s. "
                "The response stream was cut short to protect runtime capacity.]"
            ).encode("utf-8")

        return StreamingResponse(fallback_stream(), status_code=206, media_type="text/plain")
    except httpx.ConnectError:
        raise _zen(
            "ZEN-AI-5002",
            "Unable to reach the backing AI runtime",
            status_code=502,
            recovery_hint="Check the backend AI network path and container health",
            details={"target_url": target_url},
        )
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
        raise _zen(
            "ZEN-AI-5000",
            "AI gateway proxy failed",
            status_code=500,
            recovery_hint="Inspect the request logs with the active request ID",
            details={"error": str(exc)},
        )
