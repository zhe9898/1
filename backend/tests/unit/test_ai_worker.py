from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_mock import MockerFixture

import backend.workers.ai_worker as ai_worker
from backend.workers.ai_worker import get_model, process_pending_assets


def test_get_model_dry_run(mocker: MockerFixture) -> None:
    ai_worker.model_instance = None
    ai_worker.HAS_MODEL = True

    mocker.patch.dict(sys.modules, {"sentence_transformers": None})
    res = get_model()
    assert res is None
    assert ai_worker.HAS_MODEL is False


def test_get_model_success(mocker: MockerFixture) -> None:
    ai_worker.model_instance = None
    ai_worker.HAS_MODEL = True

    mock_st = MagicMock()
    mocker.patch.dict(sys.modules, {"sentence_transformers": mock_st})
    res = get_model()
    assert res is not None
    assert ai_worker.HAS_MODEL is True


@pytest.mark.asyncio
async def test_process_pending_assets_no_tasks(
    mocker: MockerFixture,
) -> None:
    mock_session = AsyncMock()
    mocker.patch(
        "backend.workers.ai_worker.AsyncSessionLocal",
        return_value=mock_session,
    )
    mock_session.__aenter__.return_value = mock_session

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute.return_value = mock_result

    count = await process_pending_assets(tenant_id="tenant-a")
    assert count == 0


@pytest.mark.asyncio
async def test_process_pending_assets_success(
    mocker: MockerFixture,
) -> None:
    mock_session = AsyncMock()
    mocker.patch(
        "backend.workers.ai_worker.AsyncSessionLocal",
        return_value=mock_session,
    )
    mock_session.__aenter__.return_value = mock_session

    mock_asset = MagicMock()
    mock_asset.embedding_status = "pending"
    mock_asset.asset_type = "image/jpeg"
    mock_asset.file_path = "/tmp/fake.jpg"

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_asset]
    mock_session.execute.return_value = mock_result

    mocker.patch("backend.workers.ai_worker.get_model", return_value=MagicMock())
    ai_worker.HAS_MODEL = True

    mocker.patch("backend.workers.ai_worker.Path.exists", return_value=True)
    mocker.patch("backend.workers.ai_worker.Image.open")

    count = await process_pending_assets(tenant_id="tenant-a")
    rendered = str(mock_session.execute.await_args.args[0])
    assert "assets.tenant_id" in rendered
    assert count == 1
    assert mock_asset.embedding_status == "done"


@pytest.mark.asyncio
async def test_process_pending_assets_no_model(
    mocker: MockerFixture,
) -> None:
    mock_session = AsyncMock()
    mocker.patch(
        "backend.workers.ai_worker.AsyncSessionLocal",
        return_value=mock_session,
    )
    mock_session.__aenter__.return_value = mock_session

    mock_asset = MagicMock()
    mock_asset.embedding_status = "pending"
    mock_asset.asset_type = "image/jpeg"

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_asset]
    mock_session.execute.return_value = mock_result

    mocker.patch("backend.workers.ai_worker.get_model", return_value=None)
    ai_worker.HAS_MODEL = False

    count = await process_pending_assets(tenant_id="tenant-a")
    assert count == 0
    assert mock_asset.embedding_status == "failed"


@pytest.mark.asyncio
async def test_process_pending_assets_requires_tenant_scope(mocker: MockerFixture, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    mock_session = AsyncMock()
    mocker.patch("backend.workers.ai_worker.AsyncSessionLocal", return_value=mock_session)
    mock_session.__aenter__.return_value = mock_session
    monkeypatch.delenv("WORKER_TENANT_ID", raising=False)
    monkeypatch.delenv("TENANT_ID", raising=False)

    count = await process_pending_assets()

    assert count == 0
    mock_session.execute.assert_not_awaited()
