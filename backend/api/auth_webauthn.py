"""
ZEN70 Auth WebAuthn - WebAuthn 注册与登录
"""
from __future__ import annotations

import json

try:
    from webauthn.helpers import bytes_to_base64url
except ImportError:
    bytes_to_base64url = None  # type: ignore[assignment]

from fastapi import APIRouter, Depends, Request, Response
from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.api.auth_shared import assert_user_active, build_token_response_model, register_login_session, request_tenant_id
from backend.api.deps import get_db, get_redis
from backend.api.models.auth import (
    TokenResponse,
    WebAuthnLoginBeginRequest, WebAuthnLoginBeginResponse,
    WebAuthnLoginCompleteRequest,
    WebAuthnRegisterBeginRequest, WebAuthnRegisterBeginResponse,
    WebAuthnRegisterCompleteRequest,
)
import backend.core.auth_helpers as _auth_helpers
from backend.core.auth_helpers import (
    CHALLENGE_TTL, CODE_BAD_REQUEST, CODE_NOT_FOUND, CODE_SERVER_ERROR,
    client_ip,
    log_auth, request_id, require_db_redis, zen,
)
# Keep direct references for re-export; function bodies use _auth() for patchability
check_webauthn_rate_limit = _auth_helpers.check_webauthn_rate_limit
consume_challenge = _auth_helpers.consume_challenge
credential_id_to_base64url = _auth_helpers.credential_id_to_base64url
expected_challenge_bytes = _auth_helpers.expected_challenge_bytes
origin_from_request = _auth_helpers.origin_from_request
from backend.core.redis_client import RedisClient
from backend.models.user import User, WebAuthnCredential

try:
    from backend.core.webauthn import (
        generate_authentication_challenge, generate_registration_challenge,
        verify_authentication, verify_registration,
    )
except (ImportError, RuntimeError):
    generate_authentication_challenge = None  # type: ignore[assignment]
    generate_registration_challenge = None  # type: ignore[assignment]
    verify_authentication = None  # type: ignore[assignment]
    verify_registration = None  # type: ignore[assignment]

router = APIRouter()


def _auth_mod():  # type: ignore[no-untyped-def]
    """Lazy lookup of backend.api.auth so patches on that module take effect."""
    import sys
    return sys.modules.get("backend.api.auth") or __import__("backend.api.auth", fromlist=["auth"])


@router.post("/webauthn/register/begin", response_model=WebAuthnRegisterBeginResponse)
async def register_begin(
    req: WebAuthnRegisterBeginRequest,
    request: Request,
    db: AsyncSession | None = Depends(get_db),
    redis: RedisClient = Depends(get_redis),
) -> WebAuthnRegisterBeginResponse:
    require_db_redis(db, redis)
    rid, cip = request_id(request), client_ip(request)
    tenant_id = request_tenant_id(req.tenant_id)
    await check_webauthn_rate_limit(redis, cip, rid)

    result = await db.execute(select(User).where(User.tenant_id == tenant_id, User.username == req.username))  # type: ignore[union-attr]
    user = result.scalar_one_or_none()
    if not user:
        user = User(username=req.username, display_name=req.display_name or req.username, tenant_id=tenant_id)
        db.add(user)  # type: ignore[union-attr]
        await db.flush()  # type: ignore[union-attr]
    else:
        assert_user_active(user, flow="webauthn_register_begin", rid=rid, username=req.username, client_ip_str=cip)
    user_id_bytes = str(user.id).encode("utf-8")

    _, challenge_b64, options_json_str = generate_registration_challenge(
        username=req.username, display_name=req.display_name or req.username, user_id=user_id_bytes,
    )
    options_dict = json.loads(options_json_str)
    payload = json.dumps({"user_id": user.id, "username": user.username, "tenant_id": tenant_id, "flow": "register"})
    if not await redis.set_auth_challenge(challenge_b64, payload, ttl=CHALLENGE_TTL):
        raise zen(CODE_SERVER_ERROR, "Failed to store challenge", status.HTTP_500_INTERNAL_SERVER_ERROR)

    log_auth("webauthn_register_begin", True, rid, username=req.username, client_ip_str=cip)
    return WebAuthnRegisterBeginResponse(options=options_dict)


