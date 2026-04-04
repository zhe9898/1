from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import jwt as pyjwt
import pytest
from fastapi import HTTPException

SECRET = "test-secret-deps-32bytes!!!!!!!!"
ALG = "HS256"


def _token(sub: str = "user1", role: str = "admin", expired: bool = False) -> str:
    now = datetime.now(UTC)
    exp = now - timedelta(minutes=1) if expired else now + timedelta(minutes=15)
    return pyjwt.encode({"sub": sub, "role": role, "tenant_id": "default", "iat": now, "exp": exp}, SECRET, algorithm=ALG)


class TestGetCurrentUser:
    @patch("backend.core.jwt._CURRENT", SECRET)
    @patch("backend.core.jwt._PREVIOUS", None)
    @pytest.mark.anyio
    async def test_valid_token_returns_payload(self) -> None:
        from backend.api.deps import get_current_user

        cred = MagicMock()
        cred.credentials = _token("alice", "admin")
        request = MagicMock()
        response = MagicMock()
        response.headers = {}

        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=MagicMock(is_active=True, status="active"))))

        result = await get_current_user(request, response, cred, db)
        assert result["sub"] == "alice"
        assert result["role"] == "admin"

    @pytest.mark.anyio
    async def test_missing_credentials_raises_401(self) -> None:
        from backend.api.deps import get_current_user

        request = MagicMock()
        response = MagicMock()
        db = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(request, response, None, db)
        assert exc_info.value.status_code == 401

    @pytest.mark.anyio
    async def test_empty_credentials_raises_401(self) -> None:
        from backend.api.deps import get_current_user

        cred = MagicMock()
        cred.credentials = ""
        request = MagicMock()
        response = MagicMock()
        db = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(request, response, cred, db)
        assert exc_info.value.status_code == 401

    @patch("backend.core.jwt._CURRENT", SECRET)
    @patch("backend.core.jwt._PREVIOUS", None)
    @pytest.mark.anyio
    async def test_disabled_user_token_is_rejected(self) -> None:
        from backend.api.deps import get_current_user

        cred = MagicMock()
        cred.credentials = _token("alice", "admin")
        request = MagicMock()
        response = MagicMock()
        response.headers = {}

        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=MagicMock(is_active=False, status="suspended"))))

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(request, response, cred, db)
        assert exc_info.value.status_code == 401


class TestGetCurrentAdmin:
    @pytest.mark.anyio
    async def test_admin_passes(self) -> None:
        from backend.api.deps import get_current_admin

        user = {"sub": "admin1", "role": "admin"}
        result = await get_current_admin(user)
        assert result["sub"] == "admin1"

    @pytest.mark.anyio
    async def test_superadmin_passes(self) -> None:
        from backend.api.deps import get_current_admin

        user = {"sub": "root1", "role": "superadmin"}
        result = await get_current_admin(user)
        assert result["sub"] == "root1"

    @pytest.mark.anyio
    async def test_non_admin_raises_403(self) -> None:
        from backend.api.deps import get_current_admin

        user = {"sub": "user1", "role": "viewer"}
        with pytest.raises(HTTPException) as exc_info:
            await get_current_admin(user)
        assert exc_info.value.status_code == 403


class TestGetCurrentUserOptional:
    @patch("backend.core.jwt._CURRENT", SECRET)
    @patch("backend.core.jwt._PREVIOUS", None)
    @pytest.mark.anyio
    async def test_valid_token_returns_payload(self) -> None:
        from backend.api.deps import get_current_user_optional

        cred = MagicMock()
        cred.credentials = _token("bob")
        request = MagicMock()
        response = MagicMock()
        response.headers = {}

        result = await get_current_user_optional(request, response, cred)
        assert result is not None
        assert result["sub"] == "bob"

    @pytest.mark.anyio
    async def test_no_credentials_returns_none(self) -> None:
        from backend.api.deps import get_current_user_optional

        request = MagicMock()
        response = MagicMock()
        result = await get_current_user_optional(request, response, None)
        assert result is None

    @patch("backend.core.jwt._CURRENT", SECRET)
    @patch("backend.core.jwt._PREVIOUS", None)
    @pytest.mark.anyio
    async def test_expired_token_returns_none(self) -> None:
        from backend.api.deps import get_current_user_optional

        cred = MagicMock()
        cred.credentials = _token(expired=True)
        request = MagicMock()
        response = MagicMock()

        result = await get_current_user_optional(request, response, cred)
        assert result is None


class TestSettingsAndTenantDb:
    def test_get_settings_contains_expected_keys(self) -> None:
        from backend.api.deps import get_settings

        get_settings.cache_clear()
        settings = get_settings()
        assert {"redis_host", "redis_port", "cors_origins", "postgres_dsn", "log_level"}.issubset(settings)
        get_settings.cache_clear()

    def test_cors_parses_comma_separated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from backend.api.deps import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("CORS_ORIGINS", "http://a.com, http://b.com")
        settings = get_settings()
        assert settings["cors_origins"] == ["http://a.com", "http://b.com"]
        get_settings.cache_clear()

    @pytest.mark.anyio
    async def test_get_tenant_db_rejects_when_rls_is_not_ready(self) -> None:
        from backend.api.deps import get_tenant_db

        db = MagicMock()
        with (
            patch("backend.api.deps.set_tenant_context") as set_tenant_context_mock,
            patch("backend.api.deps.assert_rls_ready", side_effect=RuntimeError("RLS missing")),
        ):
            set_tenant_context_mock.return_value = None
            with pytest.raises(HTTPException) as exc_info:
                await get_tenant_db({"tenant_id": "tenant-a"}, db)

        assert exc_info.value.status_code == 503

    @pytest.mark.anyio
    async def test_get_machine_tenant_db_prefers_header_without_parsing_body(self) -> None:
        from backend.api.deps import get_machine_tenant_db

        request = MagicMock()
        request.state = MagicMock()
        request.headers = {"X-Tenant-ID": "tenant-from-header"}
        request.json = AsyncMock(side_effect=AssertionError("request.json should not be called"))
        db = MagicMock()

        with (
            patch("backend.api.deps.set_tenant_context") as set_tenant_context_mock,
            patch("backend.api.deps.assert_rls_ready") as assert_rls_ready_mock,
        ):
            set_tenant_context_mock.return_value = None
            assert_rls_ready_mock.return_value = None
            result = await get_machine_tenant_db(request, db)

        assert result is db
        set_tenant_context_mock.assert_awaited_once_with(db, "tenant-from-header")
        assert_rls_ready_mock.assert_awaited_once_with(db)


class TestRequireScope:
    @pytest.mark.anyio
    async def test_admin_without_scope_is_rejected(self) -> None:
        from backend.api.deps import require_scope

        checker = require_scope("write:jobs")
        with pytest.raises(HTTPException) as exc_info:
            await checker({"sub": "u1", "role": "admin", "scopes": ["read:jobs"]})
        assert exc_info.value.status_code == 403

    @pytest.mark.anyio
    async def test_superadmin_can_bypass_scope_check(self) -> None:
        from backend.api.deps import require_scope

        checker = require_scope("write:jobs")
        result = await checker({"sub": "root", "role": "superadmin", "scopes": []})
        assert result["role"] == "superadmin"
