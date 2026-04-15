from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.control_plane.adapters import sessions as sessions_adapter
from backend.platform.redis.client import CHANNEL_SESSION_EVENTS


@pytest.mark.anyio
async def test_revoke_my_session_scopes_revocation_to_current_user() -> None:
    db = AsyncMock()
    redis = object()
    response = MagicMock()
    current_user = {
        "sub": "user-1",
        "username": "alice",
        "tenant_id": "tenant-a",
    }
    session = MagicMock()
    session.session_id = "session-1"
    session.user_id = "user-1"
    session.username = "alice"

    with (
        patch("backend.control_plane.adapters.sessions.revoke_owned_session", new=AsyncMock(return_value=session)) as revoke_owned,
        patch("backend.control_plane.adapters.sessions.log_audit", new=AsyncMock()),
        patch("backend.control_plane.adapters.sessions.publish_control_event", new=AsyncMock()),
    ):
        result = await sessions_adapter.revoke_my_session("session-1", response, current_user, db, redis)

    revoke_owned.assert_awaited_once_with(
        db,
        "session-1",
        tenant_id="tenant-a",
        user_id="user-1",
        revoked_by="alice",
        redis=redis,
    )
    assert result == {"status": "ok", "message": "Session revoked"}


@pytest.mark.anyio
async def test_revoke_my_session_records_audit_commits_cookie_clear_and_publishes_event() -> None:
    db = AsyncMock()
    db.commit = AsyncMock()
    redis = object()
    response = MagicMock()
    current_user = {
        "sub": "user-1",
        "username": "alice",
        "tenant_id": "tenant-a",
        "sid": "session-1",
    }
    session = MagicMock()
    session.session_id = "session-1"
    session.user_id = "user-1"
    session.username = "alice"
    order: list[str] = []

    async def _audit(*args, **kwargs) -> None:
        order.append("audit")

    async def _commit() -> None:
        order.append("commit")

    def _clear_cookie(*args, **kwargs) -> None:
        order.append("clear_cookie")

    async def _publish(*args, **kwargs) -> None:
        order.append("publish")

    db.commit = AsyncMock(side_effect=_commit)

    with (
        patch("backend.control_plane.adapters.sessions.revoke_owned_session", new=AsyncMock(return_value=session)),
        patch("backend.control_plane.adapters.sessions.log_audit", new=AsyncMock(side_effect=_audit)) as log_audit_mock,
        patch("backend.control_plane.adapters.sessions.clear_auth_cookie", side_effect=_clear_cookie) as clear_cookie,
        patch("backend.control_plane.adapters.sessions.publish_control_event", new=AsyncMock(side_effect=_publish)) as publish_mock,
    ):
        result = await sessions_adapter.revoke_my_session("session-1", response, current_user, db, redis)

    assert order == ["audit", "commit", "clear_cookie", "publish"]
    assert log_audit_mock.await_args.kwargs["action"] == "auth.session.revoked"
    assert log_audit_mock.await_args.kwargs["details"]["session_id"] == "session-1"
    assert log_audit_mock.await_args.kwargs["details"]["current_session_affected"] is True
    clear_cookie.assert_called_once_with(response)
    assert publish_mock.await_args.args[0] == CHANNEL_SESSION_EVENTS
    assert publish_mock.await_args.args[1] == "session_revoked"
    assert publish_mock.await_args.args[2]["session"]["id"] == "session-1"
    assert publish_mock.await_args.kwargs["tenant_id"] == "tenant-a"
    assert result == {"status": "ok", "message": "Session revoked"}


