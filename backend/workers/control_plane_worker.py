from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
from collections.abc import Callable, Coroutine

from backend.background_tasks import bitrot_worker, data_retention_worker, health_probe_worker
from backend.control_plane.adapters.deps import get_settings
from backend.platform.events.runtime import connect_event_bus_with_retry, resolve_event_bus_backend, set_runtime_event_bus
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
    settings = get_settings()
    redis_client = None
    event_bus = None
    if "health-probe" in factories:
        redis_client = await connect_redis_with_retry(settings, logger=logger)
        if redis_client is None:
            logger.warning("Redis unavailable; health-probe worker will run without event publication")
        event_bus = await connect_event_bus_with_retry(settings, redis=redis_client, logger=logger)
        set_runtime_event_bus(event_bus)
        if event_bus is None and resolve_event_bus_backend(settings) == "nats":
            raise RuntimeError("control-plane worker requires NATS event bus but it is unavailable")

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
            with contextlib.suppress(asyncio.CancelledError):
                await task
        signal.signal(signal.SIGTERM, original_sigterm)
        signal.signal(signal.SIGINT, original_sigint)
        if event_bus is not None:
            await event_bus.close()
        set_runtime_event_bus(None)
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
