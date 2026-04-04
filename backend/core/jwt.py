from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Protocol

import jwt
from fastapi import status

if TYPE_CHECKING:
    from redis.exceptions import RedisError
else:
    try:
        from redis.exceptions import RedisError
    except ImportError:  # pragma: no cover - redis may be absent in minimal test environments

        class RedisError(OSError):
            pass


from backend.core.errors import zen

ALGORITHM = "HS256"
DEFAULT_INSECURE_SECRET = "change-me-in-production-min-32-bytes"

_IS_PROD = os.getenv("ZEN70_ENV", "").lower() == "production"
_CURRENT = os.getenv("JWT_SECRET_CURRENT") or os.getenv("JWT_SECRET") or ("" if _IS_PROD else DEFAULT_INSECURE_SECRET)
_PREVIOUS = os.getenv("JWT_SECRET_PREVIOUS") or None
_EXPIRE_MINUTES = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "15"))
_INITIAL_CURRENT = _CURRENT
_INITIAL_PREVIOUS = _PREVIOUS
_INITIAL_EXPIRE_MINUTES = _EXPIRE_MINUTES

# When True, token revocation check fails CLOSED (deny) if Redis is unavailable.
# In production, set REDIS_REQUIRED_FOR_TOKEN_REVOCATION=1 to enforce strict revocation.
# In development, defaults to False (deny only on confirmed blacklist hit).
_REVOCATION_STRICT = os.getenv("REDIS_REQUIRED_FOR_TOKEN_REVOCATION", "1" if _IS_PROD else "0").strip().lower() in (
    "1",
    "true",
    "yes",
)

if _IS_PROD and not _CURRENT:
    raise RuntimeError("JWT_SECRET_CURRENT or JWT_SECRET must be set in production (ZEN70_ENV=production)")


class RedisBlacklistStore(Protocol):
    async def set(self, key: str, value: str, ex: int) -> Any: ...

    async def get(self, key: str) -> Any: ...


def _resolved_current_secret() -> str:
    is_prod = os.getenv("ZEN70_ENV", "").lower() == "production"
    # Preserve test/runtime overrides when module-level constants are monkeypatched.
    if _CURRENT != _INITIAL_CURRENT:
        return _CURRENT
    return os.getenv("JWT_SECRET_CURRENT") or os.getenv("JWT_SECRET") or ("" if is_prod else DEFAULT_INSECURE_SECRET)


def _resolved_previous_secret() -> str | None:
    if _PREVIOUS != _INITIAL_PREVIOUS:
        return _PREVIOUS
    return os.getenv("JWT_SECRET_PREVIOUS") or None


def _resolved_expire_minutes() -> int:
    if _EXPIRE_MINUTES != _INITIAL_EXPIRE_MINUTES:
        return _EXPIRE_MINUTES
    raw = os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES")
    if raw is None:
        return _EXPIRE_MINUTES
    try:
        minutes = int(raw)
    except (TypeError, ValueError):
        return _EXPIRE_MINUTES
    return minutes if minutes > 0 else _EXPIRE_MINUTES


def _resolved_revocation_strict() -> bool:
    raw = os.getenv("REDIS_REQUIRED_FOR_TOKEN_REVOCATION")
    if raw is None:
        return os.getenv("ZEN70_ENV", "").strip().lower() == "production"
    return raw.strip().lower() in ("1", "true", "yes")


def _assert_production_secret_safety() -> None:
    if os.getenv("ZEN70_ENV", "").strip().lower() != "production":
        return
    current_secret = _resolved_current_secret()
    if not current_secret:
        raise RuntimeError("JWT_SECRET_CURRENT or JWT_SECRET must be set in production")
    if current_secret == DEFAULT_INSECURE_SECRET:
        raise RuntimeError("JWT runtime is using the insecure default secret in production")
    if len(current_secret) < 32:
        raise RuntimeError("JWT_SECRET_CURRENT must be at least 32 bytes in production")


def assert_jwt_runtime_ready() -> None:
    current_secret = _resolved_current_secret()
    if not current_secret:
        raise RuntimeError("JWT_SECRET_CURRENT or JWT_SECRET must be set before starting the gateway")
    if current_secret == DEFAULT_INSECURE_SECRET:
        raise RuntimeError("JWT runtime is using the insecure default secret; configure JWT_SECRET_CURRENT explicitly")
    if len(current_secret) < 32:
        raise RuntimeError("JWT_SECRET_CURRENT must be at least 32 bytes")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(
    data: dict[str, object],
    expires_delta: timedelta | None = None,
    *,
    use_current_secret: bool = True,
) -> str:
    _assert_production_secret_safety()
    to_encode = data.copy()
    expire = _now() + (expires_delta if expires_delta is not None else timedelta(minutes=_resolved_expire_minutes()))
    to_encode["exp"] = expire
    to_encode["iat"] = _now()
    to_encode["jti"] = uuid.uuid4().hex
    current_secret = _resolved_current_secret()
    previous_secret = _resolved_previous_secret()
    secret = current_secret if use_current_secret else (previous_secret or current_secret)
    return jwt.encode(to_encode, secret, algorithm=ALGORITHM)


