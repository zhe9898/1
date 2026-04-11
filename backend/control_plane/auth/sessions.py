"""Session management core logic with Postgres-backed authority."""

from __future__ import annotations

import datetime
import logging
import uuid
from collections.abc import Mapping

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.kernel.contracts.errors import zen
from backend.kernel.contracts.tenant_claims import current_user_tenant_id
from backend.models.session import Session

_logger = logging.getLogger(__name__)


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


async def _best_effort_blacklist_session_jti(
    redis: object | None,
    jti: str,
    expires_at: datetime.datetime,
) -> None:
    """Best-effort Redis acceleration for revoked or rotated JWT ids."""
    if not jti:
        return
    if redis is None:
        return
    try:
        now = _utcnow()
        remaining_seconds = max(int((expires_at - now).total_seconds()), 1)
        redis_kv = getattr(redis, "kv", None)
        if redis_kv is None:
            return
        await redis_kv.set(f"jwt:blacklist:{jti}", "1", ex=remaining_seconds)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        _logger.warning("session jti blacklist acceleration failed for %s: %s", jti, exc)


def _derive_device_name(user_agent: str | None) -> str | None:
    """Derive a human-readable device name from user agent."""
    if not user_agent:
        return None
    ua = user_agent.lower()

    # Browser detection
    if "edg/" in ua:
        browser = "Edge"
    elif "chrome/" in ua and "chromium" not in ua:
        browser = "Chrome"
    elif "firefox/" in ua:
        browser = "Firefox"
    elif "safari/" in ua and "chrome" not in ua:
        browser = "Safari"
    else:
        browser = "Browser"

    # OS detection
    if "iphone" in ua:
        os_name = "iPhone"
    elif "ipad" in ua:
        os_name = "iPad"
    elif "android" in ua:
        os_name = "Android"
    elif "mac os" in ua or "macintosh" in ua:
        os_name = "macOS"
    elif "windows" in ua:
        os_name = "Windows"
    elif "linux" in ua:
        os_name = "Linux"
    else:
        os_name = "Unknown"

    return f"{browser} on {os_name}"


async def create_session(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    username: str,
    session_id: str | None = None,
    jti: str,
    ip_address: str | None,
    user_agent: str | None,
    auth_method: str,
    expires_in_seconds: int,
    max_concurrent: int = 10,
    redis: object | None = None,
) -> Session:
    """Create a new session after successful login.

    Args:
        db: Database session
        tenant_id: Tenant ID
        user_id: User ID (string form of int PK)
        username: Username
        jti: JWT token ID (for revocation)
        ip_address: Client IP
        user_agent: Client user agent
        auth_method: How user authenticated (password/pin/webauthn/invite)
        expires_in_seconds: Session TTL in seconds
        max_concurrent: Maximum concurrent sessions (oldest evicted)

    Returns:
        Created Session object
    """
    now = _utcnow()
    expires_at = now + datetime.timedelta(seconds=expires_in_seconds)

    # Evict oldest sessions if over limit
    active_sessions = await db.execute(
        select(Session)
        .where(
            and_(
                Session.tenant_id == tenant_id,
                Session.user_id == user_id,
                Session.is_active.is_(True),
                Session.expires_at > now,
            )
        )
        .order_by(Session.created_at.asc())
    )
    sessions = list(active_sessions.scalars().all())
    if len(sessions) >= max_concurrent:
        # Revoke oldest sessions to stay within limit
        for old_session in sessions[: len(sessions) - max_concurrent + 1]:
            old_session.is_active = False
            old_session.revoked_at = now
            old_session.revoked_by = "system:concurrent_limit"
            await _best_effort_blacklist_session_jti(redis, old_session.jti, old_session.expires_at)

    session = Session(
        session_id=session_id or uuid.uuid4().hex,
        tenant_id=tenant_id,
        user_id=user_id,
        username=username,
        jti=jti,
        ip_address=ip_address,
        user_agent=user_agent[:512] if user_agent else None,
        device_name=_derive_device_name(user_agent),
        auth_method=auth_method,
        is_active=True,
        created_at=now,
        last_seen_at=now,
        expires_at=expires_at,
    )
    db.add(session)
    await db.flush()
    return session


async def validate_session_claims(
    db: AsyncSession,
    payload: Mapping[str, object],
) -> Session | None:
    """Validate sid/jti claims against the authoritative sessions table."""
    session_id = str(payload.get("sid") or "").strip()
    jti = str(payload.get("jti") or "").strip()
    tenant_id = current_user_tenant_id(payload)
    user_id = str(payload.get("sub") or "").strip()
    if not session_id:
        raise zen("ZEN-AUTH-401", "Session token is missing sid", status_code=401)
    if not jti or not user_id:
        raise zen("ZEN-AUTH-401", "Invalid session token", status_code=401)
    if tenant_id is None:
        raise zen("ZEN-AUTH-401", "Invalid session token", status_code=401)

    result = await db.execute(
        select(Session).where(
            Session.session_id == session_id,
            Session.tenant_id == tenant_id,
        )
    )
    session = result.scalars().first()
    if session is None or session.user_id != user_id:
        raise zen("ZEN-AUTH-401", "Session not found", status_code=401)

    now = _utcnow()
    if not session.is_active or session.expires_at <= now:
        raise zen("ZEN-AUTH-401", "Session has expired or been revoked", status_code=401)
    if session.jti != jti:
        raise zen("ZEN-AUTH-401", "Session token has been rotated or revoked", status_code=401)

    session.last_seen_at = now
    await db.flush()
    return session


