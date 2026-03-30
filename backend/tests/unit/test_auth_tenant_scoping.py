from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import bcrypt
import pytest
from fastapi import HTTPException
from sqlalchemy import Select

from backend.api.auth import create_invite, create_user, list_users, login_begin, password_login, pin_login, revoke_credential
from backend.api.models.auth import (
    CreateUserRequest,
    InviteCreateRequest,
    PasswordLoginRequest,
    PinLoginRequest,
    WebAuthnLoginBeginRequest,
)
from backend.models.user import User


def _mock_request(client_ip: str = "192.168.1.10") -> MagicMock:
    request = MagicMock()
    request.state.request_id = "rid-auth-tenant"
    request.client.host = client_ip
    return request


def _mock_db(result_value: object | None = None) -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = result_value
    result.scalars.return_value.all.return_value = [result_value] if result_value is not None else []
    result.first.return_value = result_value
    db.execute.return_value = result
    db.flush = AsyncMock()
    db.delete = AsyncMock()
    db.add = MagicMock()
    return db


def _render_sql(stmt: Select[tuple[object]]) -> str:
    return str(stmt)


@pytest.mark.asyncio
async def test_pin_login_propagates_real_role_and_tenant() -> None:
    request = _mock_request()
    redis = AsyncMock()
    redis.get.return_value = None
    redis.delete = AsyncMock()
    redis.redis = MagicMock()

    user = MagicMock()
    user.id = 42
    user.username = "family"
    user.role = "admin"
    user.tenant_id = "tenant-alpha"
    user.ai_route_preference = "cloud"
    user.pin_hash = bcrypt.hashpw(b"123456", bcrypt.gensalt(rounds=4)).decode("utf-8")
    user.is_active = True

    db = _mock_db(user)

    with patch(
        "backend.api.auth.token_response",
        return_value={"access_token": "tok", "token_type": "bearer", "expires_in": 900},
    ) as token_response_mock:
        await pin_login(PinLoginRequest(username="family", pin="123456", tenant_id="tenant-alpha"), request, db=db, redis=redis)

    token_response_mock.assert_called_once_with(
        "42",
        "family",
        "admin",
        tenant_id="tenant-alpha",
        ai_route_preference="cloud",
    )


@pytest.mark.asyncio
async def test_password_login_scopes_user_lookup_by_tenant() -> None:
    request = _mock_request("127.0.0.1")
    redis = AsyncMock()
    redis.get.return_value = None
    redis.incr.return_value = 1
    redis.delete = AsyncMock()

    user = MagicMock()
    user.id = 7
    user.username = "admin"
    user.password_hash = bcrypt.hashpw(b"Password123!", bcrypt.gensalt(rounds=4)).decode("utf-8")
    user.role = "admin"
    user.tenant_id = "tenant-a"
    user.ai_route_preference = "auto"
    user.is_active = True

    db = _mock_db(user)

    await password_login(
        PasswordLoginRequest(username="admin", password="Password123!", tenant_id="tenant-a"),
        request,
        db=db,
        redis=redis,
    )

    stmt = db.execute.await_args_list[1].args[0]
    rendered = _render_sql(stmt)
    assert "users.tenant_id" in rendered
    assert "users.username" in rendered


@pytest.mark.asyncio
async def test_pin_login_scopes_user_lookup_by_tenant() -> None:
    request = _mock_request()
    redis = AsyncMock()
    redis.get.return_value = None
    redis.delete = AsyncMock()
    redis.redis = MagicMock()

    user = MagicMock()
    user.id = 42
    user.username = "family"
    user.role = "admin"
    user.tenant_id = "tenant-alpha"
    user.ai_route_preference = "cloud"
    user.pin_hash = bcrypt.hashpw(b"123456", bcrypt.gensalt(rounds=4)).decode("utf-8")
    user.is_active = True

    db = _mock_db(user)

    with patch(
        "backend.api.auth.token_response",
        return_value={"access_token": "tok", "token_type": "bearer", "expires_in": 900},
    ):
        await pin_login(PinLoginRequest(username="family", pin="123456", tenant_id="tenant-alpha"), request, db=db, redis=redis)

    stmt = db.execute.await_args.args[0]
    rendered = _render_sql(stmt)
    assert "users.tenant_id" in rendered
    assert "users.username" in rendered


@pytest.mark.asyncio
async def test_webauthn_login_begin_scopes_user_lookup_by_tenant() -> None:
    request = _mock_request("127.0.0.1")
    redis = AsyncMock()
    redis.set_auth_challenge = AsyncMock(return_value=True)

    credential = MagicMock()
    credential.credential_id = "cred-1"

    user = MagicMock()
    user.id = 7
    user.username = "shared-user"
    user.credentials = [credential]
    user.is_active = True

    db = _mock_db(user)

    with (
        patch("backend.api.auth.check_webauthn_rate_limit", new=AsyncMock()),
        patch(
            "backend.api.auth.generate_authentication_challenge",
            return_value=(b"challenge", "challenge-b64", '{"challenge":"challenge-b64"}'),
        ),
    ):
        await login_begin(
            WebAuthnLoginBeginRequest(username="shared-user", tenant_id="tenant-a"),
            request,
            db=db,
            redis=redis,
        )

    stmt = db.execute.await_args.args[0]
    rendered = _render_sql(stmt)
    assert "users.tenant_id" in rendered
    assert "users.username" in rendered


