from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest import MonkeyPatch
from pytest_mock import MockerFixture

from backend.workers.mqtt_worker import (
    _resolve_event_tenant_id,
    export_mqtt_worker_tenant_contract,
    get_media_path,
    process_event,
)


@pytest.mark.asyncio
async def test_get_media_path(mocker: MockerFixture, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("MEDIA_PATH", "/tmp/default_media")
    mock_session = AsyncMock()

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result

    path = await get_media_path(mock_session)
    assert path == "/tmp/default_media/frigate_snapshots"

    mock_config = MagicMock()
    mock_config.value = "/custom/media"
    mock_result.scalar_one_or_none.return_value = mock_config
    mock_session.execute.return_value = mock_result

    path2 = await get_media_path(mock_session)
    assert path2 == "/custom/media"


@pytest.mark.asyncio
async def test_process_event_no_snapshot(mocker: MockerFixture) -> None:
    await process_event({"type": "new", "after": {"has_snapshot": False}})


@pytest.mark.asyncio
async def test_process_event_success(mocker: MockerFixture) -> None:
    mock_session = AsyncMock()
    mocker.patch("backend.workers.mqtt_worker._async_session_factory", return_value=mock_session)
    mock_session.__aenter__.return_value = mock_session

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result

    mocker.patch("backend.workers.mqtt_worker.get_media_path", new_callable=AsyncMock, return_value="/tmp/media")
    mocker.patch("backend.workers.mqtt_worker.Path.mkdir")
    mock_open = mocker.patch("backend.workers.mqtt_worker.Path.open")

    # session.add is sync method in SQLAlchemy
    mock_session.add = MagicMock()

    ev = {
        "type": "new",
        "after": {
            "id": "event_123",
            "has_snapshot": True,
            "label": "person",
            "camera": "front",
            "tenant_id": "tenant-a",
            "snapshot": base64.b64encode(b"fake_image_bytes").decode("utf-8"),
        },
    }

    await process_event(ev)

    mock_open.assert_called_once()
    mock_session.add.assert_called_once()
    mock_session.commit.assert_awaited()
    saved_asset = mock_session.add.call_args.args[0]
    assert saved_asset.tenant_id == "tenant-a"


@pytest.mark.asyncio
async def test_process_event_sanitizes_camera_path_traversal(mocker: MockerFixture) -> None:
    mock_session = AsyncMock()
    mocker.patch("backend.workers.mqtt_worker._async_session_factory", return_value=mock_session)
    mock_session.__aenter__.return_value = mock_session

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result

    mocker.patch("backend.workers.mqtt_worker.get_media_path", new_callable=AsyncMock, return_value="/tmp/media")
    mocker.patch("backend.workers.mqtt_worker.Path.mkdir")
    mocker.patch("backend.workers.mqtt_worker.Path.open")
    mock_session.add = MagicMock()

    ev = {
        "type": "new",
        "after": {
            "id": "event_123",
            "has_snapshot": True,
            "label": "person",
            "camera": "../../etc/cron.d",
            "tenant_id": "tenant-a",
            "snapshot": base64.b64encode(b"fake_image_bytes").decode("utf-8"),
        },
    }

    await process_event(ev)

    saved_asset = mock_session.add.call_args.args[0]
    assert ".." not in str(saved_asset.file_path)
    assert ".." not in str(saved_asset.camera)


def test_resolve_event_tenant_id_prefers_after_payload() -> None:
    assert _resolve_event_tenant_id({"tenant_id": "tenant-event"}, {"tenant_id": " tenant-after "}) == "tenant-after"
    assert _resolve_event_tenant_id({"tenant_id": " tenant-event "}, {}) == "tenant-event"
    assert _resolve_event_tenant_id({}, {}) is None


def test_mqtt_worker_tenant_contract_exports_no_default_fallback() -> None:
    contract = export_mqtt_worker_tenant_contract()

    assert contract["entrypoint"] == "backend.workers.mqtt_worker.process_event"
    assert contract["tenant_resolver"] == "backend.workers.mqtt_worker._resolve_event_tenant_id"
    assert contract["tenant_sources"] == ["event.after.tenant_id", "event.tenant_id"]
    assert contract["default_tenant_fallback_allowed"] is False
    assert contract["missing_tenant_behavior"] == "drop-and-log"


@pytest.mark.asyncio
async def test_process_event_without_tenant_scope_is_dropped(mocker: MockerFixture) -> None:
    session_factory = mocker.patch("backend.workers.mqtt_worker._async_session_factory")
    log_error = mocker.patch("backend.workers.mqtt_worker.logger.error")

    ev = {
        "type": "new",
        "after": {
            "id": "event_123",
            "has_snapshot": True,
            "label": "person",
            "camera": "front",
            "snapshot": base64.b64encode(b"fake_image_bytes").decode("utf-8"),
        },
    }

    await process_event(ev)

    session_factory.assert_not_called()
    log_error.assert_called_once()
