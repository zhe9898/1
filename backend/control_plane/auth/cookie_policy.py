from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Literal, cast

from fastapi import Request, Response

CookieSameSite = Literal["lax", "strict", "none"]
_COOKIE_KEY_PATTERN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_COOKIE_VALUE_PATTERN = re.compile(r"^[\x21\x23-\x2B\x2D-\x3A\x3C-\x5B\x5D-\x7E]+$")

COOKIE_DOMAIN = os.getenv("ZEN70_AUTH_COOKIE_DOMAIN", "").strip() or None
COOKIE_PATH = os.getenv("ZEN70_AUTH_COOKIE_PATH", "/").strip() or "/"
_COOKIE_SAMESITE_RAW = os.getenv("ZEN70_AUTH_COOKIE_SAMESITE", "lax").strip().lower() or "lax"
COOKIE_SECURE = (
    os.getenv("ZEN70_AUTH_COOKIE_SECURE", "").strip().lower() in {"1", "true", "yes"}
    or os.getenv(
        "ZEN70_ENV",
        "",
    )
    .strip()
    .lower()
    == "production"
)

if _COOKIE_SAMESITE_RAW not in {"lax", "strict", "none"}:
    _COOKIE_SAMESITE_RAW = "lax"
COOKIE_SAMESITE: CookieSameSite = cast(CookieSameSite, _COOKIE_SAMESITE_RAW)


def _normalize_cookie_key(key: str) -> str:
    normalized = key.strip()
    if not normalized or _COOKIE_KEY_PATTERN.fullmatch(normalized) is None:
        raise ValueError("cookie key must be an RFC 6265 token")
    return normalized


def _normalize_cookie_value(value: str) -> str:
    normalized = value.strip()
    if not normalized or _COOKIE_VALUE_PATTERN.fullmatch(normalized) is None:
        raise ValueError("cookie value must use RFC 6265 cookie-octet characters")
    return normalized


def read_request_cookie(request: Request, key: str) -> str | None:
    cookies = getattr(request, "cookies", None)
    raw_value = cookies.get(key) if isinstance(cookies, Mapping) else None
    if not isinstance(raw_value, str):
        return None
    normalized = raw_value.strip()
    return normalized or None


def set_http_only_cookie(response: Response, *, key: str, value: str, max_age_seconds: int) -> None:
    response.set_cookie(
        key=_normalize_cookie_key(key),
        value=_normalize_cookie_value(value),
        max_age=max(max_age_seconds, 1),
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        path=COOKIE_PATH,
        domain=COOKIE_DOMAIN,
    )


def clear_http_only_cookie(response: Response, *, key: str) -> None:
    response.delete_cookie(
        key=_normalize_cookie_key(key),
        path=COOKIE_PATH,
        domain=COOKIE_DOMAIN,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
    )


__all__ = (
    "COOKIE_DOMAIN",
    "COOKIE_PATH",
    "COOKIE_SAMESITE",
    "COOKIE_SECURE",
    "CookieSameSite",
    "clear_http_only_cookie",
    "read_request_cookie",
    "set_http_only_cookie",
)
