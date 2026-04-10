from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.extensions.alerting import _fire_alert


@pytest.mark.asyncio
async def test_fire_alert_suppresses_duplicate_within_window() -> None:
    db = AsyncMock()
    db.add = MagicMock()
    duplicate_query = MagicMock()
    duplicate_query.first.return_value = object()
    db.execute.return_value = duplicate_query

    rule = SimpleNamespace(
        tenant_id="tenant-a",
        id=11,
        name="offline",
        severity="warning",
        action={"type": "log"},
    )

    alert = await _fire_alert(
        db,
        rule,
        "duplicate message",
        {"node_id": "n1"},
        dedup_window_s=300,
    )

    assert alert is None
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_fire_alert_creates_record_when_no_recent_duplicate() -> None:
    db = AsyncMock()
    db.add = MagicMock()
    duplicate_query = MagicMock()
    duplicate_query.first.return_value = None
    db.execute.return_value = duplicate_query

    rule = SimpleNamespace(
        tenant_id="tenant-a",
        id=22,
        name="offline",
        severity="warning",
        action={"type": "log"},
    )

    alert = await _fire_alert(
        db,
        rule,
        "new message",
        {"node_id": "n2"},
        dedup_window_s=300,
    )

    assert alert is not None
    db.add.assert_called_once()
    db.flush.assert_awaited()
