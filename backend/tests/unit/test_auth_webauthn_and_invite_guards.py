from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from backend.api.auth import invite_fallback_login, login_complete
from backend.api.models.auth import WebAuthnLoginCompleteRequest


def _mock_request(client_ip: str = "127.0.0.1") -> MagicMock:
    request = MagicMock()
    request.state.request_id = "rid-auth-guards"
    request.client.host = client_ip
    return request


def _scalar_result(value: object | None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


@pytest.mark.asyncio
async def test_webauthn_login_complete_rejects_disabled_user() -> None:
    request = _mock_request()
    response = MagicMock()

    credential = MagicMock()
    credential.user_id = 7
    credential.public_key = b"public-key"
    credential.sign_count = 1

    user = MagicMock()
    user.id = 7
    user.username = "shared-user"
    user.tenant_id = "tenant-a"
    user.role = "family"
    user.ai_route_preference = "auto"
    user.is_active = False

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_scalar_result(credential), _scalar_result(user)])
    redis = AsyncMock()

    req = WebAuthnLoginCompleteRequest(
        tenant_id="tenant-a",
        username="shared-user",
        credential={"id": "cred-1"},
    )

    with (
        patch("backend.api.auth.check_webauthn_rate_limit", new=AsyncMock()),
        patch(
            "backend.api.auth.consume_challenge",
            new=AsyncMock(return_value=("challenge-b64", {"tenant_id": "tenant-a", "user_id": "7"})),
        ),
        patch("backend.api.auth.credential_id_to_base64url", return_value="cred-1"),
        patch(
            "backend.api.auth.expected_challenge_bytes",
            return_value=b"challenge",
        ),
        patch(
            "backend.api.auth.verify_authentication",
            return_value=SimpleNamespace(new_sign_count=2),
        ),
        patch("backend.api.auth.origin_from_request", return_value="https://example.com"),
    ):
        with pytest.raises(HTTPException) as exc:
            await login_complete(req, request, response, db=db, redis=redis)

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_invite_fallback_login_requires_explicit_confirmation() -> None:
    request = _mock_request()
    db = AsyncMock()
    redis = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await invite_fallback_login("invite-token", request, confirm=None, db=db, redis=redis)

    assert exc.value.status_code == 400
    redis.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_invite_fallback_login_rejects_disabled_user() -> None:
    request = _mock_request("192.168.1.50")
    user = MagicMock()
    user.id = 9
    user.username = "family"
    user.tenant_id = "tenant-a"
    user.role = "family"
    user.is_active = False

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result(user))
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=json.dumps({"user_id": 9}))
    redis.delete = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await invite_fallback_login(
            "invite-token",
            request,
            confirm="degrade-login",
            db=db,
            redis=redis,
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_webauthn_login_complete_rejects_sign_count_regression() -> None:
    request = _mock_request()
    response = MagicMock()

    credential = MagicMock()
    credential.user_id = 7
    credential.public_key = b"public-key"
    credential.sign_count = 10

    user = MagicMock()
    user.id = 7
    user.username = "shared-user"
    user.tenant_id = "tenant-a"
    user.role = "family"
    user.ai_route_preference = "auto"
    user.is_active = True

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_scalar_result(credential), _scalar_result(user)])
    redis = AsyncMock()

    req = WebAuthnLoginCompleteRequest(
        tenant_id="tenant-a",
        username="shared-user",
        credential={"id": "cred-1"},
    )

    with (
        patch("backend.api.auth.check_webauthn_rate_limit", new=AsyncMock()),
        patch(
            "backend.api.auth.consume_challenge",
            new=AsyncMock(return_value=("challenge-b64", {"tenant_id": "tenant-a", "user_id": "7"})),
        ),
        patch("backend.api.auth.credential_id_to_base64url", return_value="cred-1"),
        patch(
            "backend.api.auth.expected_challenge_bytes",
            return_value=b"challenge",
        ),
        patch(
            "backend.api.auth.verify_authentication",
            return_value=SimpleNamespace(new_sign_count=9),
        ),
        patch("backend.api.auth.origin_from_request", return_value="https://example.com"),
    ):
        with pytest.raises(HTTPException) as exc:
            await login_complete(req, request, response, db=db, redis=redis)

    assert exc.value.status_code == 401
