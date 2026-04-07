from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.api.auth_bootstrap import BOOTSTRAP_LOCK_TTL_SECONDS, bootstrap
from backend.api.models.auth import BootstrapRequest


@pytest.mark.asyncio
async def test_bootstrap_commits_before_releasing_lock() -> None:
    db = AsyncMock()
    db.add = MagicMock()
    call_order: list[str] = []

    async def _commit() -> None:
        call_order.append("commit")

    db.commit.side_effect = _commit

    redis = SimpleNamespace()
    redis.acquire_lock = AsyncMock(return_value=True)

    async def _release_lock(_name: str) -> bool:
        call_order.append("release")
        return True

    redis.release_lock = _release_lock

    with (
        patch("backend.api.auth_shared.first_user_or_schema_unavailable", new=AsyncMock(return_value=None)),
        patch("backend.api.auth_bootstrap.register_login_session", new=AsyncMock()),
    ):
        response = await bootstrap(
            BootstrapRequest(username="admin", password="secret123", display_name="Admin"),
            MagicMock(),
            db=db,
            redis=redis,
        )

    assert response.authenticated is True
    assert response.role == "admin"
    assert call_order == ["commit", "release"]
    redis.acquire_lock.assert_awaited_once()
    assert redis.acquire_lock.await_args.kwargs["ttl"] == BOOTSTRAP_LOCK_TTL_SECONDS
