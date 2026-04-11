from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.control_plane.adapters import permissions as permissions_adapter
from backend.models.permission import Permission
from backend.platform.redis.client import CHANNEL_USER_EVENTS


def _permission(*, permission_id: int = 7, user_id: str = "42", scope: str = "write:jobs") -> Permission:
    now = datetime.now(UTC).replace(tzinfo=None)
    return Permission(
        id=permission_id,
        tenant_id="tenant-a",
        user_id=user_id,
        scope=scope,
        resource_type=None,
        resource_id=None,
        granted_by="admin",
        granted_at=now,
        expires_at=None,
    )


@pytest.mark.anyio
async def test_grant_permission_endpoint_invalidates_sessions_audits_commits_and_publishes() -> None:
    db = AsyncMock()
    response = MagicMock()
    current_user = {"sub": "1", "username": "admin", "tenant_id": "tenant-a"}
    payload = permissions_adapter.PermissionGrantRequest(user_id="42", scope="write:jobs")
    permission = _permission(user_id="42", scope="write:jobs")
    order: list[str] = []

    async def _revoke(*args, **kwargs) -> int:
        order.append("revoke")
        return 2

    async def _audit(*args, **kwargs) -> None:
        order.append("audit")

    async def _commit() -> None:
        order.append("commit")

    async def _publish(*args, **kwargs) -> None:
        order.append("publish")

    db.commit = AsyncMock(side_effect=_commit)

    with (
        patch("backend.control_plane.adapters.permissions.grant_permission", new=AsyncMock(return_value=permission)),
        patch("backend.control_plane.adapters.permissions.revoke_all_user_sessions", new=AsyncMock(side_effect=_revoke)) as revoke_all,
        patch("backend.control_plane.adapters.permissions.log_audit", new=AsyncMock(side_effect=_audit)) as log_audit_mock,
        patch("backend.control_plane.adapters.permissions.publish_control_event", new=AsyncMock(side_effect=_publish)) as publish_mock,
        patch("backend.control_plane.adapters.permissions.clear_auth_cookie") as clear_cookie,
    ):
        result = await permissions_adapter.grant_permission_endpoint(payload, response, current_user, db)

    assert order == ["revoke", "audit", "commit", "publish"]
    revoke_all.assert_awaited_once_with(
        db,
        tenant_id="tenant-a",
        user_id="42",
        revoked_by="admin:permission_change:admin",
        redis=None,
    )
    assert log_audit_mock.await_args.kwargs["action"] == "permission.granted"
    assert log_audit_mock.await_args.kwargs["details"]["revoked_sessions"] == 2
    assert publish_mock.await_args.args[0] == CHANNEL_USER_EVENTS
    assert publish_mock.await_args.args[1] == "permission_granted"
    assert publish_mock.await_args.args[2]["target_user_id"] == "42"
    clear_cookie.assert_not_called()
    assert result.user_id == "42"
    assert result.scope == "write:jobs"


@pytest.mark.anyio
async def test_grant_permission_endpoint_clears_cookie_when_current_user_targets_self() -> None:
    db = AsyncMock()
    response = MagicMock()
    current_user = {"sub": "42", "username": "admin", "tenant_id": "tenant-a"}
    payload = permissions_adapter.PermissionGrantRequest(user_id="42", scope="write:jobs")
    permission = _permission(user_id="42", scope="write:jobs")
    db.commit = AsyncMock()

    with (
        patch("backend.control_plane.adapters.permissions.grant_permission", new=AsyncMock(return_value=permission)),
        patch("backend.control_plane.adapters.permissions.revoke_all_user_sessions", new=AsyncMock(return_value=1)),
        patch("backend.control_plane.adapters.permissions.log_audit", new=AsyncMock()),
        patch("backend.control_plane.adapters.permissions.publish_control_event", new=AsyncMock()),
        patch("backend.control_plane.adapters.permissions.clear_auth_cookie") as clear_cookie,
    ):
        await permissions_adapter.grant_permission_endpoint(payload, response, current_user, db)

    clear_cookie.assert_called_once_with(response)


@pytest.mark.anyio
async def test_revoke_permission_endpoint_invalidates_sessions_audits_commits_and_publishes() -> None:
    db = AsyncMock()
    response = MagicMock()
    current_user = {"sub": "1", "username": "admin", "tenant_id": "tenant-a"}
    permission = _permission(user_id="42", scope="write:jobs")
    order: list[str] = []

    async def _revoke(*args, **kwargs) -> int:
        order.append("revoke")
        return 1

    async def _audit(*args, **kwargs) -> None:
        order.append("audit")

    async def _commit() -> None:
        order.append("commit")

    async def _publish(*args, **kwargs) -> None:
        order.append("publish")

    db.commit = AsyncMock(side_effect=_commit)

    with (
        patch("backend.control_plane.adapters.permissions.revoke_permission", new=AsyncMock(return_value=permission)),
        patch("backend.control_plane.adapters.permissions.revoke_all_user_sessions", new=AsyncMock(side_effect=_revoke)) as revoke_all,
        patch("backend.control_plane.adapters.permissions.log_audit", new=AsyncMock(side_effect=_audit)) as log_audit_mock,
        patch("backend.control_plane.adapters.permissions.publish_control_event", new=AsyncMock(side_effect=_publish)) as publish_mock,
        patch("backend.control_plane.adapters.permissions.clear_auth_cookie") as clear_cookie,
    ):
        result = await permissions_adapter.revoke_permission_endpoint(7, response, current_user, db)

    assert order == ["revoke", "audit", "commit", "publish"]
    revoke_all.assert_awaited_once_with(
        db,
        tenant_id="tenant-a",
        user_id="42",
        revoked_by="admin:permission_change:admin",
        redis=None,
    )
    assert log_audit_mock.await_args.kwargs["action"] == "permission.revoked"
    assert publish_mock.await_args.args[0] == CHANNEL_USER_EVENTS
    assert publish_mock.await_args.args[1] == "permission_revoked"
    assert publish_mock.await_args.args[2]["permission"]["scope"] == "write:jobs"
    clear_cookie.assert_not_called()
    assert result == {"status": "ok", "message": "Permission revoked"}


@pytest.mark.anyio
async def test_revoke_permission_endpoint_clears_cookie_when_current_user_targets_self() -> None:
    db = AsyncMock()
    response = MagicMock()
    current_user = {"sub": "42", "username": "admin", "tenant_id": "tenant-a"}
    permission = _permission(user_id="42", scope="write:jobs")
    db.commit = AsyncMock()

    with (
        patch("backend.control_plane.adapters.permissions.revoke_permission", new=AsyncMock(return_value=permission)),
        patch("backend.control_plane.adapters.permissions.revoke_all_user_sessions", new=AsyncMock(return_value=1)),
        patch("backend.control_plane.adapters.permissions.log_audit", new=AsyncMock()),
        patch("backend.control_plane.adapters.permissions.publish_control_event", new=AsyncMock()),
        patch("backend.control_plane.adapters.permissions.clear_auth_cookie") as clear_cookie,
    ):
        await permissions_adapter.revoke_permission_endpoint(7, response, current_user, db)

    clear_cookie.assert_called_once_with(response)
