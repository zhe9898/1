from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from backend.core.permissions import ALLOWED_SCOPES, grant_permission


@pytest.mark.asyncio
async def test_grant_permission_rejects_unknown_scope() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(first=MagicMock(return_value=None)))))

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
    existing = MagicMock()
    existing.scalars.return_value.first.return_value = None
    db.execute = AsyncMock(return_value=existing)
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
