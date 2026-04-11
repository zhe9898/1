from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.control_plane.adapters import auth_user
from backend.control_plane.adapters.models.auth import CreateUserRequest
from backend.platform.redis.client import CHANNEL_USER_EVENTS


@pytest.mark.anyio
async def test_create_user_records_audit_commits_and_publishes_event() -> None:
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result)
    db.flush = AsyncMock()
    order: list[str] = []

    def _add(user: object) -> None:
        user.id = 8
        user.is_active = True

    async def _audit(*args, **kwargs) -> None:
        order.append("audit")

    async def _commit() -> None:
        order.append("commit")

    async def _publish(*args, **kwargs) -> None:
        order.append("publish")

    db.add = MagicMock(side_effect=_add)
    db.commit = AsyncMock(side_effect=_commit)
    current_admin = {"sub": "1", "username": "admin", "role": "admin", "tenant_id": "tenant-a"}

    with (
        patch("backend.control_plane.adapters.auth_user.bind_admin_scope", new=AsyncMock(return_value="tenant-a")) as bind_scope,
        patch("backend.control_plane.adapters.auth_user.log_audit", new=AsyncMock(side_effect=_audit)) as log_audit_mock,
        patch("backend.control_plane.adapters.auth_user.publish_control_event", new=AsyncMock(side_effect=_publish)) as publish_mock,
        patch("backend.control_plane.adapters.auth_user.bcrypt.gensalt", return_value=b"salt"),
        patch("backend.control_plane.adapters.auth_user.bcrypt.hashpw", return_value=b"hashed-pw"),
    ):
        response = await auth_user.create_user(
            CreateUserRequest(
                username="shared-name",
                password="Password123!",
                display_name="Shared Name",
                role="family",
                tenant_id="tenant-a",
            ),
            db,
            current_admin,
        )

    assert order == ["audit", "commit", "publish"]
    bind_scope.assert_awaited_once_with(db, current_admin)
    assert response.id == 8
    assert response.username == "shared-name"
    assert response.role == "family"
    assert response.tenant_id == "tenant-a"
    assert response.has_password is True
    assert response.webauthn_credentials == []
    assert log_audit_mock.await_args.kwargs["action"] == "user.created"
    assert log_audit_mock.await_args.kwargs["resource_id"] == "8"
    assert log_audit_mock.await_args.kwargs["details"]["target_username"] == "shared-name"
    assert log_audit_mock.await_args.kwargs["details"]["target_role"] == "family"
    assert log_audit_mock.await_args.kwargs["details"]["has_password"] is True
    assert publish_mock.await_args.args[0] == CHANNEL_USER_EVENTS
    assert publish_mock.await_args.args[1] == "user_created"
    assert publish_mock.await_args.args[2]["user"]["id"] == 8
    assert publish_mock.await_args.args[2]["user"]["tenant_id"] == "tenant-a"
    assert publish_mock.await_args.args[2]["actor"]["user_id"] == "1"
    assert publish_mock.await_args.kwargs["tenant_id"] == "tenant-a"
