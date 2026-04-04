"""ZEN70 invite-based auth flows."""

from __future__ import annotations

import json
import secrets
import time

try:
    from webauthn.helpers import bytes_to_base64url
except ImportError:

    def bytes_to_base64url(val: bytes) -> str:
        raise RuntimeError("webauthn helpers are unavailable")


from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth_shared import assert_user_active
from backend.api.deps import get_current_admin, get_db, get_redis
from backend.api.models.auth import InviteCreateRequest, InviteResponse, WebAuthnRegisterBeginResponse, WebAuthnRegisterCompleteRequest
from backend.core.auth_helpers import (
    CHALLENGE_TTL,
    CODE_BAD_REQUEST,
    CODE_NOT_FOUND,
    CODE_SERVER_ERROR,
    client_ip,
    consume_challenge,
    expected_challenge_bytes,
    log_auth,
    origin_from_request,
    request_id,
    require_db_redis,
    token_response,
    zen,
)
from backend.core.redis_client import RedisClient
from backend.models.user import User, WebAuthnCredential

try:
    from backend.core.webauthn import generate_registration_challenge, verify_registration
except (ImportError, RuntimeError):
    generate_registration_challenge = None  # type: ignore[assignment]
    verify_registration = None  # type: ignore[assignment]

router = APIRouter()

INVITE_TOKEN_PREFIX = "zen70:invite:"
INVITE_TOKEN_LOCK_PREFIX = "zen70:invite-lock:"
INVITE_FALLBACK_CONFIRM_VALUE = "degrade-login"


def _assert_invite_fallback_confirmation(confirm: str | None) -> None:
    if (confirm or "").strip().lower() == INVITE_FALLBACK_CONFIRM_VALUE:
        return
    raise zen(
        CODE_BAD_REQUEST,
        "Invite fallback login requires explicit confirmation",
        status.HTTP_400_BAD_REQUEST,
        recovery_hint="Resend the request with X-Invite-Fallback-Confirm: degrade-login after operator confirmation",
    )


