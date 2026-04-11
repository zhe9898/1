from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from backend.control_plane.adapters import user_management
from backend.platform.redis.client import CHANNEL_USER_EVENTS


def _make_user(*, status: str) -> object:
    from backend.models.user import User

    now = datetime.now(UTC).replace(tzinfo=None)
    user = User(
        id=42,
        tenant_id="tenant-a",
        username="alice",
        display_name="Alice",
        role="user",
        status=status,
        is_active=status == "active",
        created_at=now,
    )
    if status == "suspended":
        user.suspended_at = now
        user.suspended_by = "admin"
        user.suspended_reason = "policy"
        user.is_active = False
    if status == "deleted":
        user.deleted_at = now
        user.is_active = False
    return user


@pytest.mark.anyio
async def test_suspend_user_endpoint_records_audit_commits_and_publishes_event() -> None:
    db = AsyncMock()
    order: list[str] = []

    async def _commit() -> None:
        order.append("commit")

    async def _audit(*args, **kwargs) -> None:
        order.append("audit")

    async def _publish(*args, **kwargs) -> None:
        order.append("publish")

    db.commit = AsyncMock(side_effect=_commit)
    current_user = {"sub": "admin-1", "username": "admin", "tenant_id": "tenant-a"}
    user = _make_user(status="suspended")

    with (
        patch("backend.control_plane.adapters.user_management.suspend_user", new=AsyncMock(return_value=user)),
        patch("backend.control_plane.adapters.user_management.log_audit", new=AsyncMock(side_effect=_audit)) as log_audit_mock,
        patch("backend.control_plane.adapters.user_management.publish_control_event", new=AsyncMock(side_effect=_publish)) as publish_mock,
    ):
        result = await user_management.suspend_user_endpoint(42, user_management.UserSuspendRequest(reason="policy"), current_user, db, None)

    assert order == ["audit", "commit", "publish"]
    assert result.status == "suspended"
    assert result.suspended_reason == "policy"
    assert log_audit_mock.await_args.kwargs["action"] == "user.suspended"
    assert log_audit_mock.await_args.kwargs["resource_id"] == "42"
    assert log_audit_mock.await_args.kwargs["details"]["reason"] == "policy"
    assert publish_mock.await_args.args[0] == CHANNEL_USER_EVENTS
    assert publish_mock.await_args.args[1] == "suspended"
    assert publish_mock.await_args.args[2]["user"]["status"] == "suspended"
    assert publish_mock.await_args.kwargs["tenant_id"] == "tenant-a"


@pytest.mark.anyio
async def test_activate_user_endpoint_records_audit_commits_and_publishes_event() -> None:
    db = AsyncMock()
    order: list[str] = []

    async def _commit() -> None:
        order.append("commit")

    async def _audit(*args, **kwargs) -> None:
        order.append("audit")

    async def _publish(*args, **kwargs) -> None:
        order.append("publish")

    db.commit = AsyncMock(side_effect=_commit)
    current_user = {"sub": "admin-1", "username": "admin", "tenant_id": "tenant-a"}
    user = _make_user(status="active")

    with (
        patch("backend.control_plane.adapters.user_management.activate_user", new=AsyncMock(return_value=user)),
        patch("backend.control_plane.adapters.user_management.log_audit", new=AsyncMock(side_effect=_audit)) as log_audit_mock,
        patch("backend.control_plane.adapters.user_management.publish_control_event", new=AsyncMock(side_effect=_publish)) as publish_mock,
    ):
        result = await user_management.activate_user_endpoint(42, current_user, db)

    assert order == ["audit", "commit", "publish"]
    assert result.status == "active"
    assert log_audit_mock.await_args.kwargs["action"] == "user.activated"
    assert "reason" not in log_audit_mock.await_args.kwargs["details"]
    assert publish_mock.await_args.args[0] == CHANNEL_USER_EVENTS
    assert publish_mock.await_args.args[1] == "activated"
    assert publish_mock.await_args.args[2]["user"]["status"] == "active"
    assert publish_mock.await_args.kwargs["tenant_id"] == "tenant-a"


@pytest.mark.anyio
async def test_delete_user_endpoint_records_audit_commits_and_publishes_event() -> None:
    db = AsyncMock()
    order: list[str] = []

    async def _commit() -> None:
        order.append("commit")

    async def _audit(*args, **kwargs) -> None:
        order.append("audit")

    async def _publish(*args, **kwargs) -> None:
        order.append("publish")

    db.commit = AsyncMock(side_effect=_commit)
    current_user = {"sub": "admin-1", "username": "admin", "tenant_id": "tenant-a"}
    user = _make_user(status="deleted")

    with (
        patch("backend.control_plane.adapters.user_management.delete_user", new=AsyncMock(return_value=user)),
        patch("backend.control_plane.adapters.user_management.log_audit", new=AsyncMock(side_effect=_audit)) as log_audit_mock,
        patch("backend.control_plane.adapters.user_management.publish_control_event", new=AsyncMock(side_effect=_publish)) as publish_mock,
    ):
        result = await user_management.delete_user_endpoint(42, current_user, db, None)

    assert order == ["audit", "commit", "publish"]
    assert result.status == "deleted"
    assert log_audit_mock.await_args.kwargs["action"] == "user.deleted"
    assert publish_mock.await_args.args[0] == CHANNEL_USER_EVENTS
    assert publish_mock.await_args.args[1] == "deleted"
    assert publish_mock.await_args.args[2]["user"]["status"] == "deleted"
    assert publish_mock.await_args.kwargs["tenant_id"] == "tenant-a"
