from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from collections.abc import Callable, Coroutine

from backend.api.deps import get_settings
from backend.background_tasks import bitrot_worker, data_retention_worker, health_probe_worker
from backend.platform.redis.client import RedisClient
from backend.platform.redis.runtime import connect_redis_with_retry
from backend.workers.attempt_expiration_worker import attempt_expiration_worker

logger = logging.getLogger("zen70.control-worker")

WorkerFactory = Callable[[RedisClient | None], Coroutine[object, object, None]]


def _worker_factories(worker: str) -> dict[str, WorkerFactory]:
    selected = worker.strip().lower()
    factories: dict[str, WorkerFactory] = {
        "attempt-expiration": lambda _redis: attempt_expiration_worker(),
        "bitrot": lambda _redis: bitrot_worker(),
        "health-probe": lambda redis_client: health_probe_worker(app_redis=redis_client),
        "data-retention": lambda _redis: data_retention_worker(),
    }
    if selected == "all":
        return factories
    if selected in factories:
        return {selected: factories[selected]}
    msg = f"Unsupported worker selection: {worker}"
    raise ValueError(msg)


async def run(worker: str) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [CONTROL-WORKER] %(message)s")
    factories = _worker_factories(worker)
    redis_client = None
    if "health-probe" in factories:
        redis_client = await connect_redis_with_retry(get_settings(), logger=logger)
        if redis_client is None:
            logger.warning("Redis unavailable; health-probe worker will run without event publication")

    shutdown_event = asyncio.Event()
    original_sigterm = signal.getsignal(signal.SIGTERM)
    original_sigint = signal.getsignal(signal.SIGINT)

    def _signal_handler(signum: int, frame: object) -> None:
        del frame
        logger.info("Signal %s received, stopping control-plane worker", signum)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    tasks: dict[str, asyncio.Task[None]] = {
        name: asyncio.create_task(factory(redis_client), name=f"control-worker:{name}") for name, factory in factories.items()
    }
    shutdown_task = asyncio.create_task(shutdown_event.wait(), name="control-worker:shutdown")

    try:
        done, _ = await asyncio.wait(
            [shutdown_task, *tasks.values()],
            return_when=asyncio.FIRST_COMPLETED,
        )
        if shutdown_task not in done:
            failed = next(task for task in done if task is not shutdown_task)
            exc = failed.exception()
            if exc is not None:
                raise exc
            raise RuntimeError(f"control-plane worker exited unexpectedly: {failed.get_name()}")
    finally:
        shutdown_task.cancel()
        for task in tasks.values():
            task.cancel()
        for task in tasks.values():
            try:
                await task
            except asyncio.CancelledError:
                pass
        signal.signal(signal.SIGTERM, original_sigterm)
        signal.signal(signal.SIGINT, original_sigint)
        if redis_client is not None:
            await redis_client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run ZEN70 control-plane workers outside the API process.")
    parser.add_argument(
        "--worker",
        default="all",
        choices=("all", "attempt-expiration", "bitrot", "health-probe", "data-retention"),
        help="Which control-plane worker set to run.",
    )
    args = parser.parse_args()
    asyncio.run(run(args.worker))


if __name__ == "__main__":
    main()
