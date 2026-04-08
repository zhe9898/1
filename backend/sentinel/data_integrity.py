"""Bit-rot detection for cold data directories.

This sentinel keeps a local SQLite baseline of file hashes and raises
critical alerts when a file changes without a size change, which is a
practical proxy for silent corruption on cold storage.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import time
from pathlib import Path

from backend.platform.http.webhooks import post_public_webhook

try:
    import psutil
except ImportError:  # pragma: no cover - optional runtime dependency
    psutil = None

logger = logging.getLogger("zen70.sentinel.bit_rot")

DB_PATH = Path(__file__).parent / "bit_rot_baseline.db"
DEFAULT_CPU_LOAD_THRESHOLD_PERCENT = 50.0
DEFAULT_ALERT_TIMEOUT_SECONDS = 5.0
DEFAULT_SQLITE_CONNECT_TIMEOUT_SECONDS = 5.0
DEFAULT_SQLITE_BUSY_TIMEOUT_MS = 5000.0

ENV_CPU_LOAD_THRESHOLD_PERCENT = "BIT_ROT_CPU_LOAD_THRESHOLD_PERCENT"
ENV_ALERT_TIMEOUT_SECONDS = "BIT_ROT_ALERT_TIMEOUT_SECONDS"
ENV_SQLITE_CONNECT_TIMEOUT_SECONDS = "BIT_ROT_SQLITE_CONNECT_TIMEOUT_SECONDS"
ENV_SQLITE_BUSY_TIMEOUT_MS = "BIT_ROT_SQLITE_BUSY_TIMEOUT_MS"


def _read_positive_float_env(env_name: str, default: float) -> float:
    raw_value = os.getenv(env_name, "").strip()
    if not raw_value:
        return default
    try:
        parsed = float(raw_value)
    except ValueError:
        logger.warning("invalid_env_value: %s=%r fallback=%s", env_name, raw_value, default)
        return default
    if parsed <= 0:
        logger.warning("non_positive_env_value: %s=%r fallback=%s", env_name, raw_value, default)
        return default
    return parsed


def _connect_baseline_db() -> sqlite3.Connection:
    connect_timeout_seconds = _read_positive_float_env(
        ENV_SQLITE_CONNECT_TIMEOUT_SECONDS,
        DEFAULT_SQLITE_CONNECT_TIMEOUT_SECONDS,
    )
    busy_timeout_ms = int(
        _read_positive_float_env(
            ENV_SQLITE_BUSY_TIMEOUT_MS,
            DEFAULT_SQLITE_BUSY_TIMEOUT_MS,
        )
    )
    conn = sqlite3.connect(DB_PATH, timeout=connect_timeout_seconds)
    conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
    return conn


def init_baseline_db() -> None:
    with _connect_baseline_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS file_hashes (
                filepath TEXT PRIMARY KEY,
                sha256 TEXT NOT NULL,
                last_checked REAL NOT NULL,
                size INTEGER NOT NULL
            )
            """
        )
        conn.commit()


def compute_sha256(filepath: str, blocksize: int = 65536) -> str | None:
    hasher = hashlib.sha256()
    try:
        with Path(filepath).open("rb") as handle:
            while True:
                block = handle.read(blocksize)
                if not block:
                    break
                hasher.update(block)
    except OSError as exc:
        logger.error("hash_read_failed: file=%s error=%s", filepath, exc)
        return None
    return hasher.hexdigest()


def check_system_load_safe() -> bool:
    cpu_threshold = _read_positive_float_env(
        ENV_CPU_LOAD_THRESHOLD_PERCENT,
        DEFAULT_CPU_LOAD_THRESHOLD_PERCENT,
    )
    if psutil is None:
        logger.warning("psutil_unavailable_skip_load_gate")
        return True
    cpu_usage = psutil.cpu_percent(interval=1)
    if cpu_usage > cpu_threshold:
        logger.info("bit_rot_scan_skipped_due_to_cpu_load: usage=%s threshold=%s", cpu_usage, cpu_threshold)
        return False
    return True


def _record_new_file(filepath: Path, cursor: sqlite3.Cursor, current_size: int) -> None:
    file_hash = compute_sha256(str(filepath))
    if not file_hash:
        return
    cursor.execute(
        "INSERT INTO file_hashes (filepath, sha256, last_checked, size) VALUES (?, ?, ?, ?)",
        (str(filepath), file_hash, time.time(), current_size),
    )


def _refresh_file_baseline(filepath: Path, cursor: sqlite3.Cursor, current_size: int) -> None:
    file_hash = compute_sha256(str(filepath))
    if not file_hash:
        return
    cursor.execute(
        "UPDATE file_hashes SET sha256 = ?, size = ?, last_checked = ? WHERE filepath = ?",
        (file_hash, current_size, time.time(), str(filepath)),
    )


def _verify_single_file(filepath: Path, cursor: sqlite3.Cursor, corrupted_files: list[Path]) -> None:
    if not filepath.is_file() or filepath.is_symlink() or filepath.name.startswith("."):
        return

    try:
        current_size = filepath.stat().st_size
    except OSError:
        return

    cursor.execute(
        "SELECT sha256, size FROM file_hashes WHERE filepath = ?",
        (str(filepath),),
    )
    row = cursor.fetchone()
    if row is None:
        logger.info("bit_rot_baseline_created: file=%s", filepath)
        _record_new_file(filepath, cursor, current_size)
        return

    baseline_hash, baseline_size = row
    if current_size != baseline_size:
        _refresh_file_baseline(filepath, cursor, current_size)
        return

    current_hash = compute_sha256(str(filepath))
    if current_hash and current_hash != baseline_hash:
        logger.critical("bit_rot_corruption_detected: file=%s", filepath)
        corrupted_files.append(filepath)


def _scan_directory_against_baseline(target: Path) -> list[Path]:
    corrupted_files: list[Path] = []
    with _connect_baseline_db() as conn:
        cursor = conn.cursor()
        for filepath in target.rglob("*"):
            _verify_single_file(filepath, cursor, corrupted_files)
        conn.commit()
    return corrupted_files


def _send_corruption_alert(corrupted_files: list[Path]) -> None:
    alert_webhook = os.getenv("ALERT_WEBHOOK_URL", "").strip()
    if not alert_webhook:
        return

    timeout_seconds = _read_positive_float_env(
        ENV_ALERT_TIMEOUT_SECONDS,
        DEFAULT_ALERT_TIMEOUT_SECONDS,
    )
    file_list = "\n".join(str(path) for path in corrupted_files[:10])
    payload = {
        "level": "critical",
        "title": "Bit-rot corruption detected",
        "message": f"Detected {len(corrupted_files)} corrupted file(s) / 腐败文件\n{file_list}",
        "source": "data_integrity",
    }
    post_public_webhook(
        alert_webhook,
        payload,
        timeout=timeout_seconds,
        logger=logger,
        context="data_integrity",
    )


def scan_and_verify_directory(target_dir: str) -> None:
    if not check_system_load_safe():
        return

    target = Path(target_dir)
    if not target.exists() or not target.is_dir():
        logger.error("bit_rot_invalid_directory: path=%s", target)
        return

    corrupted_files = _scan_directory_against_baseline(target)
    if not corrupted_files:
        return

    logger.critical("bit_rot_scan_detected_corruption: count=%s", len(corrupted_files))
    _send_corruption_alert(corrupted_files)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info("starting_bit_rot_probe")
    init_baseline_db()
    sample_dir = Path(__file__).parent.parent / "tests"
    if sample_dir.exists():
        scan_and_verify_directory(str(sample_dir))
