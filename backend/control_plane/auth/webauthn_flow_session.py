from __future__ import annotations

import os
import uuid

from fastapi import Request, Response, status

from backend.control_plane.auth.cookie_policy import clear_http_only_cookie, read_request_cookie, set_http_only_cookie
from backend.kernel.contracts.errors import zen

WEBAUTHN_FLOW_SESSION_COOKIE = os.getenv("ZEN70_WEBAUTHN_FLOW_SESSION_COOKIE", "zen70_webauthn_session").strip() or "zen70_webauthn_session"


def ensure_webauthn_flow_session(response: Response, request: Request, *, ttl_seconds: int) -> str:
    session_id = _cookie_value(request)
    if session_id is None:
        session_id = uuid.uuid4().hex
    setattr(request.state, "webauthn_flow_session_id", session_id)
    set_http_only_cookie(response, key=WEBAUTHN_FLOW_SESSION_COOKIE, value=session_id, max_age_seconds=ttl_seconds)
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
    clear_http_only_cookie(response, key=WEBAUTHN_FLOW_SESSION_COOKIE)


def _cookie_value(request: Request) -> str | None:
    return read_request_cookie(request, WEBAUTHN_FLOW_SESSION_COOKIE)
