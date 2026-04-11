from __future__ import annotations

import asyncio
import logging
import os

from backend.runtime.execution.attempt_expiration_service import expire_stale_attempts

logger = logging.getLogger(__name__)

_CYCLE_SECONDS = max(1, int(os.getenv("ATTEMPT_EXPIRATION_CYCLE_SECONDS", "15")))
_STARTUP_DELAY_SECONDS = max(0, int(os.getenv("ATTEMPT_EXPIRATION_STARTUP_DELAY_SECONDS", "10")))
_BATCH_SIZE = max(1, int(os.getenv("ATTEMPT_EXPIRATION_BATCH_SIZE", "100")))
_MAX_BACKOFF_SECONDS = 300
_BASE_BACKOFF_SECONDS = 5


def _phoenix_backoff(restart_count: int) -> float:
    return float(min(_BASE_BACKOFF_SECONDS * (2 ** max(restart_count - 1, 0)), _MAX_BACKOFF_SECONDS))


async def attempt_expiration_worker() -> None:
    await asyncio.sleep(_STARTUP_DELAY_SECONDS)
    restart_count = 0
    while True:
        try:
            from backend.db import _async_session_factory

            if _async_session_factory is None:
                logger.warning("attempt_expiration_worker: DB not configured, sleeping")
                await asyncio.sleep(_CYCLE_SECONDS)
                continue

            async with _async_session_factory() as session:
                try:
                    sweep = await expire_stale_attempts(session, limit=_BATCH_SIZE)
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

            if sweep.requeued:
                logger.info(
                    "attempt_expiration_worker: requeued %d stale leases (%d missing attempt rows)",
                    sweep.requeued,
                    sweep.repaired_without_attempt,
                )
            restart_count = 0
            await asyncio.sleep(_CYCLE_SECONDS)
        except asyncio.CancelledError:
            logger.info("attempt_expiration_worker: received CancelledError, exiting")
            return
        except Exception:
            restart_count += 1
            backoff = _phoenix_backoff(restart_count)
            logger.exception(
                "attempt_expiration_worker: unexpected crash (restart #%d), retrying in %.0fs",
                restart_count,
                backoff,
            )
            await asyncio.sleep(backoff)
