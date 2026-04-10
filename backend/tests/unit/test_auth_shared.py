from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from backend.control_plane.adapters.auth_shared import register_login_session


@pytest.mark.anyio
async def test_register_login_session_propagates_session_contract_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    create_session = AsyncMock(side_effect=RuntimeError("session store unavailable"))
    monkeypatch.setattr("backend.control_plane.auth.sessions.create_session", create_session)

    with pytest.raises(RuntimeError, match="session store unavailable"):
        await register_login_session(
            AsyncMock(),
            tenant_id="tenant-a",
            user_id="user-1",
            username="alice",
            session_id="session-1",
            token_id="jti-1",
            ip_address="127.0.0.1",
            user_agent="test-agent",
            auth_method="password",
            redis=object(),
        )
