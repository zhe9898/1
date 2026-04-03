from __future__ import annotations

import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest import MonkeyPatch
from pytest_mock import MockerFixture

from backend.workers.clip_worker import CLIPInferenceEngine, process_pending_assets


def test_clip_engine_load_cpu(mocker: MockerFixture, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("CAPABILITY_TAGS", "cpu_avx2")
    engine = CLIPInferenceEngine()

    mock_st = MagicMock()
    sys.modules["sentence_transformers"] = mock_st

    engine.load()
    assert engine.device == "cpu"
    assert engine._loaded is True
    del sys.modules["sentence_transformers"]


def test_clip_engine_extract_mock(mocker: MockerFixture) -> None:
    engine = CLIPInferenceEngine()
    engine._loaded = False

    res: dict[str, Any] = engine.extract("test.jpg")
    assert len(res["embedding"]) == 512
    assert len(res["tags"]) == 1
    assert res["tags"][0].endswith("/mock")


@pytest.mark.asyncio
async def test_process_pending_assets(mocker: MockerFixture) -> None:
    engine_mock = mocker.patch("backend.workers.clip_worker.engine")
    engine_mock._loaded = True

    mocker.patch(
        "backend.workers.clip_worker.asyncio.to_thread",
        new_callable=AsyncMock,
        return_value={"embedding": [0.1] * 512, "tags": ["smile"]},
    )

    mock_session = AsyncMock()
    mocker.patch("backend.workers.clip_worker._async_session_factory", return_value=mock_session)
    mock_session.__aenter__.return_value = mock_session

    mock_asset = MagicMock()
    mock_asset.file_path = "test.jpg"
    mock_asset.embedding_status = "pending"

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_asset]
    mock_session.execute.return_value = mock_result

    await process_pending_assets(tenant_id="tenant-a")
    rendered = str(mock_session.execute.await_args.args[0])
    assert "assets.tenant_id" in rendered

    assert mock_asset.embedding_status == "done"
    assert mock_asset.is_emotion_highlight is True
    assert mock_asset.ai_tags == ["smile"]
    mock_session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_process_pending_assets_requires_tenant_scope(mocker: MockerFixture, monkeypatch: MonkeyPatch) -> None:
    mock_session = AsyncMock()
    mocker.patch("backend.workers.clip_worker._async_session_factory", return_value=mock_session)
    mock_session.__aenter__.return_value = mock_session
    monkeypatch.delenv("WORKER_TENANT_ID", raising=False)
    monkeypatch.delenv("TENANT_ID", raising=False)

    await process_pending_assets()

    mock_session.execute.assert_not_awaited()
