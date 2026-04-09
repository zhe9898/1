from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api.deps import get_current_user_optional, get_settings
from backend.api.main import app
from backend.control_plane.app.factory import create_app


def _assert_identity_cache_headers(response) -> None:
    assert response.headers.get("cache-control") == "no-store, private"
    assert response.headers.get("pragma") == "no-cache"
    assert response.headers.get("expires") == "0"
    vary = response.headers.get("vary", "").lower()
    assert "cookie" in vary


def test_auth_session_reports_anonymous_state() -> None:
    async def override_get_current_user_optional() -> dict | None:
        return None

    app.dependency_overrides[get_current_user_optional] = override_get_current_user_optional
    try:
        client = TestClient(app)
        response = client.get("/api/v1/auth/session")
    finally:
        app.dependency_overrides.pop(get_current_user_optional, None)

    assert response.status_code == 200
    _assert_identity_cache_headers(response)
    data = response.json()["data"]
    assert data["authenticated"] is False
    assert data["sub"] is None


def test_auth_session_reports_cookie_backed_identity_claims() -> None:
    async def override_get_current_user_optional() -> dict | None:
        return {
            "sub": "user-7",
            "username": "alice",
            "role": "admin",
            "tenant_id": "tenant-a",
            "scopes": ["write:jobs"],
            "ai_route_preference": "cloud",
            "exp": 1_800_000_000,
        }

    app.dependency_overrides[get_current_user_optional] = override_get_current_user_optional
    try:
        client = TestClient(app)
        response = client.get("/api/v1/auth/session")
    finally:
        app.dependency_overrides.pop(get_current_user_optional, None)

    assert response.status_code == 200
    _assert_identity_cache_headers(response)
    data = response.json()["data"]
    assert data["authenticated"] is True
    assert data["sub"] == "user-7"
    assert data["role"] == "admin"
    assert data["tenant_id"] == "tenant-a"
    assert data["scopes"] == ["write:jobs"]


def test_auth_session_normalizes_role_aliases_and_invalid_ai_preference() -> None:
    async def override_get_current_user_optional() -> dict | None:
        return {
            "sub": "user-8",
            "username": "bob",
            "role": "family_child",
            "tenant_id": "tenant-b",
            "scopes": ["write:jobs", "invalid:scope"],
            "ai_route_preference": "edge",
            "exp": 1_800_000_000,
        }

    app.dependency_overrides[get_current_user_optional] = override_get_current_user_optional
    try:
        client = TestClient(app)
        response = client.get("/api/v1/auth/session")
    finally:
        app.dependency_overrides.pop(get_current_user_optional, None)

    assert response.status_code == 200
    _assert_identity_cache_headers(response)
    data = response.json()["data"]
    assert data["role"] == "child"
    assert data["scopes"] == ["write:jobs"]
    assert data["ai_route_preference"] == "auto"


def test_auth_session_cors_preflight_allows_x_requested_with() -> None:
    get_settings.cache_clear()
    cors_app = create_app()
    client = TestClient(cors_app)
    response = client.options(
        "/api/v1/auth/session",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-Requested-With",
        },
    )

    assert response.status_code == 200
    allow_headers = response.headers.get("access-control-allow-headers", "").lower()
    assert "x-requested-with" in allow_headers
    get_settings.cache_clear()
