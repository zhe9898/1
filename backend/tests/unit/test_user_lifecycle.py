"""Unit tests for backend.core.user_lifecycle token/session revocation."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.core.user_lifecycle import activate_user, delete_user, suspend_user
from backend.models.user import User


class _ScalarResult:
    def __init__(self, value: object | None):
        self._value = value

    def first(self) -> object | None:
        return self._value


class _ExecuteResult:
    def __init__(self, value: object | None):
        self._value = value

    def scalars(self) -> _ScalarResult:
        return _ScalarResult(self._value)


def _make_user(*, status: str = "active") -> User:
    user = User(
        id=42,
        tenant_id="tenant-a",
        username="alice",
        display_name="Alice",
        role="user",
        status=status,
        is_active=True,
    )
    return user


@pytest.mark.asyncio
async def test_suspend_user_revokes_all_user_sessions() -> None:
    db = AsyncMock()
    redis = AsyncMock()
    user = _make_user(status="active")
    db.execute = AsyncMock(return_value=_ExecuteResult(user))
    db.flush = AsyncMock()

    with patch("backend.core.user_lifecycle.revoke_all_user_sessions", new=AsyncMock()) as revoke_all:
        updated = await suspend_user(db, redis, tenant_id="tenant-a", user_id=42, suspended_by="admin", reason="policy")

    assert updated.status == "suspended"
    assert updated.is_active is False
    rendered = str(db.execute.await_args.args[0])
    assert "users.tenant_id" in rendered
    revoke_all.assert_awaited_once_with(
        db,
        tenant_id="tenant-a",
        user_id="42",
        revoked_by="admin:suspend:admin",
        redis=redis,
    )


@pytest.mark.asyncio
async def test_delete_user_revokes_all_user_sessions() -> None:
    db = AsyncMock()
    redis = AsyncMock()
    user = _make_user(status="active")
    db.execute = AsyncMock(return_value=_ExecuteResult(user))
    db.flush = AsyncMock()

    with patch("backend.core.user_lifecycle.revoke_all_user_sessions", new=AsyncMock()) as revoke_all:
        updated = await delete_user(db, redis, tenant_id="tenant-a", user_id=42)

    assert updated.status == "deleted"
    assert updated.is_active is False
    rendered = str(db.execute.await_args.args[0])
    assert "users.tenant_id" in rendered
    revoke_all.assert_awaited_once_with(
        db,
        tenant_id="tenant-a",
        user_id="42",
        revoked_by="admin:delete_user",
        redis=redis,
    )


@pytest.mark.asyncio
async def test_activate_user_scopes_lookup_to_tenant() -> None:
    db = AsyncMock()
    user = _make_user(status="suspended")
    db.execute = AsyncMock(return_value=_ExecuteResult(user))
    db.flush = AsyncMock()

    updated = await activate_user(db, tenant_id="tenant-a", user_id=42)

    assert updated.status == "active"
    assert updated.is_active is True
    rendered = str(db.execute.await_args.args[0])
    assert "users.tenant_id" in rendered
