from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.control_plane.adapters.health import HealthIngestRequest, HealthMeasurement, ingest_health_data


def _measurement(metric_type: str, recorded_at: datetime.datetime) -> HealthMeasurement:
    return HealthMeasurement(
        metric_type=metric_type,
        value=1.0,
        unit="count",
        recorded_at=recorded_at,
        source_platform="ios",
        source_app="test-client",
        meta_info={},
    )


@pytest.mark.asyncio
async def test_ingest_rejects_duplicate_measurements_in_same_batch() -> None:
    now = datetime.datetime(2026, 4, 3, 12, 0, 0, tzinfo=datetime.UTC)
    payload = HealthIngestRequest(
        measurements=[
            _measurement("steps", now),
            _measurement("steps", now),
        ],
        node_id="ios-01",
    )

    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    response = await ingest_health_data(
        payload=payload,
        current_user={"tenant_id": "tenant-a", "sub": "42"},
        db=db,
        redis=None,
    )

    assert response.ingested == 1
    assert response.rejected == 1
    assert any("Duplicate measurement in batch" in err for err in response.errors)


@pytest.mark.asyncio
async def test_ingest_idempotency_blocks_duplicate_batch_when_redis_key_exists() -> None:
    now = datetime.datetime(2026, 4, 3, 12, 0, 0, tzinfo=datetime.UTC)
    payload = HealthIngestRequest(
        measurements=[_measurement("steps", now)],
        node_id="ios-01",
        idempotency_key="health-batch-0001",
    )

    redis = SimpleNamespace(kv=SimpleNamespace(set=AsyncMock(return_value=None)))

    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    response = await ingest_health_data(
        payload=payload,
        current_user={"tenant_id": "tenant-a", "sub": "42"},
        db=db,
        redis=redis,
    )

    assert response.ingested == 0
    assert response.rejected == 0
    assert response.errors == ["Duplicate ingest batch ignored"]
    db.add.assert_not_called()