async def _consume_invite_token(redis: RedisClient, token: str) -> dict[str, object]:
    token_key = f"{INVITE_TOKEN_PREFIX}{token}"
    lock_key = f"{INVITE_TOKEN_LOCK_PREFIX}{token}"
    lock_acquired = await redis.acquire_lock(lock_key, ttl=10)
    if not lock_acquired:
        raise zen("ZEN-AUTH-4092", "Invite token is being consumed", status_code=409, recovery_hint="Retry after a moment")
    try:
        token_data_str = await redis.get(token_key)
        if not token_data_str:
            raise zen(
                "ZEN-AUTH-4031",
                "Invite token expired or not found",
                status_code=status.HTTP_403_FORBIDDEN,
                recovery_hint="Generate a new invite and retry",
            )
        try:
            payload = json.loads(token_data_str)
        except json.JSONDecodeError as exc:
            raise zen(
                CODE_SERVER_ERROR,
                "Invite token payload is invalid",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            ) from exc
        if not isinstance(payload, dict):
            raise zen(
                CODE_SERVER_ERROR,
                "Invite token payload must be an object",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        await redis.delete(token_key)
        return dict(payload)
    finally:
        await redis.release_lock(lock_key)


async def _validate_invite_token(redis: RedisClient, token: str) -> dict[str, object]:
    """Validate invite token without consuming it. Returns payload if valid."""
    token_key = f"{INVITE_TOKEN_PREFIX}{token}"
    token_data_str = await redis.get(token_key)
    if not token_data_str:
        raise zen(
            "ZEN-AUTH-4031",
            "Invite token expired or not found",
            status_code=status.HTTP_403_FORBIDDEN,
            recovery_hint="Generate a new invite and retry",
        )
    try:
        payload = json.loads(token_data_str)
    except json.JSONDecodeError as exc:
        raise zen(
            CODE_SERVER_ERROR,
            "Invite token payload is invalid",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from exc
    if not isinstance(payload, dict):
        raise zen(
            CODE_SERVER_ERROR,
            "Invite token payload must be an object",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return dict(payload)


@router.post("/invites", response_model=InviteResponse)
async def create_invite(
    req: InviteCreateRequest,
    db: AsyncSession = Depends(get_db),
    redis: RedisClient = Depends(get_redis),
    current_admin: dict[str, str] = Depends(get_current_admin),
) -> InviteResponse:
    """Create a one-time invite token (admin only)."""
    require_db_redis(db, redis)
    from backend.api.auth_shared import bind_admin_scope

    scope_tenant_id = await bind_admin_scope(db, current_admin)

    stmt = select(User).where(User.id == req.user_id)
    if scope_tenant_id is not None:
        stmt = stmt.where(User.tenant_id == scope_tenant_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if not user:
        raise zen(CODE_NOT_FOUND, "User not found", status.HTTP_404_NOT_FOUND)

    token = secrets.token_hex(32)
    expires_in = req.expires_in_minutes * 60
    token_key = f"{INVITE_TOKEN_PREFIX}{token}"
    await redis.setex(token_key, expires_in, json.dumps({"user_id": user.id}))

    return InviteResponse(token=token, expires_at=int(time.time()) + expires_in)


@router.post("/invites/{token}/webauthn/register/begin", response_model=WebAuthnRegisterBeginResponse)
async def invite_webauthn_register_begin(
    token: str,
    db: AsyncSession = Depends(get_db),
    redis: RedisClient = Depends(get_redis),
) -> WebAuthnRegisterBeginResponse:
    """Invite link: start WebAuthn registration."""
    require_db_redis(db, redis)
    token_key = f"{INVITE_TOKEN_PREFIX}{token}"
    token_data_str = await redis.get(token_key)
    if not token_data_str:
        raise zen(
            "ZEN-AUTH-4031",
            "Invite token expired or not found",
            status_code=status.HTTP_403_FORBIDDEN,
            recovery_hint="Generate a new invite and retry",
        )

    user_id = json.loads(token_data_str)["user_id"]
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise zen("ZEN-AUTH-4041", "Invite target user not found", status_code=404, recovery_hint="Validate invite target and retry")
    assert_user_active(user, flow="invite_webauthn_register_begin", rid="invite-register", username=user.username)

    user_id_bytes = str(user.id).encode("utf-8")
    _, challenge_b64, options_json_str = generate_registration_challenge(
        username=user.username,
        display_name=user.display_name or user.username,
        user_id=user_id_bytes,
    )
    challenge_payload = json.dumps({"user_id": user.id, "username": user.username, "flow": "register"})
    if not await redis.set_auth_challenge(challenge_b64, challenge_payload, ttl=CHALLENGE_TTL):
        raise zen(CODE_SERVER_ERROR, "Failed to store challenge", status.HTTP_500_INTERNAL_SERVER_ERROR)

    return WebAuthnRegisterBeginResponse(options=json.loads(options_json_str))


@router.post("/invites/{token}/webauthn/register/complete")
async def invite_webauthn_register_complete(
    token: str,
    req: WebAuthnRegisterCompleteRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: RedisClient = Depends(get_redis),
) -> dict[str, object]:
    """Invite link: complete WebAuthn registration and consume token."""
    require_db_redis(db, redis)

    # Step 1: Validate token without consuming — prevents DoS via invalid requests burning invites
    token_payload = await _validate_invite_token(redis, token)
    user_id = token_payload.get("user_id")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise zen("ZEN-AUTH-4041", "Invite target user not found", status_code=404, recovery_hint="Validate invite target and retry")

    assert_user_active(
        user,
        flow="invite_webauthn_register_complete",
        rid=request_id(request),
        username=user.username,
        client_ip_str=client_ip(request),
    )

    challenge_b64, _data = await consume_challenge(redis, req.credential, "register", username=user.username)
    origin = origin_from_request(request)
    try:
        verification = verify_registration(
            credential=req.credential,
            expected_challenge=expected_challenge_bytes(challenge_b64),
            origin=origin,
        )
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
        raise zen(
            "ZEN-AUTH-4002",
            f"WebAuthn verification failed: {exc}",
            status_code=status.HTTP_400_BAD_REQUEST,
            recovery_hint="Restart registration and retry",
        ) from exc

    # Step 2: Consume token only after successful WebAuthn verification
    await _consume_invite_token(redis, token)

    cred_id_b64 = bytes_to_base64url(verification.credential_id)  # type: ignore[attr-defined]
    raw_dev = req.credential.get("deviceName") or (req.credential.get("response") or {}).get("deviceName")  # type: ignore[attr-defined]
    new_cred = WebAuthnCredential(
        user_id=user.id,
        credential_id=cred_id_b64,
        public_key=verification.credential_public_key,  # type: ignore[attr-defined]
        sign_count=verification.sign_count,  # type: ignore[attr-defined]
        device_name=(raw_dev or "zen70-bound-device")[:128],  # type: ignore[index]
    )
    db.add(new_cred)
    await db.flush()
    await db.commit()

    from backend.core.permissions import get_user_scopes

    user_scopes = await get_user_scopes(db, tenant_id=user.tenant_id, user_id=str(user.id))

    body = token_response(sub=str(user.id), username=user.username, role=user.role, tenant_id=user.tenant_id, scopes=user_scopes)
    return {
        "status": "ok",
        "message": "WebAuthn credential registered and invite consumed",
        "access_token": body["access_token"],
        "token_type": body["token_type"],
    }


@router.post("/invites/{token}/fallback/login")
async def invite_fallback_login(
    token: str,
    request: Request,
    confirm: str | None = Header(default=None, alias="X-Invite-Fallback-Confirm"),
    db: AsyncSession = Depends(get_db),
    redis: RedisClient = Depends(get_redis),
    current_admin: dict[str, str] = Depends(get_current_admin),
) -> dict[str, object]:
    """Invite link: fallback login with admin confirmation required.

    Security: requires admin authentication to prevent invite link leak → account takeover.
    """
    require_db_redis(db, redis)
    rid, cip = request_id(request), client_ip(request)
    try:
        _assert_invite_fallback_confirmation(confirm)
    except HTTPException:
        log_auth("invite_fallback_login", False, rid, client_ip_str=cip, detail="missing_explicit_confirmation")
        raise

    token_payload = await _consume_invite_token(redis, token)
    user_id = token_payload.get("user_id")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise zen("ZEN-AUTH-4041", "Invite target user not found", status_code=404, recovery_hint="Validate invite target and retry")
    assert_user_active(user, flow="invite_fallback_login", rid=rid, username=user.username, client_ip_str=cip)

    from backend.core.permissions import get_user_scopes

    user_scopes = await get_user_scopes(db, tenant_id=user.tenant_id, user_id=str(user.id))

    body = token_response(sub=str(user.id), username=user.username, role=user.role, tenant_id=user.tenant_id, scopes=user_scopes)
    log_auth("invite_fallback_login", True, rid, username=user.username, client_ip_str=cip, detail="degraded_access_confirmed")
    return {
        "status": "ok",
        "message": "Fallback login succeeded and invite consumed",
        "access_token": body["access_token"],
        "token_type": body["token_type"],
    }
