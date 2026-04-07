from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api.deps import get_current_user_optional
from backend.api.main import app


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
    data = response.json()["data"]
    assert data["authenticated"] is True
    assert data["sub"] == "user-7"
    assert data["role"] == "admin"
    assert data["tenant_id"] == "tenant-a"
    assert data["scopes"] == ["write:jobs"]
