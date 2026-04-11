from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import bcrypt
import pytest

from backend.control_plane.adapters import auth_pin, auth_user
from backend.control_plane.adapters.models.auth import PinSetRequest
from backend.models.user import User, WebAuthnCredential
from backend.platform.redis.client import CHANNEL_USER_EVENTS


def _request() -> MagicMock:
    request = MagicMock()
    request.state.request_id = "rid-credential-mutation"
    request.client.host = "192.168.1.10"
    return request


def _user(*, user_id: int = 42, tenant_id: str = "tenant-a", username: str = "alice", pin_hash: str | None = None) -> User:
    user = User(
        username=username,
        display_name="Alice",
        role="admin",
        tenant_id=tenant_id,
        password_hash="pw-hash",
        pin_hash=pin_hash,
        ai_route_preference="auto",
        is_active=True,
        status="active",
    )
    user.id = user_id
    return user


def _credential(*, user_id: int = 42, credential_id: str = "cred-1", device_name: str = "YubiKey") -> WebAuthnCredential:
    credential = WebAuthnCredential(
        user_id=user_id,
        credential_id=credential_id,
        public_key=b"pk",
        sign_count=7,
        device_name=device_name,
        transports=["usb"],
    )
    credential.id = 9
    credential.created_at = datetime.now(UTC).replace(tzinfo=None)
    return credential


@pytest.mark.anyio
async def test_pin_set_invalidates_sessions_audits_commits_clears_cookie_and_publishes() -> None:
    old_hash = bcrypt.hashpw(b"12345678", bcrypt.gensalt(rounds=4)).decode("utf-8")
    user = _user(pin_hash=old_hash)
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    db.execute = AsyncMock(return_value=result)
    response = MagicMock()
    current_user = {"sub": "42", "username": "alice", "tenant_id": "tenant-a"}
    order: list[str] = []

    async def _revoke(*args, **kwargs) -> int:
        order.append("revoke")
        return 3

    async def _audit(*args, **kwargs) -> None:
        order.append("audit")

    async def _commit() -> None:
        order.append("commit")

    async def _publish(*args, **kwargs) -> None:
        order.append("publish")

    def _clear_cookie(*args, **kwargs) -> None:
        order.append("clear_cookie")

    db.commit = AsyncMock(side_effect=_commit)

    with (
        patch("backend.control_plane.adapters.auth_pin.hash_pin", return_value="new-pin-hash"),
        patch("backend.control_plane.adapters.auth_pin.revoke_all_user_sessions", new=AsyncMock(side_effect=_revoke)) as revoke_all,
        patch("backend.control_plane.adapters.auth_pin.log_audit", new=AsyncMock(side_effect=_audit)) as log_audit_mock,
        patch("backend.control_plane.adapters.auth_pin.publish_control_event", new=AsyncMock(side_effect=_publish)) as publish_mock,
        patch("backend.control_plane.adapters.auth_pin.clear_auth_cookie", side_effect=_clear_cookie) as clear_cookie,
    ):
        result_payload = await auth_pin.pin_set(
            PinSetRequest(pin_old="12345678", pin_new="87654321"),
            _request(),
            response,
            db,
            current_user,
        )

    assert order == ["revoke", "audit", "commit", "clear_cookie", "publish"]
    assert user.pin_hash == "new-pin-hash"
    revoke_all.assert_awaited_once_with(
        db,
        tenant_id="tenant-a",
        user_id="42",
        revoked_by="user:pin_change:alice",
        redis=None,
    )
    assert log_audit_mock.await_args.kwargs["action"] == "auth.pin.updated"
    assert log_audit_mock.await_args.kwargs["details"]["had_existing_pin"] is True
    assert log_audit_mock.await_args.kwargs["details"]["revoked_sessions"] == 3
    assert publish_mock.await_args.args[0] == CHANNEL_USER_EVENTS
    assert publish_mock.await_args.args[1] == "pin_updated"
    assert publish_mock.await_args.args[2]["target_user_id"] == "42"
    assert publish_mock.await_args.args[2]["revoked_sessions"] == 3
    clear_cookie.assert_called_once_with(response)
    assert result_payload == {"status": "ok", "message": "PIN updated"}


@pytest.mark.anyio
async def test_revoke_credential_invalidates_sessions_audits_commits_and_publishes() -> None:
    user = _user()
    credential = _credential()
    db = AsyncMock()
    result = MagicMock()
    result.first.return_value = (credential, user)
    db.execute = AsyncMock(return_value=result)
    db.delete = AsyncMock()
    response = MagicMock()
    current_admin = {"sub": "1", "username": "admin", "role": "admin", "tenant_id": "tenant-a"}
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
        patch("backend.control_plane.adapters.auth_user.bind_admin_scope", new=AsyncMock(return_value="tenant-a")),
        patch("backend.control_plane.adapters.auth_user.revoke_all_user_sessions", new=AsyncMock(side_effect=_revoke)) as revoke_all,
        patch("backend.control_plane.adapters.auth_user.log_audit", new=AsyncMock(side_effect=_audit)) as log_audit_mock,
        patch("backend.control_plane.adapters.auth_user.publish_control_event", new=AsyncMock(side_effect=_publish)) as publish_mock,
        patch("backend.control_plane.adapters.auth_user.clear_auth_cookie") as clear_cookie,
    ):
        result_payload = await auth_user.revoke_credential("cred-1", response, db, current_admin)

    assert order == ["revoke", "audit", "commit", "publish"]
    db.delete.assert_awaited_once_with(credential)
    revoke_all.assert_awaited_once_with(
        db,
        tenant_id="tenant-a",
        user_id="42",
        revoked_by="admin:credential_revoke:admin",
        redis=None,
    )
    assert log_audit_mock.await_args.kwargs["action"] == "auth.webauthn.credential.revoked"
    assert log_audit_mock.await_args.kwargs["details"]["credential_id"] == "cred-1"
    assert log_audit_mock.await_args.kwargs["details"]["revoked_sessions"] == 2
    assert publish_mock.await_args.args[0] == CHANNEL_USER_EVENTS
    assert publish_mock.await_args.args[1] == "webauthn_credential_revoked"
    assert publish_mock.await_args.args[2]["credential"]["id"] == "cred-1"
    assert publish_mock.await_args.args[2]["target_user_id"] == "42"
    clear_cookie.assert_not_called()
    assert result_payload == {"status": "ok", "message": "Credential revoked successfully"}


@pytest.mark.anyio
async def test_revoke_credential_clears_cookie_when_current_admin_targets_self() -> None:
    user = _user()
    credential = _credential()
    db = AsyncMock()
    result = MagicMock()
    result.first.return_value = (credential, user)
    db.execute = AsyncMock(return_value=result)
    db.delete = AsyncMock()
    db.commit = AsyncMock()
    response = MagicMock()
    current_admin = {"sub": "42", "username": "admin", "role": "admin", "tenant_id": "tenant-a"}

    with (
        patch("backend.control_plane.adapters.auth_user.bind_admin_scope", new=AsyncMock(return_value="tenant-a")),
        patch("backend.control_plane.adapters.auth_user.revoke_all_user_sessions", new=AsyncMock(return_value=1)),
        patch("backend.control_plane.adapters.auth_user.log_audit", new=AsyncMock()),
        patch("backend.control_plane.adapters.auth_user.publish_control_event", new=AsyncMock()),
        patch("backend.control_plane.adapters.auth_user.clear_auth_cookie") as clear_cookie,
    ):
        await auth_user.revoke_credential("cred-1", response, db, current_admin)

    clear_cookie.assert_called_once_with(response)
