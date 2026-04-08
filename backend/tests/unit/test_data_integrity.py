from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
import respx
from httpx import Response

import backend.sentinel.data_integrity as data_integrity


@pytest.fixture(autouse=True)
def isolated_db(mocker: Any, tmp_path: Path) -> Generator[None, None, None]:
    db_path = tmp_path / "bit_rot_baseline.db"
    mocker.patch.object(data_integrity, "DB_PATH", db_path)
    if db_path.exists():
        db_path.unlink()
    data_integrity.init_baseline_db()
    yield
    for _ in range(3):
        try:
            if db_path.exists():
                db_path.unlink()
            break
        except PermissionError:
            time.sleep(0.05)


@pytest.fixture
def temp_test_file(tmp_path: Path) -> str:
    test_file = tmp_path / "video_record.mp4"
    test_file.write_text("INITIAL_VIDEO_DATA_BLOCK", encoding="utf-8")
    return str(test_file)


def test_compute_sha256(temp_test_file: str) -> None:
    hash_val = data_integrity.compute_sha256(temp_test_file)
    assert hash_val is not None
    assert len(hash_val) == 64


def test_cpu_load_avoidance(mocker: Any, temp_test_file: str) -> None:
    mocker.patch("backend.sentinel.data_integrity.psutil.cpu_percent", return_value=99.0)

    data_integrity.scan_and_verify_directory(str(Path(temp_test_file).parent))

    conn = sqlite3.connect(data_integrity.DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM file_hashes")
    assert cur.fetchone()[0] == 0
    conn.close()


def test_first_run_creates_baseline(mocker: Any, temp_test_file: str) -> None:
    mocker.patch("backend.sentinel.data_integrity.psutil.cpu_percent", return_value=5.0)
    target_dir = str(Path(temp_test_file).parent)

    data_integrity.scan_and_verify_directory(target_dir)

    conn = sqlite3.connect(data_integrity.DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT sha256, size FROM file_hashes WHERE filepath = ?", (temp_test_file,))
    row = cur.fetchone()

    assert row is not None
    assert row[1] == Path(temp_test_file).stat().st_size
    conn.close()


def test_bit_rot_detection(mocker: Any, temp_test_file: str) -> None:
    mocker.patch("backend.sentinel.data_integrity.psutil.cpu_percent", return_value=5.0)
    target_dir = str(Path(temp_test_file).parent)

    data_integrity.scan_and_verify_directory(target_dir)

    with Path(temp_test_file).open("r+", encoding="utf-8") as handle:
        content = handle.read()
        handle.seek(0)
        handle.write(content.replace("I", "X"))

    spy_logger = mocker.spy(logging.getLogger("zen70.sentinel.bit_rot"), "critical")

    data_integrity.scan_and_verify_directory(target_dir)

    spy_logger.assert_called()
    assert "bit_rot_corruption_detected" in spy_logger.call_args_list[0][0][0]


def test_invalid_directory_is_guarded(mocker: Any) -> None:
    mocker.patch("backend.sentinel.data_integrity.psutil.cpu_percent", return_value=5.0)
    spy_logger = mocker.spy(logging.getLogger("zen70.sentinel.bit_rot"), "error")
    data_integrity.scan_and_verify_directory("Z:/path/not-exists")
    spy_logger.assert_called()
    assert "bit_rot_invalid_directory" in spy_logger.call_args_list[0][0][0]


def test_alert_webhook_called_for_corruption(
    mocker: Any,
    monkeypatch: pytest.MonkeyPatch,
    temp_test_file: str,
) -> None:
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "http://example.com/webhook")
    mocker.patch("backend.sentinel.data_integrity.psutil.cpu_percent", return_value=5.0)
    post_spy = mocker.patch("backend.sentinel.data_integrity.post_public_webhook", return_value=True)

    target_dir = str(Path(temp_test_file).parent)
    data_integrity.scan_and_verify_directory(target_dir)
    with Path(temp_test_file).open("r+", encoding="utf-8") as handle:
        content = handle.read()
        handle.seek(0)
        handle.write(content.replace("I", "X"))
    data_integrity.scan_and_verify_directory(target_dir)

    post_spy.assert_called_once()


@respx.mock
def test_alert_webhook_payload_contract(
    monkeypatch: pytest.MonkeyPatch,
    mocker: Any,
    temp_test_file: str,
) -> None:
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "http://alert.local/webhook")
    mocker.patch("backend.sentinel.data_integrity.psutil.cpu_percent", return_value=5.0)
    route = respx.post("http://alert.local/webhook").mock(return_value=Response(200))

    target_dir = str(Path(temp_test_file).parent)
    data_integrity.scan_and_verify_directory(target_dir)
    with Path(temp_test_file).open("r+", encoding="utf-8") as handle:
        content = handle.read()
        handle.seek(0)
        handle.write(content.replace("I", "X"))
    data_integrity.scan_and_verify_directory(target_dir)

    assert route.called
    request_payload = route.calls.last.request.read().decode("utf-8")
    assert "level" in request_payload
    assert "critical" in request_payload
    assert "source" in request_payload
    assert "data_integrity" in request_payload


