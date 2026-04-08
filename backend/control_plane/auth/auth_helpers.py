"""
ZEN70 璁よ瘉灞傚叕鍏遍昏緫锛氫緷璧栨牎楠屻佹寫鎴樻秷璐广佽姹備笂涓嬫枃銆佷护鐗屽搷搴斻?
闆嗕腑閿欒鐮佷笌鏃ュ織鏍煎紡锛岄檷浣庡啑浣欍佺粺涓鍙潬鎬ц竟鐣屻?"""

from __future__ import annotations

import base64
import ipaddress
import json
import threading
import time
from collections.abc import Mapping

from fastapi import Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

from backend.kernel.contracts.errors import ZenErrorCode, zen
from backend.control_plane.auth.jwt import create_access_token, get_access_token_expire_seconds
from backend.control_plane.auth.permissions import filter_valid_scopes
from backend.platform.logging.structured import get_logger
from backend.platform.redis.client import RedisClient

logger = get_logger("auth")

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
    """Sanitized legacy docstring."""
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
    "CODE_BAD_REQUEST",
    "CODE_DB_UNAVAILABLE",
    "CODE_FORBIDDEN",
    "CODE_NOT_FOUND",
    "CODE_REDIS_UNAVAILABLE",
    "CODE_SERVER_ERROR",
    "CODE_TOO_MANY",
    "CODE_UNAUTHORIZED",
    "require_db_redis",
    "request_id",
    "client_ip",
    "origin_from_request",
    "token_response",
    "log_auth",
    "consume_challenge",
    "expected_challenge_bytes",
    "extract_webauthn_transports",
    # compat exports
    "zen",
    "ZenErrorCode",
]


def request_id(req: Request) -> str:
    return getattr(req.state, "request_id", "")


def client_ip(req: Request) -> str:
    return req.client.host if req.client else ""


def origin_from_request(req: Request) -> str:
    """Sanitized legacy docstring."""
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
    """Sanitized legacy docstring."""
    sanitized_scopes = filter_valid_scopes(scopes)
    data: dict[str, object] = {
        "sub": str(sub),
        "username": username,
        "role": role,
        "tenant_id": tenant_id,
        "ai_route_preference": ai_route_preference,
        "scopes": sanitized_scopes,
    }
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
    """Sanitized legacy docstring."""
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
    """Sanitized legacy docstring."""
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
    """Sanitized legacy docstring."""
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
    浠?Redis 涓娆℃у彇鍥炲苟鏍￠獙鎸戞垬锛涙牎楠?flow锛堝強鍙?username锛夈?    杩斿洖 (challenge_base64url, payload_dict)銆?    澶辫触鐩存帴 raise HTTPException銆?    """
    challenge_b64 = get_challenge_from_credential(credential)
    if not challenge_b64:
        raise zen(
            CODE_BAD_REQUEST,
            "Invalid credential: missing challenge",
            status.HTTP_400_BAD_REQUEST,
        )

    stored = await redis.auth_challenges.consume(challenge_b64)
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
    """Sanitized legacy docstring."""
    return base64url_to_bytes(challenge_b64)


def is_private_ip(ip: str) -> bool:
    """Sanitized legacy docstring."""
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


WEBAUTHN_RATE_KEY = "webauthn:rate:"
WEBAUTHN_RATE_MAX = 20
WEBAUTHN_RATE_WINDOW = 60
WEBAUTHN_KNOWN_TRANSPORTS = frozenset({"ble", "hybrid", "internal", "nfc", "usb"})
_LOCAL_WEBAUTHN_RATE_LOCK = threading.Lock()
_LOCAL_WEBAUTHN_RATE_BUCKETS: dict[str, tuple[int, float]] = {}


def extract_webauthn_transports(credential: Mapping[str, object] | None) -> list[str]:
    """Extract a normalized transport list from a WebAuthn credential payload."""
    if credential is None:
        return []
    transports_source = credential.get("transports")
    if not isinstance(transports_source, list):
        response = credential.get("response")
        transports_source = response.get("transports") if isinstance(response, Mapping) else None
    if not isinstance(transports_source, list):
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for transport in transports_source:
        if not isinstance(transport, str):
            continue
        transport_name = transport.strip().lower()
        if transport_name not in WEBAUTHN_KNOWN_TRANSPORTS or transport_name in seen:
            continue
        seen.add(transport_name)
        normalized.append(transport_name)
    return normalized


def _check_local_webauthn_rate_limit(client_ip_str: str) -> None:
    now = time.monotonic()
    with _LOCAL_WEBAUTHN_RATE_LOCK:
        expired_keys = [key for key, (_, window_end) in _LOCAL_WEBAUTHN_RATE_BUCKETS.items() if now >= window_end]
        for key in expired_keys:
            del _LOCAL_WEBAUTHN_RATE_BUCKETS[key]
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
    WebAuthn 鎺ュ彛闄愭祦锛氭寜 IP 婊戝姩绐楀彛锛岃秴闄愭姏 429銆?    Redis 涓嶅彲鐢ㄦ椂鏀捐锛堢敱鏋佸垜瓒呮椂鍏滃簳锛夛紝閬垮厤闄愭祦鏁呴殰闃诲璁よ瘉銆?    """
    if redis is None:
        _check_local_webauthn_rate_limit(client_ip_str)
        return
    try:
        rate_key = f"{WEBAUTHN_RATE_KEY}{client_ip_str}"
        count = await redis.kv.incr(rate_key)
        if count == 1:
            await redis.kv.expire(rate_key, WEBAUTHN_RATE_WINDOW)
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
