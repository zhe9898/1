"""
ZEN70 认证层公共逻辑：依赖校验、挑战消费、请求上下文、令牌响应。

集中错误码与日志格式，降低冗余、统一可靠性边界。
"""

from __future__ import annotations

import base64
import ipaddress
import json
import threading
import time

from fastapi import Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

from backend.core.errors import ZenErrorCode, zen
from backend.core.jwt import create_access_token, get_access_token_expire_seconds
from backend.core.redis_client import RedisClient, get_logger

logger = get_logger("auth")

# 错误码与常量（兼容旧引用；新代码优先使用 ZenErrorCode / enums）
CODE_DB_UNAVAILABLE = "ZEN-AUTH-503"
CODE_REDIS_UNAVAILABLE = "ZEN-AUTH-503"
CODE_BAD_REQUEST = "ZEN-AUTH-400"
CODE_UNAUTHORIZED = str(ZenErrorCode.AUTH_UNAUTHORIZED)
CODE_FORBIDDEN = str(ZenErrorCode.AUTH_FORBIDDEN)
CODE_NOT_FOUND = "ZEN-AUTH-404"
CODE_TOO_MANY = "ZEN-AUTH-429"
CODE_SERVER_ERROR = "ZEN-AUTH-500"

CHALLENGE_TTL = 300


def require_db_redis(
    db: AsyncSession | None,
    redis: RedisClient | None,
) -> None:
    """无 DB 或 Redis 时直接抛 503，避免后续空指针。"""
    if db is None:
        raise zen(
            CODE_DB_UNAVAILABLE,
            "Database not configured",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    if redis is None:
        raise zen(
            CODE_REDIS_UNAVAILABLE,
            "Redis not available",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )


__all__ = [
    "require_db_redis",
    "request_id",
    "client_ip",
    "origin_from_request",
    "token_response",
    "log_auth",
    "consume_challenge",
    "expected_challenge_bytes",
    # compat exports
    "zen",
    "ZenErrorCode",
]


def request_id(req: Request) -> str:
    return getattr(req.state, "request_id", "")


def client_ip(req: Request) -> str:
    return req.client.host if req.client else ""


def origin_from_request(req: Request) -> str:
    """协议 + 主机，无路径（rstrip 已去除末尾斜杠）。"""
    return str(req.base_url).rstrip("/")


def token_response(
    sub: str,
    username: str,
    role: str = "user",
    tenant_id: str = "default",
    ai_route_preference: str = "auto",
    scopes: list[str] | None = None,
    **kwargs: object,
) -> dict[str, str | int]:
    """统一构造 TokenResponse 体。包含 AI 路由偏好和多租户标识法典隔离。"""
    data: dict[str, object] = {
        "sub": str(sub),
        "username": username,
        "role": role,
        "tenant_id": tenant_id,
        "ai_route_preference": ai_route_preference,
        "scopes": scopes or [],
    }
    # 忽略未知 kwargs，防止调用传错参数导致崩溃
    access_token = create_access_token(data=data)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": get_access_token_expire_seconds(),
    }


def log_auth(
    event: str,
    success: bool,
    request_id_str: str,
    *,
    username: str | None = None,
    client_ip_str: str | None = None,
    detail: str | None = None,
) -> None:
    """结构化日志。"""
    log_obj = {
        "event": event,
        "success": success,
        "request_id": request_id_str,
        "username": username,
        "client_ip": client_ip_str,
        "detail": detail,
    }
    msg = json.dumps(log_obj, ensure_ascii=False)
    if success:
        logger.info(msg)
    else:
        logger.warning(msg)


def _base64url_decode(s: str) -> bytes:
    pad = 4 - len(s) % 4
    if pad != 4:
        s += "=" * pad
    return base64.urlsafe_b64decode(s)


def get_challenge_from_credential(credential: dict[str, object]) -> str | None:
    """从 credential.response.clientDataJSON 解析 challenge（base64url）。"""
    if not isinstance(credential, dict):
        return None  # type: ignore[unreachable]
    try:
        resp = credential.get("response")
        if not isinstance(resp, dict):
            return None
        client_data_b64 = resp.get("clientDataJSON")
        if not client_data_b64 or not isinstance(client_data_b64, str):
            return None
        raw = _base64url_decode(client_data_b64)
        data = json.loads(raw.decode("utf-8"))
        result: str | None = data.get("challenge")
        return result
    except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError):
        return None