def test_alert_timeout_reads_from_env(
    mocker: Any,
    monkeypatch: pytest.MonkeyPatch,
    temp_test_file: str,
) -> None:
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "http://example.com/webhook")
    monkeypatch.setenv("BIT_ROT_ALERT_TIMEOUT_SECONDS", "9.5")
    mocker.patch("backend.sentinel.data_integrity.psutil.cpu_percent", return_value=5.0)
    webhook_mock = mocker.patch("backend.sentinel.data_integrity.post_public_webhook", return_value=True)

    target_dir = str(Path(temp_test_file).parent)
    data_integrity.scan_and_verify_directory(target_dir)
    with Path(temp_test_file).open("r+", encoding="utf-8") as handle:
        content = handle.read()
        handle.seek(0)
        handle.write(content.replace("I", "X"))
    data_integrity.scan_and_verify_directory(target_dir)

    assert webhook_mock.call_args is not None
    assert webhook_mock.call_args.kwargs["timeout"] == 9.5


def test_invalid_numeric_env_falls_back_to_defaults(
    mocker: Any,
    monkeypatch: pytest.MonkeyPatch,
    temp_test_file: str,
) -> None:
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "http://example.com/webhook")
    monkeypatch.setenv("BIT_ROT_ALERT_TIMEOUT_SECONDS", "bad-value")
    monkeypatch.setenv("BIT_ROT_SQLITE_CONNECT_TIMEOUT_SECONDS", "-1")
    monkeypatch.setenv("BIT_ROT_SQLITE_BUSY_TIMEOUT_MS", "0")
    mocker.patch("backend.sentinel.data_integrity.psutil.cpu_percent", return_value=5.0)
    webhook_mock = mocker.patch("backend.sentinel.data_integrity.post_public_webhook", return_value=True)

    target_dir = str(Path(temp_test_file).parent)
    data_integrity.scan_and_verify_directory(target_dir)
    with Path(temp_test_file).open("r+", encoding="utf-8") as handle:
        content = handle.read()
        handle.seek(0)
        handle.write(content.replace("I", "X"))
    data_integrity.scan_and_verify_directory(target_dir)

    assert webhook_mock.call_args is not None
    assert webhook_mock.call_args.kwargs["timeout"] == data_integrity.DEFAULT_ALERT_TIMEOUT_SECONDS


def test_scan_performance_boundary(
    mocker: Any,
    tmp_path: Path,
) -> None:
    mocker.patch("backend.sentinel.data_integrity.psutil.cpu_percent", return_value=5.0)
    for idx in range(100):
        (tmp_path / f"payload_{idx}.txt").write_text(f"block-{idx}", encoding="utf-8")

    start = time.perf_counter()
    data_integrity.scan_and_verify_directory(str(tmp_path))
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0
