"""Background workers for retention, bit-rot scanning, and health probes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import time
from pathlib import Path

import httpx

from backend.kernel.contracts.events_schema import build_switch_event
from backend.platform.redis.client import RedisClient
from backend.platform.redis.constants import CHANNEL_SWITCH_EVENTS
from backend.shared_state import service_liveness_fails, service_readiness

logger = logging.getLogger(__name__)

RETENTION_CYCLE_SECONDS = int(os.getenv("RETENTION_CYCLE_SECONDS", "86400"))
_PHOENIX_MAX_BACKOFF_S = 300
_PHOENIX_BASE_BACKOFF_S = 5

BITROT_DB_PATH = Path("/app/data/bitrot.db") if Path("/app").exists() else Path("bitrot.db")
_bitrot_dirs_raw = os.getenv("BITROT_SCAN_DIRS", "")
BITROT_SCAN_DIRS = [path.strip() for path in _bitrot_dirs_raw.split(",") if path.strip()]

_microservices_health_urls: dict[str, str] = {}
try:
    urls_raw = os.getenv("MICROSERVICE_HEALTH_URLS", "{}")
    if urls_raw:
        _microservices_health_urls = json.loads(urls_raw)
except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
    logger.debug("MICROSERVICE_HEALTH_URLS parse failed: %s", exc)


def _phoenix_backoff(restart_count: int) -> float:
    return float(min(_PHOENIX_BASE_BACKOFF_S * (2 ** max(restart_count - 1, 0)), _PHOENIX_MAX_BACKOFF_S))


async def data_retention_worker() -> None:
    await asyncio.sleep(120)
    restart_count = 0
    while True:
        try:
            from backend.control_plane.admin.data_retention import run_retention_cycle
            from backend.db import _async_session_factory

            if _async_session_factory is None:
                logger.warning("data_retention_worker: DB not configured, sleeping")
                await asyncio.sleep(RETENTION_CYCLE_SECONDS)
                continue

            async with _async_session_factory() as session:
                result = await run_retention_cycle(session)
                total = sum(result.values())
                if total:
                    logger.info("data_retention_worker: cycle complete %s", result)
                else:
                    logger.debug("data_retention_worker: no records to purge")

            restart_count = 0
            await asyncio.sleep(RETENTION_CYCLE_SECONDS)
        except asyncio.CancelledError:
            logger.info("data_retention_worker: received CancelledError, exiting")
            return
        except Exception:
            restart_count += 1
            backoff = _phoenix_backoff(restart_count)
            logger.exception(
                "data_retention_worker: unexpected crash (restart #%d), retrying in %.0fs",
                restart_count,
                backoff,
            )
            await asyncio.sleep(backoff)


def _init_bitrot_db() -> None:
    conn = sqlite3.connect(BITROT_DB_PATH)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS file_hashes (
                filepath TEXT PRIMARY KEY,
                sha256 TEXT NOT NULL,
                last_checked REAL NOT NULL
            )
            """)
        conn.commit()
    finally:
        conn.close()


def _scan_and_hash_file(filepath: Path, db_path: Path) -> str | None:
    try:
        if filepath.is_symlink():
            return None

        sha256_hash = hashlib.sha256()
        with filepath.open("rb") as handle:
            for block in iter(lambda: handle.read(4096), b""):
                sha256_hash.update(block)
        file_hash = sha256_hash.hexdigest()
        now = time.time()

        conn = sqlite3.connect(db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT sha256 FROM file_hashes WHERE filepath = ?",
                (str(filepath),),
            )
            row = cursor.fetchone()

            if row is None:
                cursor.execute(
                    "INSERT INTO file_hashes (filepath, sha256, last_checked) VALUES (?, ?, ?)",
                    (str(filepath), file_hash, now),
                )
            elif row[0] != file_hash:
                return "重大警告 critical_bitrot_detected: " f"file={filepath} baseline_hash={row[0]} current_hash={file_hash}"
            else:
                cursor.execute(
                    "UPDATE file_hashes SET last_checked = ? WHERE filepath = ?",
                    (now, str(filepath)),
                )
            conn.commit()
        finally:
            conn.close()
        return None
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
        return f"bitrot scan failed for {filepath}: {exc}"


async def bitrot_worker() -> None:
    restart_count = 0
    while True:
        try:
            await asyncio.sleep(60)
            await asyncio.to_thread(_init_bitrot_db)

            while True:
                for directory in BITROT_SCAN_DIRS:
                    dir_path = Path(directory)
                    if not dir_path.exists():
                        continue
                    for filepath in dir_path.rglob("*"):
                        if not filepath.is_file() or filepath.is_symlink():
                            continue
                        result = await asyncio.to_thread(_scan_and_hash_file, filepath, BITROT_DB_PATH)
                        if result is not None:
                            if result.startswith("critical_bitrot_detected"):
                                logger.error(result)
                            else:
                                logger.debug(result)
                        await asyncio.sleep(0.5)

                await asyncio.sleep(86400)
        except asyncio.CancelledError:
            logger.info("bitrot_worker: received CancelledError, exiting")
            return
        except Exception:
            restart_count += 1
            backoff = _phoenix_backoff(restart_count)
            logger.exception(
                "bitrot_worker: unexpected crash (restart #%d), retrying in %.0fs",
                restart_count,
                backoff,
            )
            await asyncio.sleep(backoff)


async def health_probe_worker(app_redis: RedisClient | None = None) -> None:
    await asyncio.sleep(5)
    restart_count = 0
    while True:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                while True:
                    for service_name, health_url in _microservices_health_urls.items():
                        is_ok = False
                        try:
                            response = await client.get(health_url)
                            if response.status_code == 200:
                                is_ok = True
                        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
                            logger.debug("health probe request failed for %s: %s", service_name, exc)

                        if is_ok:
                            service_readiness[service_name] = True
                            service_liveness_fails[service_name] = 0
                        else:
                            service_readiness[service_name] = False
                            service_liveness_fails[service_name] = service_liveness_fails.get(service_name, 0) + 1

                            if service_liveness_fails[service_name] >= 3:
                                logger.error(
                                    "Liveness Probe failed 3 times for %s. Emitting kill signal.",
                                    service_name,
                                )
                                if app_redis is not None:
                                    try:
                                        event = build_switch_event(
                                            service_name,
                                            "RESTART",
                                            reason="liveness_failed_3_times",
                                            updated_by="health_probe",
                                        )
                                        await app_redis.pubsub.publish(CHANNEL_SWITCH_EVENTS, json.dumps(event))
                                    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
                                        logger.warning("publish restart event failed for %s: %s", service_name, exc)
                                service_liveness_fails[service_name] = 0

                    restart_count = 0
                    await asyncio.sleep(10)
        except asyncio.CancelledError:
            logger.info("health_probe_worker: received CancelledError, exiting")
            return
        except Exception:
            restart_count += 1
            backoff = _phoenix_backoff(restart_count)
            logger.exception(
                "health_probe_worker: unexpected crash (restart #%d), retrying in %.0fs",
                restart_count,
                backoff,
            )
            await asyncio.sleep(backoff)
