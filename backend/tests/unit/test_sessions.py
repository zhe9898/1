from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from backend.control_plane.auth.sessions import create_session, revoke_owned_session, revoke_session
from backend.models.session import Session


def _active_session(*, jti: str, expires_at: datetime.datetime) -> Session:
    return Session(
        session_id=f"session-{jti}",
        tenant_id="tenant-a",
        user_id="user-1",
        username="alice",
        jti=jti,
        auth_method="password",
        is_active=True,
        created_at=expires_at - datetime.timedelta(minutes=10),
        last_seen_at=expires_at - datetime.timedelta(minutes=5),
        expires_at=expires_at,
    )


def _db_with_sessions(sessions: list[Session]) -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    scalar_result = MagicMock()
    scalar_result.all.return_value = sessions
    result.scalars.return_value = scalar_result
    db.execute.return_value = result
    db.flush = AsyncMock()
    db.add = MagicMock()
    return db


@pytest.mark.anyio
async def test_create_session_blacklists_evicted_session_jti() -> None:
    expires_at = datetime.datetime.now(datetime.UTC).replace(tzinfo=None) + datetime.timedelta(minutes=30)
    old_session = _active_session(jti="old-jti", expires_at=expires_at)
    db = _db_with_sessions([old_session])
    redis = MagicMock()
    redis.kv = AsyncMock()
    redis.kv.set = AsyncMock(return_value=True)

    session = await create_session(
        db,
        tenant_id="tenant-a",
        user_id="user-1",
        username="alice",
        jti="new-jti",
        ip_address="127.0.0.1",
        user_agent="test-agent",
        auth_method="password",
        expires_in_seconds=900,
        max_concurrent=1,
        redis=redis,
    )

    assert session.jti == "new-jti"
    assert old_session.is_active is False
    assert old_session.revoked_by == "system:concurrent_limit"
    redis.kv.set.assert_awaited_once()
    key = redis.kv.set.await_args.args[0]
    assert key == "jwt:blacklist:old-jti"


@pytest.mark.anyio
async def test_create_session_without_eviction_does_not_touch_blacklist() -> None:
    db = _db_with_sessions([])
    redis = MagicMock()
    redis.kv = AsyncMock()
    redis.kv.set = AsyncMock(return_value=True)

    await create_session(
        db,
        tenant_id="tenant-a",
        user_id="user-1",
        username="alice",
        jti="fresh-jti",
        ip_address="127.0.0.1",
        user_agent="test-agent",
        auth_method="password",
        expires_in_seconds=900,
        max_concurrent=2,
        redis=redis,
    )

    redis.kv.set.assert_not_awaited()


@pytest.mark.anyio
async def test_create_session_blacklist_failures_do_not_block_session_creation() -> None:
    expires_at = datetime.datetime.now(datetime.UTC).replace(tzinfo=None) + datetime.timedelta(minutes=30)
    old_session = _active_session(jti="old-jti", expires_at=expires_at)
    db = _db_with_sessions([old_session])
    redis = MagicMock()
    redis.kv = AsyncMock()
    redis.kv.set = AsyncMock(side_effect=OSError("redis down"))

    session = await create_session(
        db,
        tenant_id="tenant-a",
        user_id="user-1",
        username="alice",
        jti="new-jti",
        ip_address="127.0.0.1",
        user_agent="test-agent",
        auth_method="password",
        expires_in_seconds=900,
        max_concurrent=1,
        redis=redis,
    )

    assert session.jti == "new-jti"
    db.add.assert_called_once()
    db.flush.assert_awaited_once()


@pytest.mark.anyio
async def test_revoke_session_blacklist_failures_do_not_block_revocation() -> None:
    session = _active_session(
        jti="live-jti",
        expires_at=datetime.datetime.now(datetime.UTC).replace(tzinfo=None) + datetime.timedelta(minutes=30),
    )
    db = AsyncMock()
    result = MagicMock()
    scalar_result = MagicMock()
    scalar_result.first.return_value = session
    result.scalars.return_value = scalar_result
    db.execute.return_value = result
    db.flush = AsyncMock()
    redis = SimpleNamespace(kv=SimpleNamespace(set=AsyncMock(side_effect=OSError("redis down"))))

    revoked = await revoke_session(
        db,
        session.session_id,
        tenant_id="tenant-a",
        revoked_by="alice",
        redis=redis,
    )

    assert revoked is session
    assert session.is_active is False
    assert session.revoked_by == "alice"


@pytest.mark.anyio
async def test_revoke_owned_session_rejects_cross_user_session_lookup() -> None:
    db = AsyncMock()
    result = MagicMock()
    scalar_result = MagicMock()
    scalar_result.first.return_value = None
    result.scalars.return_value = scalar_result
    db.execute.return_value = result
    db.flush = AsyncMock()
    redis = SimpleNamespace(kv=SimpleNamespace(set=AsyncMock()))

    with pytest.raises(HTTPException) as exc_info:
        await revoke_owned_session(
            db,
            "session-foreign",
            tenant_id="tenant-a",
            user_id="user-1",
            revoked_by="alice",
            redis=redis,
        )

    assert exc_info.value.status_code == 404
    db.flush.assert_not_awaited()
    redis.kv.set.assert_not_awaited()