@router.post("/webauthn/register/complete")
async def register_complete(
    req: WebAuthnRegisterCompleteRequest,
    request: Request,
    db: AsyncSession | None = Depends(get_db),
    redis: RedisClient = Depends(get_redis),
) -> dict[str, str]:
    require_db_redis(db, redis)
    rid, cip = request_id(request), client_ip(request)
    await check_webauthn_rate_limit(redis, cip, rid)

    challenge_b64, data = await consume_challenge(redis, req.credential, "register", username=None)
    user_id = data.get("user_id")
    username = data.get("username")
    tenant_id = data.get("tenant_id")
    if user_id is None or not username or not tenant_id:
        raise zen(CODE_BAD_REQUEST, "Invalid challenge data", status.HTTP_400_BAD_REQUEST)

    origin = origin_from_request(request)
    try:
        verification = verify_registration(
            credential=req.credential,
            expected_challenge=expected_challenge_bytes(challenge_b64),
            origin=origin,
        )
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
        log_auth("webauthn_register_complete", False, rid, username=username, detail=str(e))  # type: ignore[arg-type]
        raise zen(CODE_BAD_REQUEST, "Registration verification failed", status.HTTP_400_BAD_REQUEST)

    credential_id_b64 = bytes_to_base64url(verification.credential_id)  # type: ignore[attr-defined]
    raw_name = req.credential.get("deviceName") or (req.credential.get("response") or {}).get("deviceName")  # type: ignore[attr-defined]
    device_name = (raw_name or "unknown")[:128]  # type: ignore[index]
    cred = WebAuthnCredential(
        user_id=int(user_id),  # type: ignore[call-overload]
        credential_id=credential_id_b64,
        public_key=verification.credential_public_key,  # type: ignore[attr-defined]
        sign_count=verification.sign_count,  # type: ignore[attr-defined]
        device_name=device_name,
    )
    db.add(cred)  # type: ignore[union-attr]
    log_auth("webauthn_register_complete", True, rid, username=username, client_ip_str=cip)  # type: ignore[arg-type]
    return {"status": "ok", "message": "Credential registered"}


@router.post("/webauthn/login/begin", response_model=WebAuthnLoginBeginResponse)
async def login_begin(
    req: WebAuthnLoginBeginRequest,
    request: Request,
    db: AsyncSession | None = Depends(get_db),
    redis: RedisClient = Depends(get_redis),
) -> WebAuthnLoginBeginResponse:
    require_db_redis(db, redis)
    assert db is not None  # noqa: S101
    rid, cip = request_id(request), client_ip(request)
    tenant_id = request_tenant_id(req.tenant_id)
    await _auth_mod().check_webauthn_rate_limit(redis, cip, rid)

    result = await db.execute(select(User).where(User.tenant_id == tenant_id, User.username == req.username).options(selectinload(User.credentials)))
    user = result.scalar_one_or_none()
    if not user:
        log_auth("webauthn_login_begin", False, rid, username=req.username, detail="user_not_found")
        raise zen(CODE_NOT_FOUND, "User not found", status.HTTP_404_NOT_FOUND)
    assert_user_active(user, flow="webauthn_login_begin", rid=rid, username=req.username, client_ip_str=cip)
    creds = list(user.credentials)
    if not creds:
        log_auth("webauthn_login_begin", False, rid, username=req.username, detail="no_credentials")
        raise zen(CODE_NOT_FOUND, "No credentials found for user", status.HTTP_404_NOT_FOUND)

    allow_credentials: list[dict[str, object]] = [
        {"id": c.credential_id, "type": "public-key", "transports": ["internal", "usb", "nfc"]}
        for c in creds
    ]
    _, challenge_b64, options_json_str = _auth_mod().generate_authentication_challenge(allow_credentials=allow_credentials)
    options_dict = json.loads(options_json_str)
    payload = json.dumps({"user_id": user.id, "username": user.username, "tenant_id": tenant_id, "flow": "login"})
    if not await redis.set_auth_challenge(challenge_b64, payload, ttl=CHALLENGE_TTL):
        raise zen(CODE_SERVER_ERROR, "Failed to store challenge", status.HTTP_500_INTERNAL_SERVER_ERROR)

    log_auth("webauthn_login_begin", True, rid, username=req.username, client_ip_str=cip)
    return WebAuthnLoginBeginResponse(options=options_dict)