@pytest.mark.anyio
async def test_revoke_my_session_clears_cookie_when_current_session_is_revoked() -> None:
    db = AsyncMock()
    redis = object()
    response = MagicMock()
    current_user = {
        "sub": "user-1",
        "username": "alice",
        "tenant_id": "tenant-a",
        "sid": "session-1",
    }

    session = MagicMock()
    session.session_id = "session-1"
    session.user_id = "user-1"
    session.username = "alice"

    with (
        patch("backend.control_plane.adapters.sessions.revoke_owned_session", new=AsyncMock(return_value=session)) as revoke_owned,
        patch("backend.control_plane.adapters.sessions.log_audit", new=AsyncMock()),
        patch("backend.control_plane.adapters.sessions.clear_auth_cookie") as clear_cookie,
        patch("backend.control_plane.adapters.sessions.publish_control_event", new=AsyncMock()),
    ):
        result = await sessions_adapter.revoke_my_session("session-1", response, current_user, db, redis)

    revoke_owned.assert_awaited_once()
    clear_cookie.assert_called_once_with(response)
    assert result == {"status": "ok", "message": "Session revoked"}


@pytest.mark.anyio
async def test_revoke_my_session_keeps_cookie_when_revoking_another_device() -> None:
    db = AsyncMock()
    redis = object()
    response = MagicMock()
    current_user = {
        "sub": "user-1",
        "username": "alice",
        "tenant_id": "tenant-a",
        "sid": "session-current",
    }

    session = MagicMock()
    session.session_id = "session-other"
    session.user_id = "user-1"
    session.username = "alice"

    with (
        patch("backend.control_plane.adapters.sessions.revoke_owned_session", new=AsyncMock(return_value=session)) as revoke_owned,
        patch("backend.control_plane.adapters.sessions.log_audit", new=AsyncMock()),
        patch("backend.control_plane.adapters.sessions.clear_auth_cookie") as clear_cookie,
        patch("backend.control_plane.adapters.sessions.publish_control_event", new=AsyncMock()),
    ):
        result = await sessions_adapter.revoke_my_session("session-other", response, current_user, db, redis)

    revoke_owned.assert_awaited_once()
    clear_cookie.assert_not_called()
    assert result == {"status": "ok", "message": "Session revoked"}


@pytest.mark.anyio
async def test_revoke_all_my_sessions_clears_cookie_after_logout_everywhere() -> None:
    db = AsyncMock()
    redis = object()
    response = MagicMock()
    current_user = {
        "sub": "user-1",
        "username": "alice",
        "tenant_id": "tenant-a",
        "sid": "session-current",
    }

    with (
        patch("backend.control_plane.adapters.sessions.revoke_all_user_sessions", new=AsyncMock(return_value=3)) as revoke_all,
        patch("backend.control_plane.adapters.sessions.log_audit", new=AsyncMock()),
        patch("backend.control_plane.adapters.sessions.clear_auth_cookie") as clear_cookie,
        patch("backend.control_plane.adapters.sessions.publish_control_event", new=AsyncMock()),
    ):
        result = await sessions_adapter.revoke_all_my_sessions(response, current_user, db, redis)

    revoke_all.assert_awaited_once_with(
        db,
        tenant_id="tenant-a",
        user_id="user-1",
        revoked_by="alice",
        redis=redis,
    )
    clear_cookie.assert_called_once_with(response)
    assert result == {"status": "ok", "revoked": 3}


@pytest.mark.anyio
async def test_revoke_all_my_sessions_records_audit_commits_cookie_clear_and_publishes_event() -> None:
    db = AsyncMock()
    redis = object()
    response = MagicMock()
    current_user = {
        "sub": "user-1",
        "username": "alice",
        "tenant_id": "tenant-a",
        "sid": "session-current",
    }
    order: list[str] = []

    async def _audit(*args, **kwargs) -> None:
        order.append("audit")

    async def _commit() -> None:
        order.append("commit")

    def _clear_cookie(*args, **kwargs) -> None:
        order.append("clear_cookie")

    async def _publish(*args, **kwargs) -> None:
        order.append("publish")

    db.commit = AsyncMock(side_effect=_commit)

    with (
        patch("backend.control_plane.adapters.sessions.revoke_all_user_sessions", new=AsyncMock(return_value=3)),
        patch("backend.control_plane.adapters.sessions.log_audit", new=AsyncMock(side_effect=_audit)) as log_audit_mock,
        patch("backend.control_plane.adapters.sessions.clear_auth_cookie", side_effect=_clear_cookie) as clear_cookie,
        patch("backend.control_plane.adapters.sessions.publish_control_event", new=AsyncMock(side_effect=_publish)) as publish_mock,
    ):
        result = await sessions_adapter.revoke_all_my_sessions(response, current_user, db, redis)

    assert order == ["audit", "commit", "clear_cookie", "publish"]
    assert log_audit_mock.await_args.kwargs["action"] == "auth.session.revoked_all"
    assert log_audit_mock.await_args.kwargs["details"]["revoked_sessions"] == 3
    assert log_audit_mock.await_args.kwargs["details"]["current_session_affected"] is True
    clear_cookie.assert_called_once_with(response)
    assert publish_mock.await_args.args[0] == CHANNEL_SESSION_EVENTS
    assert publish_mock.await_args.args[1] == "sessions_revoked"
    assert publish_mock.await_args.args[2]["target_user_id"] == "user-1"
    assert publish_mock.await_args.kwargs["tenant_id"] == "tenant-a"
    assert result == {"status": "ok", "revoked": 3}


