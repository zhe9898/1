from __future__ import annotations

import uuid
from dataclasses import dataclass

from backend.control_plane.auth.auth_helpers import token_response
from backend.control_plane.auth.jwt import get_access_token_expire_seconds


@dataclass(frozen=True, slots=True)
class IssuedAuthToken:
    access_token: str
    expires_in: int
    session_id: str
    token_id: str


def issue_auth_token(
    sub: str,
    username: str,
    role: str = "user",
    *,
    tenant_id: str = "default",
    ai_route_preference: str = "auto",
    scopes: list[str] | None = None,
    session_id: str | None = None,
    token_id: str | None = None,
) -> IssuedAuthToken:
    resolved_session_id = session_id or uuid.uuid4().hex
    resolved_token_id = token_id or uuid.uuid4().hex
    body = token_response(
        sub,
        username,
        role,
        tenant_id=tenant_id,
        ai_route_preference=ai_route_preference,
        scopes=scopes,
        session_id=resolved_session_id,
        token_id=resolved_token_id,
    )
    expires_in = int(body.get("expires_in", get_access_token_expire_seconds()))
    return IssuedAuthToken(
        access_token=str(body["access_token"]),
        expires_in=expires_in,
        session_id=resolved_session_id,
        token_id=resolved_token_id,
    )
