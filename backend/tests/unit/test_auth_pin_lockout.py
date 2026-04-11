from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _mock_request(client_ip: str = "192.168.1.10") -> MagicMock:
    req = MagicMock()
    req.state.request_id = "test-rid-001"
    req.client.host = client_ip
    return req


def _mock_user(pin_hash: str | None = None, username: str = "alice") -> MagicMock:
    user = MagicMock()
    user.id = "user-uuid-001"
    user.username = username
    user.role = "family"
    user.pin_hash = pin_hash
    user.tenant_id = "default"
    user.is_active = True
    return user


def _mock_redis(*, get_return: str | None = None, incr_return: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        kv=SimpleNamespace(
            get=AsyncMock(return_value=get_return),
            incr=AsyncMock(return_value=incr_return),
            expire=AsyncMock(),
            setex=AsyncMock(),
            delete=AsyncMock(),
        )
    )


def _mock_db(user: Any = None) -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    db.execute = AsyncMock(return_value=result)
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


def _pin_request(username: str = "alice", pin: str = "12345678") -> MagicMock:
    req = MagicMock()
    req.username = username
    req.pin = pin
    req.tenant_id = "default"
    return req


class TestPinLockout:
    @pytest.mark.asyncio
    async def test_lockout_rejects_when_frozen(self) -> None:
        from backend.control_plane.adapters.auth import PIN_RATE_LIMIT_WINDOW, pin_login

        redis = _mock_redis(get_return="1")
        db = _mock_db()
        req = _pin_request()
        request = _mock_request()
        response = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            await pin_login(req, request, response, db=db, redis=redis)

        assert exc_info.value.status_code == 429
        assert "ZEN-AUTH-429" in str(exc_info.value.detail)
        assert str(PIN_RATE_LIMIT_WINDOW // 60) in str(exc_info.value.detail)
        db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_wrong_pin_increments_counter(self) -> None:
        import bcrypt

        from backend.control_plane.adapters.auth import pin_login

        correct_hash = bcrypt.hashpw(b"99999999", bcrypt.gensalt(rounds=4)).decode("utf-8")
        user = _mock_user(pin_hash=correct_hash)
        redis = _mock_redis(get_return=None, incr_return=1)
        db = _mock_db(user=user)
        req = _pin_request(pin="00000000")
        request = _mock_request()
        response = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            await pin_login(req, request, response, db=db, redis=redis)

        assert exc_info.value.status_code == 401
        redis.kv.incr.assert_awaited_once()
        redis.kv.expire.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fifth_failure_triggers_freeze(self) -> None:
        import bcrypt

        from backend.control_plane.adapters.auth import PIN_RATE_LIMIT_MAX, PIN_RATE_LIMIT_WINDOW, pin_login

        correct_hash = bcrypt.hashpw(b"99999999", bcrypt.gensalt(rounds=4)).decode("utf-8")
        user = _mock_user(pin_hash=correct_hash)
        redis = _mock_redis(get_return=None, incr_return=PIN_RATE_LIMIT_MAX)
        db = _mock_db(user=user)
        req = _pin_request(pin="00000000")
        request = _mock_request()
        response = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            await pin_login(req, request, response, db=db, redis=redis)

        assert exc_info.value.status_code == 429
        assert str(PIN_RATE_LIMIT_WINDOW // 60) in str(exc_info.value.detail)
        redis.kv.setex.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_successful_pin_clears_counter(self) -> None:
        import bcrypt

        from backend.control_plane.adapters.auth import pin_login

        pin = "12345678"
        pin_hash = bcrypt.hashpw(pin.encode("utf-8"), bcrypt.gensalt(rounds=4)).decode("utf-8")
        user = _mock_user(pin_hash=pin_hash)
        redis = _mock_redis(get_return=None)
        db = _mock_db(user=user)
        req = _pin_request(pin=pin)
        request = _mock_request()
        response = MagicMock()

        with patch(
            "backend.control_plane.adapters.auth.token_response",
            return_value={"access_token": "test-tok", "token_type": "bearer", "expires_in": 900},
        ):
            await pin_login(req, request, response, db=db, redis=redis)

        assert redis.kv.delete.await_count >= 2

    @pytest.mark.asyncio
    async def test_public_ip_rejected(self) -> None:
        from backend.control_plane.adapters.auth import pin_login

        redis = _mock_redis(get_return=None)
        db = _mock_db()
        req = _pin_request()
        request = _mock_request(client_ip="8.8.8.8")
        response = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            await pin_login(req, request, response, db=db, redis=redis)

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_disabled_user_rejected_before_token_issue(self) -> None:
        import bcrypt

        from backend.control_plane.adapters.auth import pin_login

        pin_hash = bcrypt.hashpw(b"12345678", bcrypt.gensalt(rounds=4)).decode("utf-8")
        user = _mock_user(pin_hash=pin_hash)
        user.is_active = False
        redis = _mock_redis(get_return=None)
        db = _mock_db(user=user)
        req = _pin_request(pin="12345678")
        request = _mock_request()
        response = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            await pin_login(req, request, response, db=db, redis=redis)

        assert exc_info.value.status_code == 403
