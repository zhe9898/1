from __future__ import annotations

import os
import uuid

from fastapi import Request, Response, status

from backend.api.auth_cookies import AUTH_COOKIE_DOMAIN, AUTH_COOKIE_PATH, AUTH_COOKIE_SAMESITE, AUTH_COOKIE_SECURE
from backend.core.errors import zen

WEBAUTHN_FLOW_SESSION_COOKIE = os.getenv("ZEN70_WEBAUTHN_FLOW_SESSION_COOKIE", "zen70_webauthn_session").strip() or "zen70_webauthn_session"


def ensure_webauthn_flow_session(response: Response, request: Request, *, ttl_seconds: int) -> str:
    session_id = _cookie_value(request)
    if session_id is None:
        session_id = uuid.uuid4().hex
    setattr(request.state, "webauthn_flow_session_id", session_id)
    response.set_cookie(
        key=WEBAUTHN_FLOW_SESSION_COOKIE,
        value=session_id,
        max_age=max(ttl_seconds, 1),
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite=AUTH_COOKIE_SAMESITE,
        path=AUTH_COOKIE_PATH,
        domain=AUTH_COOKIE_DOMAIN,
    )
    return session_id


def require_webauthn_flow_session(request: Request) -> str:
    session_id = _cookie_value(request)
    if session_id is None:
        state_value = getattr(request.state, "webauthn_flow_session_id", None)
        if isinstance(state_value, str) and state_value.strip():
            session_id = state_value.strip()
    if session_id is None:
        raise zen(
            "ZEN-AUTH-4003",
            "WebAuthn flow session is missing or expired",
            status_code=status.HTTP_400_BAD_REQUEST,
            recovery_hint="Restart the WebAuthn flow and complete it in the same browser session",
        )
    return session_id


def clear_webauthn_flow_session(response: Response) -> None:
    response.delete_cookie(
        key=WEBAUTHN_FLOW_SESSION_COOKIE,
        path=AUTH_COOKIE_PATH,
        domain=AUTH_COOKIE_DOMAIN,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite=AUTH_COOKIE_SAMESITE,
    )


def _cookie_value(request: Request) -> str | None:
    cookies = getattr(request, "cookies", None)
    raw_value = cookies.get(WEBAUTHN_FLOW_SESSION_COOKIE) if isinstance(cookies, dict) else None
    if not isinstance(raw_value, str):
        return None
    normalized = raw_value.strip()
    return normalized or None
