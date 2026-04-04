from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.core.auth_helpers import check_webauthn_rate_limit


@pytest.mark.asyncio
async def test_webauthn_rate_limit_falls_back_to_local_bucket() -> None:
    for _ in range(20):
        await check_webauthn_rate_limit(None, "127.0.0.2", "rid")

    with pytest.raises(HTTPException) as exc:
        await check_webauthn_rate_limit(None, "127.0.0.2", "rid")

    assert exc.value.status_code == 429