@pytest.mark.anyio
async def test_revoke_all_user_sessions_admin_records_audit_commits_and_publishes_event() -> None:
    db = AsyncMock()
    redis = object()
    response = MagicMock()
    current_user = {
        "sub": "admin-1",
        "username": "admin",
        "tenant_id": "tenant-a",
        "sid": "session-admin",
    }
    order: list[str] = []

    async def _audit(*args, **kwargs) -> None:
        order.append("audit")

    async def _commit() -> None:
        order.append("commit")

    async def _publish(*args, **kwargs) -> None:
        order.append("publish")

    db.commit = AsyncMock(side_effect=_commit)

    with (
        patch("backend.control_plane.adapters.sessions.revoke_all_user_sessions", new=AsyncMock(return_value=2)) as revoke_all,
        patch("backend.control_plane.adapters.sessions.log_audit", new=AsyncMock(side_effect=_audit)) as log_audit_mock,
        patch("backend.control_plane.adapters.sessions.clear_auth_cookie") as clear_cookie,
        patch("backend.control_plane.adapters.sessions.publish_control_event", new=AsyncMock(side_effect=_publish)) as publish_mock,
    ):
        result = await sessions_adapter.revoke_all_user_sessions_admin("user-9", response, current_user, db, redis)

    assert order == ["audit", "commit", "publish"]
    revoke_all.assert_awaited_once_with(
        db,
        tenant_id="tenant-a",
        user_id="user-9",
        revoked_by="admin",
        redis=redis,
    )
    assert log_audit_mock.await_args.kwargs["action"] == "auth.session.user_revoked_all"
    assert log_audit_mock.await_args.kwargs["details"]["target_user_id"] == "user-9"
    clear_cookie.assert_not_called()
    assert publish_mock.await_args.args[0] == CHANNEL_SESSION_EVENTS
    assert publish_mock.await_args.args[1] == "user_sessions_revoked"
    assert publish_mock.await_args.args[2]["target_user_id"] == "user-9"
    assert publish_mock.await_args.kwargs["tenant_id"] == "tenant-a"
    assert result == {"status": "ok", "revoked": 2}


@pytest.mark.anyio
async def test_revoke_all_user_sessions_admin_clears_cookie_when_current_admin_targets_self() -> None:
    db = AsyncMock()
    redis = object()
    response = MagicMock()
    current_user = {
        "sub": "admin-1",
        "username": "admin",
        "tenant_id": "tenant-a",
        "sid": "session-admin",
    }
    db.commit = AsyncMock()

    with (
        patch("backend.control_plane.adapters.sessions.revoke_all_user_sessions", new=AsyncMock(return_value=1)),
        patch("backend.control_plane.adapters.sessions.log_audit", new=AsyncMock()),
        patch("backend.control_plane.adapters.sessions.clear_auth_cookie") as clear_cookie,
        patch("backend.control_plane.adapters.sessions.publish_control_event", new=AsyncMock()),
    ):
        await sessions_adapter.revoke_all_user_sessions_admin("admin-1", response, current_user, db, redis)

    clear_cookie.assert_called_once_with(response)
