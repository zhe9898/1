from __future__ import annotations

import json
from inspect import signature
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from backend.api.auth import invite_fallback_login, login_complete
from backend.api.auth_invite import invite_webauthn_register_begin
from backend.api.auth_user import update_ai_preference
from backend.api.auth_webauthn import register_begin, register_complete
from backend.api.deps import get_tenant_db
from backend.api.models.auth import AiRoutePreferenceRequest, WebAuthnLoginCompleteRequest, WebAuthnRegisterBeginRequest, WebAuthnRegisterCompleteRequest


def _mock_request(client_ip: str = "127.0.0.1", *, flow_session_id: str = "flow-session-1") -> MagicMock:
    request = MagicMock()
    request.state.request_id = "rid-auth-guards"
    request.state.webauthn_flow_session_id = flow_session_id
    request.client.host = client_ip
    request.cookies = {}
    return request


def _scalar_result(value: object | None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _stored_challenge(
    *,
    challenge_id: str = "challenge-b64",
    session_id: str = "flow-session-1",
    user_id: str = "7",
    tenant_id: str = "tenant-a",
    flow: str = "register",
) -> SimpleNamespace:
    return SimpleNamespace(
        challenge_id=challenge_id,
        session_id=session_id,
        user_id=user_id,
        tenant_id=tenant_id,
        flow=flow,
    )


@pytest.mark.asyncio
async def test_webauthn_register_begin_requires_self_registration() -> None:
    request = _mock_request()
    response = MagicMock()
    db = AsyncMock()
    redis = AsyncMock()

    with patch("backend.api.auth_webauthn.check_webauthn_rate_limit", new=AsyncMock()):
        with pytest.raises(HTTPException) as exc:
            await register_begin(
                WebAuthnRegisterBeginRequest(username="bob", display_name="Bob", tenant_id="tenant-a"),
                request,
                response,
                db=db,
                redis=redis,
                current_user={"sub": "7", "username": "alice", "tenant_id": "tenant-a"},
            )

    assert exc.value.status_code == 403
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_webauthn_register_complete_rejects_challenge_for_other_user() -> None:
    request = _mock_request()
    response = MagicMock()
    user = MagicMock()
    user.id = 7
    user.username = "alice"
    user.tenant_id = "tenant-a"
    user.is_active = True
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result(user))
    db.add = MagicMock()
    redis = AsyncMock()

    with (
        patch("backend.api.auth_webauthn.check_webauthn_rate_limit", new=AsyncMock()),
        patch(
            "backend.api.auth_webauthn.WebAuthnChallengeStore.consume",
            new=AsyncMock(side_effect=HTTPException(status_code=403, detail={"code": "ZEN-AUTH-4032"})),
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await register_complete(
                WebAuthnRegisterCompleteRequest(credential={"id": "cred-1"}),
                request,
                response,
                db=db,
                redis=redis,
                current_user={"sub": "7", "username": "alice", "tenant_id": "tenant-a"},
            )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_webauthn_register_complete_persists_transports_without_explicit_commit() -> None:
    request = _mock_request()
    response = MagicMock()
    user = MagicMock()
    user.id = 7
    user.username = "alice"
    user.tenant_id = "tenant-a"
    user.is_active = True

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result(user))
    db.add = MagicMock()
    redis = AsyncMock()

    verification = SimpleNamespace(credential_id=b"cred-1", credential_public_key=b"pk", sign_count=1)
    with (
        patch("backend.api.auth_webauthn.check_webauthn_rate_limit", new=AsyncMock()),
        patch(
            "backend.api.auth_webauthn.WebAuthnChallengeStore.consume",
            new=AsyncMock(return_value=_stored_challenge(user_id="7")),
        ),
        patch("backend.api.auth_webauthn.verify_registration", return_value=verification),
        patch("backend.api.auth_webauthn.expected_challenge_bytes", return_value=b"challenge"),
        patch("backend.api.auth_webauthn.origin_from_request", return_value="https://example.com"),
    ):
        result = await register_complete(
            WebAuthnRegisterCompleteRequest(
                credential={
                    "id": "cred-1",
                    "response": {"transports": ["usb", "ble", "invalid"]},
                }
            ),
            request,
            response,
            db=db,
            redis=redis,
            current_user={"sub": "7", "username": "alice", "tenant_id": "tenant-a"},
        )

    saved_credential = db.add.call_args.args[0]
    assert saved_credential.transports == ["usb", "ble"]
    db.commit.assert_not_awaited()
    assert result["status"] == "ok"
    response.delete_cookie.assert_called_once()


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
    db.execute = AsyncMock(return_value=_scalar_result(user))
    redis = AsyncMock()

    req = WebAuthnLoginCompleteRequest(
        tenant_id="tenant-a",
        username="shared-user",
        credential={"id": "cred-1"},
    )

    with patch("backend.api.auth.check_webauthn_rate_limit", new=AsyncMock()):
        with pytest.raises(HTTPException) as exc:
            await login_complete(req, request, response, db=db, redis=redis)

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_invite_fallback_login_requires_explicit_confirmation() -> None:
    request = _mock_request()
    response = MagicMock()
    db = AsyncMock()
    redis = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await invite_fallback_login("invite-token", request, response, confirm=None, db=db, redis=redis)

    assert exc.value.status_code == 400
    redis.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_invite_fallback_login_rejects_disabled_user() -> None:
    request = _mock_request("192.168.1.50")
    response = MagicMock()
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
            response,
            confirm="degrade-login",
            db=db,
            redis=redis,
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_invite_webauthn_begin_reuses_cached_challenge_for_same_flow_session() -> None:
    request = _mock_request()
    response = MagicMock()
    user = MagicMock()
    user.id = 9
    user.username = "family"
    user.display_name = "Family"
    user.tenant_id = "tenant-a"
    user.is_active = True

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result(user))
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=json.dumps({"user_id": 9}))
    redis.set_auth_challenge = AsyncMock(return_value=True)
    redis.delete = AsyncMock(return_value=1)

    with (
        patch("backend.api.auth_invite.check_webauthn_rate_limit", new=AsyncMock()),
        patch(
            "backend.api.auth_invite.generate_registration_challenge",
            return_value=(b"challenge", "challenge-b64", '{"challenge":"challenge-b64"}'),
        ),
    ):
        first = await invite_webauthn_register_begin(
            "invite-token",
            request,
            response,
            db=db,
            redis=redis,
        )
        second = await invite_webauthn_register_begin(
            "invite-token",
            request,
            response,
            db=db,
            redis=redis,
        )

    assert first.options == second.options
    redis.set_auth_challenge.assert_awaited()


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
    db.execute = AsyncMock(side_effect=[_scalar_result(user), _scalar_result(credential)])
    redis = AsyncMock()

    req = WebAuthnLoginCompleteRequest(
        tenant_id="tenant-a",
        username="shared-user",
        credential={"id": "cred-1"},
    )

    with (
        patch("backend.api.auth.check_webauthn_rate_limit", new=AsyncMock()),
        patch(
            "backend.api.auth_webauthn.WebAuthnChallengeStore.consume",
            new=AsyncMock(return_value=_stored_challenge(flow="login")),
        ),
        patch("backend.api.auth.credential_id_to_base64url", return_value="cred-1"),
        patch("backend.api.auth.expected_challenge_bytes", return_value=b"challenge"),
        patch("backend.api.auth.verify_authentication", return_value=SimpleNamespace(new_sign_count=9)),
        patch("backend.api.auth.origin_from_request", return_value="https://example.com"),
    ):
        with pytest.raises(HTTPException) as exc:
            await login_complete(req, request, response, db=db, redis=redis)

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_webauthn_login_complete_warns_when_authenticator_has_no_counter() -> None:
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
    db.execute = AsyncMock(side_effect=[_scalar_result(user), _scalar_result(credential)])
    redis = AsyncMock()

    req = WebAuthnLoginCompleteRequest(
        tenant_id="tenant-a",
        username="shared-user",
        credential={"id": "cred-1"},
    )
    token_response = SimpleNamespace(access_token="tok", token_type="bearer", expires_in=900)

    with (
        patch("backend.api.auth.check_webauthn_rate_limit", new=AsyncMock()),
        patch(
            "backend.api.auth_webauthn.WebAuthnChallengeStore.consume",
            new=AsyncMock(return_value=_stored_challenge(flow="login")),
        ),
        patch("backend.api.auth.credential_id_to_base64url", return_value="cred-1"),
        patch("backend.api.auth.expected_challenge_bytes", return_value=b"challenge"),
        patch("backend.api.auth.verify_authentication", return_value=SimpleNamespace(new_sign_count=0)),
        patch("backend.api.auth.origin_from_request", return_value="https://example.com"),
        patch("backend.api.auth_webauthn.issue_auth_token", return_value=token_response),
        patch("backend.api.auth_webauthn.register_login_session", new=AsyncMock()),
        patch("backend.core.permissions.get_user_scopes", new=AsyncMock(return_value=[])),
        patch("backend.core.permissions.hydrate_scopes_for_role", return_value=[]),
        patch("backend.api.auth_webauthn.logger.warning") as warning_mock,
    ):
        result = await login_complete(req, request, response, db=db, redis=redis)

    assert result.authenticated is True
    assert result.sub == "7"
    assert result.role == "family"
    warning_mock.assert_called_once()
    response.delete_cookie.assert_called_once()


