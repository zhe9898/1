from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from backend.core.webauthn_challenge_store import WebAuthnChallengeStore
from backend.models.webauthn_challenge import WebAuthnChallenge


def _scalar_result(value: object | None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _challenge(**overrides: object) -> WebAuthnChallenge:
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    challenge = WebAuthnChallenge(
        challenge_id="Y2hhbGxlbmdlLWJ5dGVz",
        session_id="flow-session-1",
        user_id="7",
        tenant_id="tenant-a",
        flow="login",
        expires_at=now + datetime.timedelta(minutes=5),
        used_at=None,
        created_at=now,
    )
    for key, value in overrides.items():
        setattr(challenge, key, value)
    return challenge


@pytest.mark.asyncio
async def test_get_or_create_reuses_active_unconsumed_challenge() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result(_challenge()))
    db.flush = AsyncMock()
    db.add = MagicMock()
    redis = AsyncMock()
    redis.set_auth_challenge = AsyncMock(return_value=True)

    def _builder(challenge: bytes | None) -> tuple[bytes, str, str]:
        assert challenge is not None
        return challenge, "Y2hhbGxlbmdlLWJ5dGVz", '{"challenge":"Y2hhbGxlbmdlLWJ5dGVz"}'

    stored, options = await WebAuthnChallengeStore.get_or_create(
        db,
        redis,
        session_id="flow-session-1",
        user_id="7",
        tenant_id="tenant-a",
        flow="login",
        ttl_seconds=300,
        options_builder=_builder,
    )

    assert stored.challenge_id == "Y2hhbGxlbmdlLWJ5dGVz"
    assert options == {"challenge": "Y2hhbGxlbmdlLWJ5dGVz"}
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_consume_rejects_cross_session_challenge() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result(_challenge(session_id="flow-session-a")))
    db.flush = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await WebAuthnChallengeStore.consume(
            db,
            None,
            credential={"response": {"clientDataJSON": "eyJjaGFsbGVuZ2UiOiAiWTJoaGJHeGxibWRsTFdKNWRHVnoifQ"}},
            expected_flow="login",
            expected_session_id="flow-session-b",
            expected_user_id="7",
            expected_tenant_id="tenant-a",
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_consume_marks_challenge_used_on_success() -> None:
    record = _challenge()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result(record))
    db.flush = AsyncMock()

    stored = await WebAuthnChallengeStore.consume(
        db,
        None,
        credential={"response": {"clientDataJSON": "eyJjaGFsbGVuZ2UiOiAiWTJoaGJHeGxibWRsTFdKNWRHVnoifQ"}},
        expected_flow="login",
        expected_session_id="flow-session-1",
        expected_user_id="7",
        expected_tenant_id="tenant-a",
    )

    assert stored.challenge_id == "Y2hhbGxlbmdlLWJ5dGVz"
    assert record.used_at is not None
    db.flush.assert_awaited_once()
