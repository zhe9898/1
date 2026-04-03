from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest


class TestJwtDualTrack:
    @pytest.fixture(autouse=True)
    def _patch_jwt_secrets(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "JWT_SECRET_CURRENT": "test-current-secret-at-least-32-bytes-0001",
                "JWT_SECRET_PREVIOUS": "test-previous-secret-at-least-32-bytes-0001",
                "JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "15",
                "ZEN70_ENV": "",
            },
        ):
            import importlib

            import backend.core.jwt as jwt_mod

            importlib.reload(jwt_mod)
            self.jwt_mod = jwt_mod
            yield

    @pytest.mark.asyncio
    async def test_create_and_decode_current_secret(self) -> None:
        token = self.jwt_mod.create_access_token({"sub": "user1", "username": "alice", "role": "admin"})
        payload, new_token = await self.jwt_mod.decode_token(token)

        assert payload["sub"] == "user1"
        assert payload["username"] == "alice"
        assert payload["role"] == "admin"
        assert new_token is None

    @pytest.mark.asyncio
    async def test_previous_secret_accepted_with_new_token(self) -> None:
        token = self.jwt_mod.create_access_token(
            {"sub": "user2", "username": "bob", "role": "family"},
            use_current_secret=False,
        )
        payload, new_token = await self.jwt_mod.decode_token(token)

        assert payload["sub"] == "user2"
        assert new_token is not None

    @pytest.mark.asyncio
    async def test_new_token_from_previous_is_valid(self) -> None:
        old_token = self.jwt_mod.create_access_token(
            {"sub": "user3", "username": "carol", "role": "admin"},
            use_current_secret=False,
        )
        _, new_token = await self.jwt_mod.decode_token(old_token)
        assert new_token is not None

        payload2, new_token2 = await self.jwt_mod.decode_token(new_token)
        assert payload2["sub"] == "user3"
        assert payload2["username"] == "carol"
        assert new_token2 is None

    @pytest.mark.asyncio
    async def test_invalid_secret_rejected(self) -> None:
        import jwt as pyjwt
        from fastapi import HTTPException

        bad_token = pyjwt.encode(
            {"sub": "hacker", "exp": 9999999999},
            "totally-wrong-secret-at-least-32-bytes",
            algorithm="HS256",
        )

        with pytest.raises(HTTPException) as exc_info:
            await self.jwt_mod.decode_token(bad_token)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_empty_token_rejected(self) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await self.jwt_mod.decode_token("")
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_expired_token_rejected(self) -> None:
        from fastapi import HTTPException

        token = self.jwt_mod.create_access_token(
            {"sub": "user4", "username": "dave", "role": "family"},
            expires_delta=timedelta(seconds=-10),
        )

        with pytest.raises(HTTPException) as exc_info:
            await self.jwt_mod.decode_token(token)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_half_lifespan_auto_renew(self) -> None:
        import jwt as pyjwt

        now = datetime.now(timezone.utc).timestamp()
        payload = {
            "sub": "user5",
            "username": "eve",
            "role": "admin",
            "iat": now - 1200,
            "exp": now + 600,
        }
        token = pyjwt.encode(payload, self.jwt_mod._CURRENT, algorithm="HS256")

        result_payload, new_token = await self.jwt_mod.decode_token(token)
        assert result_payload["sub"] == "user5"
        assert new_token is not None

    @pytest.mark.asyncio
    async def test_runtime_env_secret_change_is_respected_without_reload(self) -> None:
        old_token = self.jwt_mod.create_access_token({"sub": "dynamic-user"})

        with patch.dict(
            "os.environ",
            {
                "JWT_SECRET_CURRENT": "test-current-secret-at-least-32-bytes-0002",
                "JWT_SECRET_PREVIOUS": "test-current-secret-at-least-32-bytes-0001",
            },
        ):
            payload, new_token = await self.jwt_mod.decode_token(old_token)

        assert payload["sub"] == "dynamic-user"
        assert new_token is not None

    def test_production_env_requires_secret(self) -> None:
        import importlib

        with patch.dict(
            "os.environ",
            {
                "ZEN70_ENV": "production",
                "JWT_SECRET_CURRENT": "",
                "JWT_SECRET": "",
                "JWT_SECRET_PREVIOUS": "",
            },
        ):
            with pytest.raises(RuntimeError, match="JWT_SECRET_CURRENT"):
                import backend.core.jwt as jwt_mod

                importlib.reload(jwt_mod)

    def test_expire_seconds_from_env(self) -> None:
        assert self.jwt_mod.get_access_token_expire_seconds() == 15 * 60

    def test_runtime_ready_rejects_insecure_default_secret(self) -> None:
        import importlib

        with patch.dict(
            "os.environ",
            {
                "ZEN70_ENV": "development",
                "JWT_SECRET_CURRENT": "",
                "JWT_SECRET": "",
                "JWT_SECRET_PREVIOUS": "",
            },
        ):
            import backend.core.jwt as jwt_mod

            importlib.reload(jwt_mod)
            with pytest.raises(RuntimeError, match="insecure default secret"):
                jwt_mod.assert_jwt_runtime_ready()
