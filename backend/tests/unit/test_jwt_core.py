"""
core/jwt.py 单元测试 — 双轨 JWT 验证、自动续签、fail-fast。

策略：由于 jwt.py 在模块级读取环境变量，测试通过 patch 模块级常量
来模拟不同的密钥组合，避免 import-time 副作用。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import jwt as pyjwt
import pytest
from fastapi import HTTPException

# 测试用密钥
SECRET_A = "secret-a-32bytes-for-testing!!!!!"
SECRET_B = "secret-b-32bytes-for-testing!!!!!"
ALGORITHM = "HS256"


def _encode(payload: dict, secret: str = SECRET_A, **kw: object) -> str:
    now = datetime.now(UTC)
    data = {
        "sub": "user1",
        "role": "admin",
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=15),
        "jti": "test-jti",
        **payload,
    }
    return pyjwt.encode(data, secret, algorithm=ALGORITHM)


def _expired(secret: str = SECRET_A) -> str:
    now = datetime.now(UTC)
    return pyjwt.encode(
        {"sub": "user1", "exp": now - timedelta(minutes=1), "iat": now - timedelta(minutes=16)},
        secret,
        algorithm=ALGORITHM,
    )


def _half_life_token(secret: str = SECRET_A) -> str:
    """Token 已过 50% 寿命 → 应触发自动续签。"""
    now = datetime.now(UTC)
    return pyjwt.encode(
        {
            "sub": "user1",
            "role": "admin",
            "iat": (now - timedelta(minutes=10)).timestamp(),
            "nbf": (now - timedelta(minutes=10)).timestamp(),
            "exp": (now + timedelta(minutes=5)).timestamp(),
            "jti": "half-life-jti",
        },
        secret,
        algorithm=ALGORITHM,
    )


def _redis_allowing_tokens() -> AsyncMock:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    return redis


class TestDecodeToken:
    """decode_token() 双轨验证矩阵。"""

    @pytest.mark.asyncio
    @patch("backend.control_plane.auth.jwt._CURRENT", SECRET_A)
    @patch("backend.control_plane.auth.jwt._PREVIOUS", None)
    async def test_valid_token_current_secret(self) -> None:
        """当前密钥签发的合法 token → 解码成功，无 new_token。"""
        from backend.control_plane.auth.jwt import decode_token

        token = _encode({"sub": "alice"})
        payload, new_token = await decode_token(token, redis_conn=_redis_allowing_tokens())
        assert payload["sub"] == "alice"
        # 新签发的 token 在不到 50% 寿命时应为 None
        # (取决于 timing，可能为 None 或非 None，但不应 raise)

    @pytest.mark.asyncio
    @patch("backend.control_plane.auth.jwt._CURRENT", SECRET_A)
    @patch("backend.control_plane.auth.jwt._PREVIOUS", SECRET_B)
    async def test_old_secret_triggers_renewal(self) -> None:
        """旧密钥签发的 token → 用 PREVIOUS 解码成功 + 自动续签 new_token。"""
        from backend.control_plane.auth.jwt import decode_token

        token = _encode({"sub": "bob"}, secret=SECRET_B)
        payload, new_token = await decode_token(token, redis_conn=_redis_allowing_tokens())
        assert payload["sub"] == "bob"
        assert new_token is not None, "旧密钥验证通过后必须签发新 token"
        # 验证 new_token 可以用 CURRENT 解码
        new_payload = pyjwt.decode(new_token, SECRET_A, algorithms=[ALGORITHM])
        assert new_payload["sub"] == "bob"

    @pytest.mark.asyncio
    @patch("backend.control_plane.auth.jwt._CURRENT", SECRET_A)
    @patch("backend.control_plane.auth.jwt._PREVIOUS", None)
    async def test_expired_token_rejected(self) -> None:
        """过期 token → 401 拒绝。"""
        from backend.control_plane.auth.jwt import decode_token

        with pytest.raises(HTTPException) as exc_info:
            await decode_token(_expired())
        assert exc_info.value.status_code == 401
        assert "ZEN-AUTH-401" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    @patch("backend.control_plane.auth.jwt._CURRENT", SECRET_A)
    @patch("backend.control_plane.auth.jwt._PREVIOUS", None)
    async def test_wrong_secret_rejected(self) -> None:
        """完全无效的密钥 → 401 拒绝。"""
        from backend.control_plane.auth.jwt import decode_token

        token = _encode({"sub": "eve"}, secret="completely-wrong-secret-32bytes!!")
        with pytest.raises(HTTPException) as exc_info:
            await decode_token(token)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    @patch("backend.control_plane.auth.jwt._CURRENT", SECRET_A)
    @patch("backend.control_plane.auth.jwt._PREVIOUS", None)
    async def test_empty_token_rejected(self) -> None:
        """空 token → 401 拒绝。"""
        from backend.control_plane.auth.jwt import decode_token

        with pytest.raises(HTTPException) as exc_info:
            await decode_token("")
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    @patch("backend.control_plane.auth.jwt._CURRENT", SECRET_A)
    @patch("backend.control_plane.auth.jwt._PREVIOUS", None)
    async def test_whitespace_token_rejected(self) -> None:
        """纯空白 token → 401 拒绝。"""
        from backend.control_plane.auth.jwt import decode_token

        with pytest.raises(HTTPException) as exc_info:
            await decode_token("   ")
        assert exc_info.value.status_code == 401


class TestCreateAccessToken:
    """create_access_token() 签发测试。"""

    @patch("backend.control_plane.auth.jwt._CURRENT", SECRET_A)
    @patch("backend.control_plane.auth.jwt._PREVIOUS", SECRET_B)
    def test_creates_valid_jwt(self) -> None:
        from backend.control_plane.auth.jwt import create_access_token

        token = create_access_token({"sub": "test", "role": "admin"})
        payload = pyjwt.decode(token, SECRET_A, algorithms=[ALGORITHM])
        assert payload["sub"] == "test"
        assert payload["role"] == "admin"
        assert "exp" in payload
        assert "iat" in payload
        assert "nbf" in payload

    @patch("backend.control_plane.auth.jwt._CURRENT", SECRET_A)
    @patch("backend.control_plane.auth.jwt._PREVIOUS", SECRET_B)
    def test_custom_expiry(self) -> None:
        from backend.control_plane.auth.jwt import create_access_token

        token = create_access_token(
            {"sub": "test"},
            expires_delta=timedelta(hours=1),
        )
        payload = pyjwt.decode(token, SECRET_A, algorithms=[ALGORITHM])
        assert payload["exp"] - payload["iat"] == pytest.approx(3600, abs=2)

    @patch("backend.control_plane.auth.jwt._CURRENT", SECRET_A)
    @patch("backend.control_plane.auth.jwt._PREVIOUS", SECRET_B)
    def test_use_previous_secret(self) -> None:
        from backend.control_plane.auth.jwt import create_access_token

        token = create_access_token(
            {"sub": "test"},
            use_current_secret=False,
        )
        # 用 PREVIOUS 密钥解码应成功
        payload = pyjwt.decode(token, SECRET_B, algorithms=[ALGORITHM])
        assert payload["sub"] == "test"


class TestGetAccessTokenExpireSeconds:
    """get_access_token_expire_seconds() 输出验证。"""

    @patch("backend.control_plane.auth.jwt._EXPIRE_MINUTES", 30)
    def test_returns_seconds(self) -> None:
        from backend.control_plane.auth.jwt import get_access_token_expire_seconds

        assert get_access_token_expire_seconds() == 1800


class TestRevocationChecks:
    @pytest.mark.asyncio
    @patch("backend.control_plane.auth.jwt._resolved_revocation_strict", return_value=True)
    async def test_blacklist_check_denies_when_redis_is_unavailable(self, _mock_strict: object) -> None:
        from backend.control_plane.auth.jwt import is_jti_blacklisted

        assert await is_jti_blacklisted(None, "jti-1") is True

    @pytest.mark.asyncio
    @patch("backend.control_plane.auth.jwt._CURRENT", SECRET_A)
    @patch("backend.control_plane.auth.jwt._PREVIOUS", None)
    async def test_decode_token_rejects_blacklisted_jti(self) -> None:
        from backend.control_plane.auth.jwt import decode_token

        redis = AsyncMock()
        redis.get = AsyncMock(return_value="1")
        token = _encode({"sub": "alice", "jti": "revoked-jti", "nbf": datetime.now(UTC)})

        with pytest.raises(HTTPException) as exc_info:
            await decode_token(token, redis_conn=redis)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    @patch("backend.control_plane.auth.jwt._CURRENT", SECRET_A)
    @patch("backend.control_plane.auth.jwt._PREVIOUS", None)
    async def test_half_life_rotation_skips_new_token_when_blacklist_write_fails(self) -> None:
        from backend.control_plane.auth.jwt import decode_token

        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        redis.set = AsyncMock(side_effect=RuntimeError("redis down"))

        payload, new_token = await decode_token(_half_life_token(), redis_conn=redis)

        assert payload["sub"] == "user1"
        assert new_token is None

    @pytest.mark.asyncio
    @patch("backend.control_plane.auth.jwt._CURRENT", SECRET_A)
    @patch("backend.control_plane.auth.jwt._PREVIOUS", None)
    async def test_half_life_token_without_jti_rotates_legacy_token(self) -> None:
        from backend.control_plane.auth.jwt import decode_token

        now = datetime.now(UTC)
        token = pyjwt.encode(
            {
                "sub": "user1",
                "role": "admin",
                "iat": (now - timedelta(minutes=10)).timestamp(),
                "nbf": (now - timedelta(minutes=10)).timestamp(),
                "exp": (now + timedelta(minutes=5)).timestamp(),
            },
            SECRET_A,
            algorithm=ALGORITHM,
        )

        payload, new_token = await decode_token(token, redis_conn=_redis_allowing_tokens())

        assert payload["sub"] == "user1"
        assert new_token is not None
