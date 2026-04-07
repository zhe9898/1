from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import backend.core.rls as rls_mod
from backend.core.rls import apply_rls_policies, assert_rls_ready, validate_rls_runtime_mode


@pytest.mark.asyncio
async def test_apply_rls_policies_fails_fast_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZEN70_RLS_ALLOW_SOFT_FAIL", raising=False)
    session = AsyncMock()
    session.execute.side_effect = RuntimeError("boom")
    session.rollback = AsyncMock()

    with pytest.raises(RuntimeError):
        await apply_rls_policies(session)

    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_rls_policies_can_soft_fail_when_explicitly_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZEN70_RLS_ALLOW_SOFT_FAIL", "true")
    monkeypatch.setenv("ZEN70_ENV", "development")
    session = AsyncMock()
    session.execute.side_effect = RuntimeError("boom")
    session.rollback = AsyncMock()

    await apply_rls_policies(session)

    session.rollback.assert_awaited_once()


def test_production_disallows_soft_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZEN70_ENV", "production")
    monkeypatch.setenv("ZEN70_RLS_ALLOW_SOFT_FAIL", "true")

    with pytest.raises(RuntimeError, match="cannot be enabled in production"):
        validate_rls_runtime_mode()


@pytest.mark.asyncio
async def test_assert_rls_ready_fails_when_policy_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    session = AsyncMock()
    session.info = {}
    monkeypatch.setattr(rls_mod, "_TENANT_TABLES", ("users",))
    session.execute.side_effect = [
        MagicMock(scalar=MagicMock(return_value=True)),
        MagicMock(scalar=MagicMock(return_value=True)),
        MagicMock(first=MagicMock(return_value=(True, True))),
        MagicMock(scalar=MagicMock(return_value=False)),
    ]

    with pytest.raises(RuntimeError, match="missing_policy"):
        await assert_rls_ready(session)
