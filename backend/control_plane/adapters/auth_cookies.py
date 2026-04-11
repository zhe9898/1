from __future__ import annotations

import os

from fastapi import Request, Response

from backend.control_plane.auth.cookie_policy import clear_http_only_cookie, read_request_cookie, set_http_only_cookie
from backend.control_plane.auth.jwt import get_access_token_expire_seconds

AUTH_COOKIE_NAME = os.getenv("ZEN70_AUTH_COOKIE_NAME", "zen70_access_token").strip() or "zen70_access_token"


def get_auth_cookie_token(request: Request) -> str | None:
    return read_request_cookie(request, AUTH_COOKIE_NAME)


def set_auth_cookie(response: Response, access_token: str, *, max_age_seconds: int | None = None) -> None:
    ttl_seconds = max_age_seconds if max_age_seconds is not None else get_access_token_expire_seconds()
    set_http_only_cookie(response, key=AUTH_COOKIE_NAME, value=access_token, max_age_seconds=ttl_seconds)


def clear_auth_cookie(response: Response) -> None:
    clear_http_only_cookie(response, key=AUTH_COOKIE_NAME)
