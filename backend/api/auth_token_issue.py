from __future__ import annotations

import sys
from dataclasses import dataclass

from backend.control_plane.auth.auth_helpers import token_response as _token_response_impl
from backend.control_plane.auth.jwt import get_access_token_expire_seconds


def _auth_mod() -> object:  # noqa: ANN202
    mod = sys.modules.get("backend.api.auth")
    if mod is not None:
        return mod

    class _Fallback:
        token_response = staticmethod(_token_response_impl)

    return _Fallback()


@dataclass(frozen=True, slots=True)
class IssuedAuthToken:
    access_token: str
    expires_in: int


def issue_auth_token(
    sub: str,
    username: str,
    role: str = "user",
    *,
    tenant_id: str = "default",
    ai_route_preference: str = "auto",
    scopes: list[str] | None = None,
) -> IssuedAuthToken:
    token_kwargs: dict[str, object] = {
        "tenant_id": tenant_id,
        "ai_route_preference": ai_route_preference,
    }
    if scopes is not None:
        token_kwargs["scopes"] = scopes
    body = _auth_mod().token_response(  # type: ignore[attr-defined]
        sub,
        username,
        role,
        **token_kwargs,
    )
    expires_in = int(body.get("expires_in", get_access_token_expire_seconds()))
    return IssuedAuthToken(
        access_token=str(body["access_token"]),
        expires_in=expires_in,
    )
