from __future__ import annotations

import os
from typing import Literal, cast

from fastapi import Request, Response

from backend.core.jwt import get_access_token_expire_seconds

CookieSameSite = Literal["lax", "strict", "none"]

AUTH_COOKIE_NAME = os.getenv("ZEN70_AUTH_COOKIE_NAME", "zen70_access_token").strip() or "zen70_access_token"
AUTH_COOKIE_DOMAIN = os.getenv("ZEN70_AUTH_COOKIE_DOMAIN", "").strip() or None
AUTH_COOKIE_PATH = os.getenv("ZEN70_AUTH_COOKIE_PATH", "/").strip() or "/"
_AUTH_COOKIE_SAMESITE_RAW = os.getenv("ZEN70_AUTH_COOKIE_SAMESITE", "lax").strip().lower() or "lax"
AUTH_COOKIE_SECURE = (
    os.getenv("ZEN70_AUTH_COOKIE_SECURE", "").strip().lower() in {"1", "true", "yes"}
    or os.getenv(
        "ZEN70_ENV",
        "",
    )
    .strip()
    .lower()
    == "production"
)

if _AUTH_COOKIE_SAMESITE_RAW not in {"lax", "strict", "none"}:
    _AUTH_COOKIE_SAMESITE_RAW = "lax"
AUTH_COOKIE_SAMESITE: CookieSameSite = cast(CookieSameSite, _AUTH_COOKIE_SAMESITE_RAW)


def get_auth_cookie_token(request: Request) -> str | None:
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not isinstance(token, str):
        return None
    normalized = token.strip()
    return normalized or None


def set_auth_cookie(response: Response, access_token: str, *, max_age_seconds: int | None = None) -> None:
    ttl_seconds = max_age_seconds if max_age_seconds is not None else get_access_token_expire_seconds()
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=access_token,
        max_age=max(ttl_seconds, 1),
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite=AUTH_COOKIE_SAMESITE,
        path=AUTH_COOKIE_PATH,
        domain=AUTH_COOKIE_DOMAIN,
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(
        key=AUTH_COOKIE_NAME,
        path=AUTH_COOKIE_PATH,
        domain=AUTH_COOKIE_DOMAIN,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite=AUTH_COOKIE_SAMESITE,
    )