def credential_id_to_base64url(credential: dict[str, object]) -> str | None:
    """从 credential 取 id/rawId 转为 base64url 字符串。"""
    if not isinstance(credential, dict):
        return None  # type: ignore[unreachable]
    raw = credential.get("id") or credential.get("rawId")
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    if isinstance(raw, bytes):
        return bytes_to_base64url(raw)
    return None


async def consume_challenge(
    redis: RedisClient,
    credential: dict[str, object],
    flow: str,
    username: str | None = None,
) -> tuple[str, dict[str, object]]:
    """
    从 Redis 一次性取回并校验挑战；校验 flow（及可选 username）。
    返回 (challenge_base64url, payload_dict)。
    失败直接 raise HTTPException。
    """
    challenge_b64 = get_challenge_from_credential(credential)
    if not challenge_b64:
        raise zen(
            CODE_BAD_REQUEST,
            "Invalid credential: missing challenge",
            status.HTTP_400_BAD_REQUEST,
        )

    stored = await redis.get_auth_challenge(challenge_b64)
    if not stored:
        raise zen(
            CODE_UNAUTHORIZED,
            "Challenge expired or already used",
            status.HTTP_401_UNAUTHORIZED,
        )

    try:
        data = json.loads(stored)
    except json.JSONDecodeError:
        raise zen(
            CODE_SERVER_ERROR,
            "Invalid challenge data",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if data.get("flow") != flow:
        raise zen(CODE_BAD_REQUEST, "Invalid challenge flow", status.HTTP_400_BAD_REQUEST)
    if username is not None and data.get("username") != username:
        raise zen(CODE_BAD_REQUEST, "Challenge mismatch", status.HTTP_400_BAD_REQUEST)

    return challenge_b64, data


def expected_challenge_bytes(challenge_b64: str) -> bytes:
    """base64url 挑战转 bytes（供 webauthn 校验）。"""
    return base64url_to_bytes(challenge_b64)


def is_private_ip(ip: str) -> bool:
    """内网 IP 判断。"""
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


WEBAUTHN_RATE_KEY = "webauthn:rate:"
WEBAUTHN_RATE_MAX = 20
WEBAUTHN_RATE_WINDOW = 60
_LOCAL_WEBAUTHN_RATE_LOCK = threading.Lock()
_LOCAL_WEBAUTHN_RATE_BUCKETS: dict[str, tuple[int, float]] = {}


def _check_local_webauthn_rate_limit(client_ip_str: str) -> None:
    now = time.monotonic()
    with _LOCAL_WEBAUTHN_RATE_LOCK:
        count, window_end = _LOCAL_WEBAUTHN_RATE_BUCKETS.get(client_ip_str, (0, now + WEBAUTHN_RATE_WINDOW))
        if now >= window_end:
            count = 0
            window_end = now + WEBAUTHN_RATE_WINDOW
        count += 1
        _LOCAL_WEBAUTHN_RATE_BUCKETS[client_ip_str] = (count, window_end)
        if count > WEBAUTHN_RATE_MAX:
            raise zen(
                CODE_TOO_MANY,
                "Too many authentication attempts, try again later",
                status.HTTP_429_TOO_MANY_REQUESTS,
            )


async def check_webauthn_rate_limit(
    redis: RedisClient | None,
    client_ip_str: str,
    request_id_str: str,
) -> None:
    """
    WebAuthn 接口限流：按 IP 滑动窗口，超限抛 429。
    Redis 不可用时放行（由极刑超时兜底），避免限流故障阻塞认证。
    """
    if redis is None:
        _check_local_webauthn_rate_limit(client_ip_str)
        return
    try:
        count = await redis.incr_with_expire(f"{WEBAUTHN_RATE_KEY}{client_ip_str}", WEBAUTHN_RATE_WINDOW)
    except (OSError, ValueError, KeyError, RuntimeError, TypeError):
        _check_local_webauthn_rate_limit(client_ip_str)
        return
    if count > WEBAUTHN_RATE_MAX:
        logger.warning(
            json.dumps(
                {
                    "event": "webauthn_rate_limit",
                    "client_ip": client_ip_str,
                    "request_id": request_id_str,
                    "count": count,
                },
                ensure_ascii=False,
            )
        )
        raise zen(
            CODE_TOO_MANY,
            "Too many authentication attempts, try again later",
            status.HTTP_429_TOO_MANY_REQUESTS,
        )