@router.post("/webauthn/login/complete", response_model=TokenResponse)
async def login_complete(
    req: WebAuthnLoginCompleteRequest,
    request: Request,
    response: Response,
    db: AsyncSession | None = Depends(get_db),
    redis: RedisClient = Depends(get_redis),
) -> TokenResponse:
    require_db_redis(db, redis)
    rid, cip = request_id(request), client_ip(request)
    await _auth_mod().check_webauthn_rate_limit(redis, cip, rid)

    challenge_b64, data = await _auth_mod().consume_challenge(redis, req.credential, "login", username=req.username)
    challenge_tenant_id = str(data.get("tenant_id") or "")
    if challenge_tenant_id != request_tenant_id(req.tenant_id):
        raise zen(CODE_BAD_REQUEST, "Challenge tenant mismatch", status.HTTP_400_BAD_REQUEST)
    cred_id_b64 = _auth_mod().credential_id_to_base64url(req.credential)
    if not cred_id_b64:
        raise zen(CODE_BAD_REQUEST, "Invalid credential: missing id", status.HTTP_400_BAD_REQUEST)

    cred_result = await db.execute(  # type: ignore[union-attr]
        select(WebAuthnCredential).where(
            WebAuthnCredential.credential_id == cred_id_b64,
            WebAuthnCredential.user_id == int(data["user_id"]),  # type: ignore[call-overload]
        )
    )
    cred = cred_result.scalar_one_or_none()
    if not cred:
        log_auth("webauthn_login_complete", False, rid, username=req.username, detail="credential_not_found")
        raise zen(CODE_NOT_FOUND, "Credential not found", status.HTTP_404_NOT_FOUND)

    origin = _auth_mod().origin_from_request(request)
    try:
        verification = _auth_mod().verify_authentication(
            credential=req.credential,
            expected_challenge=_auth_mod().expected_challenge_bytes(challenge_b64),
            origin=origin,
            credential_public_key=cred.public_key,
            credential_current_sign_count=cred.sign_count,
        )
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
        log_auth("webauthn_login_complete", False, rid, username=req.username, detail=str(e))
        raise zen(CODE_BAD_REQUEST, "Authentication verification failed", status.HTTP_400_BAD_REQUEST)

    cred.sign_count = verification.new_sign_count  # type: ignore[attr-defined]
    user_result = await db.execute(select(User).where(User.id == cred.user_id, User.tenant_id == challenge_tenant_id))  # type: ignore[union-attr]
    login_user = user_result.scalar_one_or_none()
    if login_user is None:
        log_auth("webauthn_login_complete", False, rid, username=req.username, detail="user_not_found")
        raise zen(CODE_NOT_FOUND, "User not found", status.HTTP_404_NOT_FOUND)
    assert_user_active(login_user, flow="webauthn_login_complete", rid=rid, username=req.username, client_ip_str=cip)

    log_auth("webauthn_login_complete", True, rid, username=req.username, client_ip_str=cip)
    resp = build_token_response_model(
        sub=str(cred.user_id),
        username=req.username,
        role=login_user.role,
        tenant_id=login_user.tenant_id,
        ai_route_preference=login_user.ai_route_preference,
    )
    await register_login_session(
        db,  # type: ignore[arg-type]
        tenant_id=login_user.tenant_id,
        user_id=str(cred.user_id),
        username=req.username,
        access_token=resp.access_token,
        ip_address=cip,
        user_agent=request.headers.get("user-agent"),
        auth_method="webauthn",
    )
    return resp
