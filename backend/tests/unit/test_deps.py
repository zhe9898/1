from __future__ import annotations

import asyncio
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
        request.cookies = {}
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
        request.cookies = {}
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
        request.cookies = {}
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
        request.cookies = {}
        response = MagicMock()
        response.headers = {}

        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=MagicMock(is_active=False, status="suspended"))))

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(request, response, cred, db)
        assert exc_info.value.status_code == 401

    @patch("backend.core.jwt._CURRENT", SECRET)
    @patch("backend.core.jwt._PREVIOUS", None)
    @pytest.mark.anyio
    async def test_cookie_token_is_accepted_when_authorization_header_is_missing(self) -> None:
        from backend.api.deps import get_current_user

        request = MagicMock()
        request.cookies = {"zen70_access_token": _token("cookie-user", "admin")}
        request.app.state.redis = None
        response = MagicMock()
        response.headers = {}

        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=MagicMock(is_active=True, status="active"))))

        result = await get_current_user(request, response, None, db)
        assert result["sub"] == "cookie-user"


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
        request.cookies = {}
        response = MagicMock()
        response.headers = {}

        result = await get_current_user_optional(request, response, cred)
        assert result is not None
        assert result["sub"] == "bob"

    @pytest.mark.anyio
    async def test_no_credentials_returns_none(self) -> None:
        from backend.api.deps import get_current_user_optional

        request = MagicMock()
        request.cookies = {}
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
        request.cookies = {}
        response = MagicMock()

        result = await get_current_user_optional(request, response, cred)
        assert result is None

    @pytest.mark.anyio
    async def test_unexpected_decode_error_returns_none(self) -> None:
        from backend.api.deps import get_current_user_optional

        cred = MagicMock()
        cred.credentials = "bad-token"
        request = MagicMock()
        request.cookies = {}
        request.app.state.redis = None
        response = MagicMock()
        response.headers = {}

        with patch("backend.api.deps.decode_token", new=AsyncMock(side_effect=RuntimeError("decoder exploded"))):
            result = await get_current_user_optional(request, response, cred)

        assert result is None

    @patch("backend.core.jwt._CURRENT", SECRET)
    @patch("backend.core.jwt._PREVIOUS", None)
    @pytest.mark.anyio
    async def test_cookie_token_is_used_for_optional_auth(self) -> None:
        from backend.api.deps import get_current_user_optional

        request = MagicMock()
        request.cookies = {"zen70_access_token": _token("cookie-optional")}
        request.app.state.redis = None
        response = MagicMock()
        response.headers = {}

        result = await get_current_user_optional(request, response, None)
        assert result is not None
        assert result["sub"] == "cookie-optional"


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
    async def test_get_machine_tenant_db_derives_tenant_from_authenticated_node(self) -> None:
        from backend.api.deps import get_machine_tenant_db

        request = MagicMock()
        request.state = MagicMock()
        request.headers = {"Authorization": "Bearer node-secret"}
        request.json = AsyncMock(return_value={"tenant_id": "spoofed-tenant", "node_id": "node-1"})
        db = MagicMock()
        node = MagicMock()
        node.tenant_id = "tenant-from-node"
        node.node_id = "node-1"

        with (
            patch("backend.api.deps.authenticate_node_request", new=AsyncMock(return_value=node)) as auth_node_mock,
            patch("backend.api.deps.set_tenant_context") as set_tenant_context_mock,
            patch("backend.api.deps.assert_rls_ready") as assert_rls_ready_mock,
        ):
            set_tenant_context_mock.return_value = None
            assert_rls_ready_mock.return_value = None
            result = await get_machine_tenant_db(request, db)

        assert result is db
        auth_node_mock.assert_awaited_once_with(db, "node-1", "node-secret", require_active=False)
        set_tenant_context_mock.assert_awaited_once_with(db, "tenant-from-node")
        assert_rls_ready_mock.assert_awaited_once_with(db)
        assert request.state.machine_tenant_id == "tenant-from-node"

    @pytest.mark.anyio
    async def test_get_tenant_db_rejects_missing_tenant_id(self) -> None:
        from backend.api.deps import get_tenant_db

        with pytest.raises(HTTPException) as exc_info:
            await get_tenant_db({"sub": "alice", "role": "admin", "tenant_id": ""}, MagicMock())

        assert exc_info.value.status_code == 403

    @pytest.mark.anyio
    async def test_get_machine_tenant_db_times_out_when_body_stalls(self) -> None:
        from backend.api.deps import get_machine_tenant_db

        request = MagicMock()
        request.state = MagicMock()
        request.headers = {"Authorization": "Bearer node-secret"}

        async def _slow_json() -> dict[str, object]:
            raise asyncio.TimeoutError()

        request.json = AsyncMock(side_effect=_slow_json)

        with pytest.raises(HTTPException) as exc_info:
            await get_machine_tenant_db(request, MagicMock())

        assert exc_info.value.status_code == 408


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
        with pytest.raises(HTTPException) as exc_info:
            await checker({"sub": "root", "role": "superadmin", "scopes": []})
        assert exc_info.value.status_code == 403

    @pytest.mark.anyio
    async def test_superadmin_with_scope_passes(self) -> None:
        from backend.api.deps import require_scope

        checker = require_scope("write:jobs")
        result = await checker({"sub": "root", "role": "superadmin", "scopes": ["write:jobs"]})
        assert result["role"] == "superadmin"
