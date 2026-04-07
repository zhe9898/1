from __future__ import annotations

import asyncio
import logging
import signal
import sys
from pathlib import Path

logger = logging.getLogger("zen70.control-plane-supervisor")


def _child_commands(project_root: Path) -> dict[str, list[str]]:
    backend_root = project_root / "backend"
    return {
        "topology-sentinel": [sys.executable, str(backend_root / "sentinel" / "topology_sentinel.py")],
        "control-worker": [sys.executable, "-m", "backend.workers.control_plane_worker", "--worker", "all"],
        "routing-operator": [sys.executable, str(backend_root / "sentinel" / "routing_operator.py")],
    }


async def _terminate_children(children: dict[str, asyncio.subprocess.Process]) -> None:
    for process in children.values():
        if process.returncode is None:
            process.terminate()

    for name, process in children.items():
        try:
            await asyncio.wait_for(process.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("Child %s did not exit after SIGTERM; killing", name)
            process.kill()
            await process.wait()


async def run(project_root: Path | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [CONTROL-SUPERVISOR] %(message)s")
    root = project_root or Path(__file__).resolve().parent.parent.parent
    commands = _child_commands(root)
    shutdown_event = asyncio.Event()
    original_sigterm = signal.getsignal(signal.SIGTERM)
    original_sigint = signal.getsignal(signal.SIGINT)

    def _signal_handler(signum: int, frame: object) -> None:
        del frame
        logger.info("Signal %s received, stopping control-plane supervisor", signum)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    children: dict[str, asyncio.subprocess.Process] = {}
    wait_tasks: dict[str, asyncio.Task[int]] = {}
    shutdown_task = asyncio.create_task(shutdown_event.wait(), name="control-supervisor:shutdown")

    try:
        for name, command in commands.items():
            logger.info("Starting child %s: %s", name, " ".join(command))
            process = await asyncio.create_subprocess_exec(*command, cwd=str(root))
            children[name] = process
            wait_tasks[name] = asyncio.create_task(process.wait(), name=f"control-supervisor:{name}")

        done, _ = await asyncio.wait(
            [shutdown_task, *wait_tasks.values()],
            return_when=asyncio.FIRST_COMPLETED,
        )

        if shutdown_task not in done:
            failed = next(task for task in done if task is not shutdown_task)
            failed_name = next(name for name, task in wait_tasks.items() if task is failed)
            code = failed.result()
            raise RuntimeError(f"control-plane child exited unexpectedly: {failed_name} (code={code})")
    finally:
        shutdown_task.cancel()
        await _terminate_children(children)
        signal.signal(signal.SIGTERM, original_sigterm)
        signal.signal(signal.SIGINT, original_sigint)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
