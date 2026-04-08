from __future__ import annotations

import datetime
import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn.helpers import base64url_to_bytes

from backend.control_plane.auth.auth_helpers import CODE_BAD_REQUEST, CODE_SERVER_ERROR, CODE_UNAUTHORIZED, get_challenge_from_credential
from backend.kernel.contracts.errors import zen
from backend.models.webauthn_challenge import WebAuthnChallenge
from backend.platform.redis.client import RedisClient

ChallengeOptionsBuilder = Callable[[bytes | None], tuple[bytes, str, str]]


@dataclass(frozen=True, slots=True)
class StoredWebAuthnChallenge:
    challenge_id: str
    session_id: str
    user_id: str
    tenant_id: str
    flow: str
    expires_at: datetime.datetime
    used_at: datetime.datetime | None
    created_at: datetime.datetime


class WebAuthnChallengeStore:
    @staticmethod
    async def get_or_create(
        db: AsyncSession,
        redis: RedisClient | None,
        *,
        session_id: str,
        user_id: str,
        tenant_id: str,
        flow: str,
        ttl_seconds: int,
        options_builder: ChallengeOptionsBuilder,
    ) -> tuple[StoredWebAuthnChallenge, dict[str, object]]:
        now = _utcnow()
        existing = await WebAuthnChallengeStore._load_reusable_challenge(
            db,
            session_id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
            flow=flow,
            now=now,
        )
        if existing is not None:
            _, _, options_json = options_builder(base64url_to_bytes(existing.challenge_id))
            await WebAuthnChallengeStore._cache_snapshot(redis, existing, ttl_seconds=_remaining_ttl_seconds(existing.expires_at, now))
            return existing, _options_dict(options_json)

        _, challenge_id, options_json = options_builder(None)
        record = WebAuthnChallenge(
            challenge_id=challenge_id,
            session_id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
            flow=flow,
            expires_at=now + datetime.timedelta(seconds=ttl_seconds),
            used_at=None,
            created_at=now,
        )
        add_result: object = getattr(db, "add")(record)
        if inspect.isawaitable(add_result):
            await add_result
        await db.flush()

        stored = _snapshot(record)
        await WebAuthnChallengeStore._cache_snapshot(redis, stored, ttl_seconds=ttl_seconds)
        return stored, _options_dict(options_json)

    @staticmethod
    async def consume(
        db: AsyncSession,
        redis: RedisClient | None,
        *,
        credential: dict[str, object],
        expected_flow: str,
        expected_session_id: str,
        expected_user_id: str,
        expected_tenant_id: str,
    ) -> StoredWebAuthnChallenge:
        challenge_id = get_challenge_from_credential(credential)
        if not challenge_id:
            raise zen(
                CODE_BAD_REQUEST,
                "Invalid credential: missing challenge",
                status_code=400,
            )
        now = _utcnow()
        statement: Select[tuple[WebAuthnChallenge]] = select(WebAuthnChallenge).where(WebAuthnChallenge.challenge_id == challenge_id).with_for_update()
        result = await db.execute(statement)
        record = result.scalar_one_or_none()
        if record is None:
            raise zen(
                CODE_UNAUTHORIZED,
                "Challenge expired or already used",
                status_code=401,
            )
        if record.expires_at <= now or record.used_at is not None:
            await WebAuthnChallengeStore._clear_cache(redis, record.challenge_id)
            raise zen(
                CODE_UNAUTHORIZED,
                "Challenge expired or already used",
                status_code=401,
            )
        if record.flow != expected_flow:
            raise zen(CODE_BAD_REQUEST, "Invalid challenge flow", status_code=400)
        if record.session_id != expected_session_id:
            raise zen(
                "ZEN-AUTH-4032",
                "Challenge no longer matches the current browser session",
                status_code=403,
                recovery_hint="Restart the WebAuthn flow in the same browser tab and retry",
            )
        if record.user_id != expected_user_id:
            raise zen(CODE_BAD_REQUEST, "Challenge user mismatch", status_code=400)
        if record.tenant_id != expected_tenant_id:
            raise zen(CODE_BAD_REQUEST, "Challenge tenant mismatch", status_code=400)

        record.used_at = now
        await db.flush()
        await WebAuthnChallengeStore._clear_cache(redis, record.challenge_id)
        return _snapshot(record)

    @staticmethod
    async def _load_reusable_challenge(
        db: AsyncSession,
        *,
        session_id: str,
        user_id: str,
        tenant_id: str,
        flow: str,
        now: datetime.datetime,
    ) -> StoredWebAuthnChallenge | None:
        result = await db.execute(
            select(WebAuthnChallenge)
            .where(
                WebAuthnChallenge.session_id == session_id,
                WebAuthnChallenge.user_id == user_id,
                WebAuthnChallenge.tenant_id == tenant_id,
                WebAuthnChallenge.flow == flow,
                WebAuthnChallenge.used_at.is_(None),
                WebAuthnChallenge.expires_at > now,
            )
            .order_by(WebAuthnChallenge.created_at.desc())
            .limit(1)
        )
        record = result.scalar_one_or_none()
        return _snapshot(record) if isinstance(record, WebAuthnChallenge) else None

    @staticmethod
    async def _cache_snapshot(
        redis: RedisClient | None,
        challenge: StoredWebAuthnChallenge,
        *,
        ttl_seconds: int,
    ) -> None:
        if redis is None or ttl_seconds <= 0:
            return
        payload = json.dumps(
            {
                "challenge_id": challenge.challenge_id,
                "session_id": challenge.session_id,
                "user_id": challenge.user_id,
                "tenant_id": challenge.tenant_id,
                "flow": challenge.flow,
                "expires_at": challenge.expires_at.isoformat(),
                "used_at": challenge.used_at.isoformat() if challenge.used_at else None,
                "created_at": challenge.created_at.isoformat(),
            }
        )
        try:
            await redis.auth_challenges.store(challenge.challenge_id, payload, ttl_seconds=ttl_seconds)
        except Exception:
            return

    @staticmethod
    async def _clear_cache(redis: RedisClient | None, challenge_id: str) -> None:
        if redis is None:
            return
        try:
            await redis.kv.delete(f"auth:challenge:{challenge_id}")
        except Exception:
            return


def _snapshot(record: WebAuthnChallenge) -> StoredWebAuthnChallenge:
    return StoredWebAuthnChallenge(
        challenge_id=record.challenge_id,
        session_id=record.session_id,
        user_id=record.user_id,
        tenant_id=record.tenant_id,
        flow=record.flow,
        expires_at=record.expires_at,
        used_at=record.used_at,
        created_at=record.created_at,
    )


def _options_dict(options_json: str) -> dict[str, object]:
    try:
        parsed = json.loads(options_json)
    except json.JSONDecodeError as exc:
        raise zen(CODE_SERVER_ERROR, "Challenge options JSON is invalid", status_code=500) from exc
    if not isinstance(parsed, dict):
        raise zen(CODE_SERVER_ERROR, "Challenge options payload must be an object", status_code=500)
    return parsed


def _remaining_ttl_seconds(expires_at: datetime.datetime, now: datetime.datetime) -> int:
    return max(int((expires_at - now).total_seconds()), 1)


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