@pytest.mark.asyncio
async def test_invite_register_complete_rejects_cross_session_replay() -> None:
    request = _mock_request(flow_session_id="flow-session-b")
    response = MagicMock()
    user = MagicMock()
    user.id = 9
    user.username = "family"
    user.tenant_id = "tenant-a"
    user.role = "family"
    user.is_active = True

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result(user))
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=json.dumps({"user_id": 9}))

    with (
        patch(
            "backend.api.auth_invite.WebAuthnChallengeStore.consume",
            new=AsyncMock(side_effect=HTTPException(status_code=403, detail={"code": "ZEN-AUTH-4032"})),
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            from backend.api.auth_invite import invite_webauthn_register_complete

            await invite_webauthn_register_complete(
                "invite-token",
                WebAuthnRegisterCompleteRequest(credential={"id": "cred-1"}),
                request,
                response,
                db=db,
                redis=redis,
            )

    assert exc.value.status_code == 403


def test_update_ai_preference_uses_tenant_bound_db_dependency() -> None:
    dependency = signature(update_ai_preference).parameters["db"].default
    assert dependency.dependency is get_tenant_db


@pytest.mark.asyncio
async def test_update_ai_preference_commits_before_issuing_new_token() -> None:
    request = _mock_request()
    user = MagicMock()
    user.id = 7
    user.username = "alice"
    user.tenant_id = "tenant-a"
    user.role = "admin"
    user.ai_route_preference = "auto"

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result(user))

    with (
        patch("backend.core.permissions.get_user_scopes", new=AsyncMock(return_value=[])),
        patch("backend.core.permissions.hydrate_scopes_for_role", return_value=[]),
    ):
        result = await update_ai_preference(
            AiRoutePreferenceRequest(preference="cloud"),
            request,
            MagicMock(),
            db=db,
            current_user={"username": "alice", "tenant_id": "tenant-a"},
        )

    assert result.authenticated is True
    assert result.ai_route_preference == "cloud"
    db.commit.assert_awaited_once()
