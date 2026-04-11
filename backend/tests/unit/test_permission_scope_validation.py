from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from backend.control_plane.auth.permissions import ALLOWED_SCOPES, grant_permission, revoke_permission


def _execute_result(
    *,
    scalar_one_or_none: object | None = None,
    first: object | None = None,
) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar_one_or_none
    result.scalars.return_value.first.return_value = first
    return result


@pytest.mark.asyncio
async def test_grant_permission_rejects_unknown_scope() -> None:
    db = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await grant_permission(
            db,
            tenant_id="tenant-a",
            user_id="1",
            scope="read:anything",
            granted_by="admin",
        )

    assert exc.value.status_code == 400
    assert "ZEN-PERM-4001" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_grant_permission_accepts_allowed_scope() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _execute_result(scalar_one_or_none=MagicMock(id=1)),
            _execute_result(first=None),
        ]
    )
    db.add = MagicMock()
    db.flush = AsyncMock()

    scope = sorted(ALLOWED_SCOPES)[0]
    permission = await grant_permission(
        db,
        tenant_id="tenant-a",
        user_id="1",
        scope=scope,
        granted_by="admin",
    )

    assert permission.scope == scope
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_grant_permission_rejects_cross_tenant_target_user() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_execute_result(scalar_one_or_none=None))

    with pytest.raises(HTTPException) as exc:
        await grant_permission(
            db,
            tenant_id="tenant-a",
            user_id="42",
            scope="write:jobs",
            granted_by="admin",
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_grant_permission_rejects_non_future_expiry() -> None:
    db = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await grant_permission(
            db,
            tenant_id="tenant-a",
            user_id="42",
            scope="write:jobs",
            granted_by="admin",
            expires_at=datetime.now(UTC) - timedelta(minutes=5),
        )

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_revoke_permission_scopes_lookup_to_tenant() -> None:
    permission = MagicMock()
    permission.user_id = "42"
    permission.scope = "write:jobs"
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_execute_result(first=permission))
    db.delete = AsyncMock()
    db.flush = AsyncMock()

    revoked = await revoke_permission(db, 7, tenant_id="tenant-a")

    stmt = db.execute.await_args.args[0]
    rendered = str(stmt)
    assert "permissions.id" in rendered
    assert "permissions.tenant_id" in rendered
    assert revoked is permission
