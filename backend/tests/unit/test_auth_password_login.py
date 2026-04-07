from unittest.mock import AsyncMock, MagicMock

import bcrypt
import pytest
from fastapi import HTTPException, Request
from sqlalchemy.exc import ProgrammingError

from backend.api.auth import password_login
from backend.api.models.auth import PasswordLoginRequest


def _mock_redis():
    redis = AsyncMock()
    redis.get.return_value = None
    redis.incr.return_value = 1
    redis.delete = AsyncMock()
    return redis


def _mock_db(user):
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    db.execute.return_value = result
    return db


@pytest.mark.asyncio
async def test_password_login_success():
    hashed = bcrypt.hashpw(b"Password123!", bcrypt.gensalt(rounds=4)).decode("utf-8")
    user = MagicMock()
    user.id = 1
    user.username = "admin"
    user.password_hash = hashed
    user.role = "admin"
    user.tenant_id = "default"
    user.ai_route_preference = "auto"
    user.is_active = True

    req = PasswordLoginRequest(username="admin", password="Password123!")
    request = MagicMock(spec=Request)
    request.state.request_id = "test-123"
    request.client.host = "127.0.0.1"
    response = MagicMock()

    db = _mock_db(user)
    redis = _mock_redis()

    resp = await password_login(req, request, response, db=db, redis=redis)
    assert resp is not None
    assert resp.access_token is not None


@pytest.mark.asyncio
async def test_password_login_accepts_bytes_hash():
    hashed = bcrypt.hashpw(b"Password123!", bcrypt.gensalt(rounds=4))
    user = MagicMock()
    user.id = 1
    user.username = "admin"
    user.password_hash = hashed
    user.role = "admin"
    user.tenant_id = "default"
    user.ai_route_preference = "auto"
    user.is_active = True

    req = PasswordLoginRequest(username="admin", password="Password123!")
    request = MagicMock(spec=Request)
    request.state.request_id = "test-123"
    request.client.host = "127.0.0.1"
    response = MagicMock()

    db = _mock_db(user)
    redis = _mock_redis()

    resp = await password_login(req, request, response, db=db, redis=redis)
    assert resp.access_token is not None


@pytest.mark.asyncio
async def test_password_login_wrong_pwd():
    hashed = bcrypt.hashpw(b"Password123!", bcrypt.gensalt(rounds=4)).decode("utf-8")
    user = MagicMock()
    user.id = 1
    user.username = "admin"
    user.password_hash = hashed
    user.is_active = True

    req = PasswordLoginRequest(username="admin", password="WrongPassword!")
    request = MagicMock(spec=Request)
    request.state.request_id = "test-123"
    request.client.host = "127.0.0.1"
    response = MagicMock()

    db = _mock_db(user)
    redis = _mock_redis()

    with pytest.raises(HTTPException) as exc:
        await password_login(req, request, response, db=db, redis=redis)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_password_login_returns_503_when_schema_missing():
    req = PasswordLoginRequest(username="admin", password="Password123!")
    request = MagicMock(spec=Request)
    request.state.request_id = "test-123"
    request.client.host = "127.0.0.1"
    response = MagicMock()

    db = AsyncMock()
    db.execute.side_effect = ProgrammingError("SELECT 1", {}, Exception('relation "users" does not exist'))
    redis = _mock_redis()

    with pytest.raises(HTTPException) as exc:
        await password_login(req, request, response, db=db, redis=redis)
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_password_login_rejects_disabled_user():
    hashed = bcrypt.hashpw(b"Password123!", bcrypt.gensalt(rounds=4)).decode("utf-8")
    user = MagicMock()
    user.id = 1
    user.username = "admin"
    user.password_hash = hashed
    user.role = "admin"
    user.tenant_id = "default"
    user.ai_route_preference = "auto"
    user.is_active = False

    req = PasswordLoginRequest(username="admin", password="Password123!")
    request = MagicMock(spec=Request)
    request.state.request_id = "test-123"
    request.client.host = "127.0.0.1"
    response = MagicMock()

    db = _mock_db(user)
    redis = _mock_redis()

    with pytest.raises(HTTPException) as exc:
        await password_login(req, request, response, db=db, redis=redis)
    assert exc.value.status_code == 403
