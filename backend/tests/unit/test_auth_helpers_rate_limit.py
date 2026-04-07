from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.core.auth_helpers import _LOCAL_WEBAUTHN_RATE_BUCKETS, _LOCAL_WEBAUTHN_RATE_LOCK, _check_local_webauthn_rate_limit, check_webauthn_rate_limit


@pytest.mark.asyncio
async def test_webauthn_rate_limit_falls_back_to_local_bucket() -> None:
    with _LOCAL_WEBAUTHN_RATE_LOCK:
        _LOCAL_WEBAUTHN_RATE_BUCKETS.clear()
    for _ in range(20):
        await check_webauthn_rate_limit(None, "127.0.0.2", "rid")

    with pytest.raises(HTTPException) as exc:
        await check_webauthn_rate_limit(None, "127.0.0.2", "rid")

    assert exc.value.status_code == 429
    with _LOCAL_WEBAUTHN_RATE_LOCK:
        _LOCAL_WEBAUTHN_RATE_BUCKETS.clear()


def test_local_webauthn_rate_limit_prunes_expired_buckets() -> None:
    with _LOCAL_WEBAUTHN_RATE_LOCK:
        _LOCAL_WEBAUTHN_RATE_BUCKETS.clear()
        _LOCAL_WEBAUTHN_RATE_BUCKETS["expired"] = (1, 0.0)

    _check_local_webauthn_rate_limit("127.0.0.9")

    with _LOCAL_WEBAUTHN_RATE_LOCK:
        assert "expired" not in _LOCAL_WEBAUTHN_RATE_BUCKETS
        _LOCAL_WEBAUTHN_RATE_BUCKETS.clear()