async def rotate_session_credentials(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
    new_jti: str,
    expires_in_seconds: int,
) -> Session:
    """Persist the newest token identity for an existing active session."""
    result = await db.execute(
        select(Session).where(
            Session.session_id == session_id,
            Session.tenant_id == tenant_id,
        )
    )
    session = result.scalars().first()
    if session is None or session.user_id != user_id:
        raise zen("ZEN-AUTH-401", "Session not found", status_code=401)
    if not session.is_active:
        raise zen("ZEN-AUTH-401", "Session has been revoked", status_code=401)

    now = _utcnow()
    session.jti = new_jti
    session.last_seen_at = now
    session.expires_at = now + datetime.timedelta(seconds=expires_in_seconds)
    await db.flush()
    return session


async def revoke_owned_session(
    db: AsyncSession,
    session_id: str,
    *,
    tenant_id: str,
    user_id: str,
    revoked_by: str,
    redis: object | None = None,
) -> Session:
    """Revoke a session only when it belongs to the authenticated user."""
    result = await db.execute(
        select(Session).where(
            Session.session_id == session_id,
            Session.tenant_id == tenant_id,
            Session.user_id == user_id,
        )
    )
    session = result.scalars().first()

    if session is None:
        raise zen("ZEN-SESSION-4040", "Session not found", status_code=404)

    if not session.is_active:
        raise zen("ZEN-SESSION-4090", "Session already revoked", status_code=409)

    now = _utcnow()
    session.is_active = False
    session.revoked_at = now
    session.revoked_by = revoked_by
    await db.flush()

    await _best_effort_blacklist_session_jti(redis, session.jti, session.expires_at)

    return session


async def revoke_session(
    db: AsyncSession,
    session_id: str,
    *,
    tenant_id: str,
    revoked_by: str,
    redis: object | None = None,
) -> Session:
    """Revoke a specific session.

    Args:
        db: Database session
        session_id: Session ID to revoke
        tenant_id: Tenant ID (for scoping)
        revoked_by: Username of who revoked this session
        redis: Optional Redis client for JWT blacklisting

    Returns:
        Revoked Session

    Raises:
        HTTPException: If session not found or already revoked
    """
    result = await db.execute(
        select(Session).where(
            Session.session_id == session_id,
            Session.tenant_id == tenant_id,
        )
    )
    session = result.scalars().first()

    if session is None:
        raise zen("ZEN-SESSION-4040", "Session not found", status_code=404)

    if not session.is_active:
        raise zen("ZEN-SESSION-4090", "Session already revoked", status_code=409)

    now = _utcnow()
    session.is_active = False
    session.revoked_at = now
    session.revoked_by = revoked_by
    await db.flush()

    await _best_effort_blacklist_session_jti(redis, session.jti, session.expires_at)

    return session


async def revoke_all_user_sessions(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    revoked_by: str,
    except_session_id: str | None = None,
    redis: object | None = None,
) -> int:
    """Revoke all active sessions for a user.

    Args:
        db: Database session
        tenant_id: Tenant ID
        user_id: User ID
        revoked_by: Username of who revoked
        except_session_id: Keep this session active (current session)
        redis: Optional Redis client for JWT blacklisting

    Returns:
        Number of sessions revoked
    """
    now = _utcnow()
    result = await db.execute(
        select(Session).where(
            and_(
                Session.tenant_id == tenant_id,
                Session.user_id == user_id,
                Session.is_active.is_(True),
            )
        )
    )
    sessions = result.scalars().all()
    count = 0
    for session in sessions:
        if except_session_id and session.session_id == except_session_id:
            continue
        session.is_active = False
        session.revoked_at = now
        session.revoked_by = revoked_by
        await _best_effort_blacklist_session_jti(redis, session.jti, session.expires_at)
        count += 1
    await db.flush()
    return count


async def list_user_sessions(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    include_expired: bool = False,
) -> list[Session]:
    """List sessions for a user.

    Args:
        db: Database session
        tenant_id: Tenant ID
        user_id: User ID
        include_expired: Include expired/revoked sessions

    Returns:
        List of Session objects, newest first
    """
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    query = select(Session).where(
        Session.tenant_id == tenant_id,
        Session.user_id == user_id,
    )
    if not include_expired:
        query = query.where(Session.is_active.is_(True), Session.expires_at > now)
    result = await db.execute(query.order_by(Session.created_at.desc()))
    return list(result.scalars().all())
