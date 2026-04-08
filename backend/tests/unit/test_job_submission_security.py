from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from backend.api.jobs.models import JobCreateRequest
from backend.api.jobs.submission import submit_job
from backend.kernel.extensions.job_kind_registry import validate_job_payload


def _job_db() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_submit_job_allows_safe_kind_for_scoped_non_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _job_db()
    monkeypatch.setattr(
        "backend.kernel.scheduling.scheduling_resilience.AdmissionController.check_admission",
        AsyncMock(return_value=(True, "", {})),
    )
    monkeypatch.setattr("backend.api.jobs.submission.acquire_transaction_advisory_locks", AsyncMock(return_value=None))
    monkeypatch.setattr("backend.api.jobs.submission.check_concurrent_limits", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "backend.api.jobs.submission.resolve_job_queue_contract",
        lambda **_: ("interactive", "default"),
    )
    monkeypatch.setattr("backend.api.jobs.submission._append_log", AsyncMock(return_value=None))
    monkeypatch.setattr("backend.api.jobs.submission.publish_control_event", AsyncMock(return_value=None))

    response = await submit_job(
        JobCreateRequest(kind="noop", payload={}),
        current_user={
            "sub": "user-1",
            "username": "family-user",
            "tenant_id": "default",
            "role": "family",
            "scopes": ["write:jobs"],
        },
        db=db,
        redis=None,
    )

    assert response.kind == "noop"
    db.add.assert_called_once()


@pytest.mark.asyncio
async def test_submit_job_rejects_privileged_kind_for_non_admin() -> None:
    with pytest.raises(HTTPException) as exc:
        await submit_job(
            JobCreateRequest(kind="shell.exec", payload={"command": "echo blocked"}),
            current_user={
                "sub": "user-1",
                "username": "family-user",
                "tenant_id": "default",
                "role": "family",
                "scopes": ["write:jobs", "admin:jobs"],
            },
            db=_job_db(),
            redis=None,
        )

    assert exc.value.status_code == 403
    assert "requires admin privileges" in str(exc.value.detail)


def test_validate_job_payload_rejects_private_http_targets() -> None:
    with pytest.raises(ValueError, match="private, loopback, or link-local"):
        validate_job_payload("http.request", {"url": "http://127.0.0.1:8080/internal"})


def test_validate_job_payload_rejects_file_transfer_uris() -> None:
    with pytest.raises(ValueError, match="local filesystem path"):
        validate_job_payload("file.transfer", {"src": "file:///etc/shadow", "dst": "/tmp/exfil"})


def test_validate_job_payload_rejects_invalid_script_interpreter() -> None:
    with pytest.raises(ValueError):
        validate_job_payload(
            "script.run",
            {"interpreter": "perl", "script": "print('blocked')"},
        )


def test_validate_job_payload_rejects_plain_http_wasm_modules() -> None:
    with pytest.raises(ValueError, match="must use one of: https"):
        validate_job_payload("wasm.run", {"module_uri": "http://example.test/runner.wasm"})


def test_validate_job_payload_rejects_plain_http_ml_inputs() -> None:
    with pytest.raises(ValueError, match="must use one of: https, s3"):
        validate_job_payload(
            "ml.inference",
            {"model_id": "model-a", "input_uri": "http://example.test/input.json"},
        )


def test_validate_job_payload_rejects_file_media_inputs() -> None:
    with pytest.raises(ValueError, match="must use one of: https, s3"):
        validate_job_payload(
            "media.transcode",
            {"input_uri": "file:///etc/shadow", "output_uri": "s3://bucket/output.mp4"},
        )


def test_validate_job_payload_rejects_non_rsync_data_sync_uris() -> None:
    with pytest.raises(ValueError, match="must use one of: rsync"):
        validate_job_payload(
            "data.sync",
            {"source_uri": "https://example.test/data", "dest_uri": "rsync://cluster-a/bucket"},
        )