@pytest.mark.asyncio
async def test_webauthn_login_begin_rejects_disabled_user() -> None:
    request = _mock_request("127.0.0.1")
    redis = AsyncMock()
    redis.set_auth_challenge = AsyncMock(return_value=True)

    credential = MagicMock()
    credential.credential_id = "cred-1"

    user = MagicMock()
    user.id = 7
    user.username = "shared-user"
    user.credentials = [credential]
    user.is_active = False

    db = _mock_db(user)

    with (
        patch("backend.api.auth.check_webauthn_rate_limit", new=AsyncMock()),
        patch(
            "backend.api.auth.generate_authentication_challenge",
            return_value=(b"challenge", "challenge-b64", '{"challenge":"challenge-b64"}'),
        ),
        pytest.raises(HTTPException) as exc,
    ):
        await login_begin(
            WebAuthnLoginBeginRequest(username="shared-user", tenant_id="tenant-a"),
            request,
            db=db,
            redis=redis,
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_create_user_rejects_cross_tenant_target_for_tenant_admin() -> None:
    db = _mock_db()
    with patch("backend.api.auth.set_tenant_context", new=AsyncMock()):
        with pytest.raises(HTTPException) as exc:
            await create_user(
                CreateUserRequest(
                    username="user-b",
                    password="Password123!",
                    display_name="User B",
                    role="family",
                    tenant_id="tenant-b",
                ),
                db=db,
                current_admin={"sub": "admin-a", "role": "admin", "tenant_id": "tenant-a"},
            )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_create_user_scopes_username_uniqueness_to_target_tenant() -> None:
    db = _mock_db(None)
    created: dict[str, object] = {}

    def add_side_effect(user: object) -> None:
        user.id = 8
        user.is_active = True
        created["user"] = user

    db.add = MagicMock(side_effect=add_side_effect)
    with patch("backend.api.auth.set_tenant_context", new=AsyncMock()):
        response = await create_user(
            CreateUserRequest(
                username="shared-name",
                password="Password123!",
                display_name="Shared Name",
                role="family",
                tenant_id="tenant-a",
            ),
            db=db,
            current_admin={"sub": "admin-a", "role": "admin", "tenant_id": "tenant-a"},
        )

    stmt = db.execute.await_args.args[0]
    rendered = _render_sql(stmt)
    assert "users.tenant_id" in rendered
    assert "users.username" in rendered
    assert response.username == "shared-name"


@pytest.mark.asyncio
async def test_list_users_binds_tenant_scope_for_admin_queries() -> None:
    user = User(
        username="tenant-admin",
        display_name="Tenant Admin",
        role="admin",
        tenant_id="tenant-a",
        password_hash="hash",
        is_active=True,
    )
    user.id = 1
    user.credentials = []

    db = _mock_db(user)
    with patch("backend.api.auth.set_tenant_context", new=AsyncMock()):
        response = await list_users(
            db=db,
            current_admin={"sub": "admin-a", "role": "admin", "tenant_id": "tenant-a"},
        )

    stmt = db.execute.await_args.args[0]
    rendered = _render_sql(stmt)
    assert "users.tenant_id" in rendered
    assert response.users[0].tenant_id == "tenant-a"


@pytest.mark.asyncio
async def test_revoke_credential_scopes_lookup_to_admin_tenant() -> None:
    db = _mock_db(None)
    with patch("backend.api.auth.set_tenant_context", new=AsyncMock()):
        with pytest.raises(HTTPException) as exc:
            await revoke_credential(
                "cred-cross-tenant",
                db=db,
                current_admin={"sub": "admin-a", "role": "admin", "tenant_id": "tenant-a"},
            )

    stmt = db.execute.await_args.args[0]
    rendered = _render_sql(stmt)
    assert "JOIN users" in rendered
    assert "users.tenant_id" in rendered
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_create_invite_scopes_user_lookup_to_admin_tenant() -> None:
    redis = AsyncMock()
    db = _mock_db(None)
    with patch("backend.api.auth.set_tenant_context", new=AsyncMock()):
        with pytest.raises(HTTPException) as exc:
            await create_invite(
                InviteCreateRequest(user_id=99, expires_in_minutes=15),
                db=db,
                redis=redis,
                current_admin={"sub": "admin-a", "role": "admin", "tenant_id": "tenant-a"},
            )

    stmt = db.execute.await_args.args[0]
    rendered = _render_sql(stmt)
    assert "users.tenant_id" in rendered
    assert exc.value.status_code == 404