async def decode_token(token: str, *, redis_conn: RedisBlacklistStore | None = None) -> tuple[dict[str, object], str | None]:
    _assert_production_secret_safety()
    if not token or not token.strip():
        exc = zen("ZEN-AUTH-401", "Missing or invalid token", status_code=status.HTTP_401_UNAUTHORIZED)
        exc.headers = {"WWW-Authenticate": "Bearer"}
        raise exc

    current_secret = _resolved_current_secret()
    previous_secret = _resolved_previous_secret()

    try:
        payload = jwt.decode(token, current_secret, algorithms=[ALGORITHM])
        exp = payload.get("exp")
        iat = payload.get("iat")
        if exp and iat:
            current_timestamp = _now().timestamp()
            lifespan = exp - iat
            if (current_timestamp - iat) > (lifespan / 2):
                new_token = create_access_token(
                    {key: value for key, value in payload.items() if key not in ("exp", "iat", "nbf", "jti")},
                    use_current_secret=True,
                )
                await _blacklist_jti(redis_conn, payload.get("jti"), int(exp - current_timestamp))
                return payload, new_token
        return payload, None
    except jwt.InvalidTokenError:
        logging.getLogger("zen70.jwt").debug("Token validation failed with current secret, trying previous")

    if previous_secret:
        try:
            payload = jwt.decode(token, previous_secret, algorithms=[ALGORITHM])
            new_token = create_access_token(
                {key: value for key, value in payload.items() if key not in ("exp", "iat", "nbf", "jti")},
                use_current_secret=True,
            )
            exp = payload.get("exp", 0)
            await _blacklist_jti(redis_conn, payload.get("jti"), int(max(exp - _now().timestamp(), 60)))
            return payload, new_token
        except jwt.InvalidTokenError:
            logging.getLogger("zen70.jwt").debug("Token validation also failed with previous secret")

    exc = zen("ZEN-AUTH-401", "Invalid or expired token", status_code=status.HTTP_401_UNAUTHORIZED)
    exc.headers = {"WWW-Authenticate": "Bearer"}
    raise exc


async def _blacklist_jti(redis_conn: RedisBlacklistStore | None, jti: object | None, ttl_seconds: int) -> None:
    if redis_conn is None or jti is None:
        # Redis unavailable: log warning. Token will expire naturally via exp claim.
        # This is acceptable because tokens are short-lived (default 15 min).
        if jti is not None:
            logging.getLogger("zen70.jwt").warning(
                "jti blacklist write skipped (Redis unavailable): jti=%s ttl=%ds 鈥?" "token will expire naturally at exp claim",
                jti,
                ttl_seconds,
            )
        return
    try:
        await redis_conn.set(f"jwt:blacklist:{jti}", "1", ex=max(ttl_seconds, 1))
    except (OSError, RuntimeError, TypeError, ValueError, RedisError) as exc:
        logging.getLogger("zen70.jwt").warning(
            "jti blacklist write FAILED (Redis error): jti=%s 鈥?token remains valid until exp: %s",
            jti,
            exc,
        )


async def is_jti_blacklisted(redis_conn: RedisBlacklistStore | None, jti: object | None) -> bool:
    revocation_strict = _resolved_revocation_strict()
    if redis_conn is None or jti is None:
        if revocation_strict:
            # Strict mode: Redis required. Fail closed 鈥?treat as blacklisted.
            logging.getLogger("zen70.jwt").warning("is_jti_blacklisted: Redis unavailable in strict mode, denying token jti=%s", jti)
            return True
        return False
    try:
        return await redis_conn.get(f"jwt:blacklist:{jti}") is not None
    except (OSError, RuntimeError, TypeError, ValueError, RedisError) as exc:
        if revocation_strict:
            logging.getLogger("zen70.jwt").warning("is_jti_blacklisted: Redis error in strict mode, denying token jti=%s: %s", jti, exc)
            return True
        # Non-strict: fail open (token passes). Log at warning so it's visible.
        logging.getLogger("zen70.jwt").warning("is_jti_blacklisted: Redis error, failing open for jti=%s: %s", jti, exc)
        return False


def get_access_token_expire_seconds() -> int:
    return _resolved_expire_minutes() * 60
