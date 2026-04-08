from __future__ import annotations

import time

from backend.api.models.auth import AuthSessionResponse
from backend.control_plane.auth.permissions import filter_valid_scopes
from backend.control_plane.auth.role_claims import normalize_ai_route_preference, normalize_role_name


def _coerce_optional_string(value: object, *, fallback: str | None = None) -> str | None:
    if isinstance(value, str):
        normalized = value.strip()
        if normalized:
            return normalized
    return fallback


def build_authenticated_session_response(
    *,
    sub: str,
    username: str,
    role: str,
    tenant_id: str,
    ai_route_preference: str = "auto",
    scopes: list[str] | None = None,
    expires_in: int,
) -> AuthSessionResponse:
    normalized_scopes = filter_valid_scopes(scopes)
    return AuthSessionResponse(
        authenticated=True,
        sub=_coerce_optional_string(sub),
        username=_coerce_optional_string(username),
        role=normalize_role_name(role),
        tenant_id=_coerce_optional_string(tenant_id),
        scopes=normalized_scopes,
        ai_route_preference=normalize_ai_route_preference(ai_route_preference),
        exp=int(time.time()) + max(int(expires_in), 1),
    )
