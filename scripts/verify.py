#!/usr/bin/env python3
"""Infrastructure verification helpers for the host-first kernel runtime."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import time
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

try:
    import docker
except ImportError:  # pragma: no cover - optional dependency in some environments
    docker = None

PROJECT_LABEL = "com.docker.compose.project=zen70"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAX_WAIT = 30
CHECK_INTERVAL = 3
FATAL_PATTERN = re.compile(r"(FATAL|Permission denied|panic|address already in use)", re.IGNORECASE)
DEFAULT_INFRASTRUCTURE_CONTAINERS = {
    "zen70-caddy",
    "zen70-nats",
    "zen70-postgres",
    "zen70-redis",
}


def _load_core_containers() -> set[str]:
    """Load infrastructure container names from the rendered host-first manifest."""
    manifest_path = PROJECT_ROOT / "render-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        manifest = None

    if isinstance(manifest, dict):
        services = manifest.get("infrastructure_containers_rendered") or []
        normalized = {f"zen70-{svc}" for svc in services if isinstance(svc, str) and svc.strip()}
        if normalized:
            return normalized

    try:
        sys_cfg = yaml.safe_load((PROJECT_ROOT / "system.yaml").read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        sys_cfg = None

    if isinstance(sys_cfg, dict):
        enabled_containers = {
            svc.get("container_name", f"zen70-{name}")
            for name, svc in (sys_cfg.get("services") or {}).items()
            if isinstance(svc, dict) and svc.get("enabled") is not False and svc.get("runtime") != "host"
        }
        if enabled_containers:
            return enabled_containers

    return set(DEFAULT_INFRASTRUCTURE_CONTAINERS)


DEFAULT_CORE_CONTAINERS = _load_core_containers()


def _run_ci_step(command: list[str], step_name: str) -> bool:
    """Run one CI step and log decoded stdout and stderr."""
    logger.info("CI step start: %s", step_name)
    try:
        result = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            check=False,
            capture_output=True,
            text=False,
        )
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
        logger.error("CI step error (%s): %s", step_name, exc)
        return False

    stdout_text = (result.stdout or b"").decode("utf-8", errors="ignore").strip()
    stderr_text = (result.stderr or b"").decode("utf-8", errors="ignore").strip()
    if stdout_text:
        logger.info("[%s stdout]\n%s", step_name, stdout_text)
    if stderr_text:
        logger.warning("[%s stderr]\n%s", step_name, stderr_text)
    if result.returncode != 0:
        logger.error("CI step failed: %s (exit=%s)", step_name, result.returncode)
        return False
    logger.info("CI step passed: %s", step_name)
    return True


def run_ci_pipeline(exit_on_fail: bool = True) -> bool:
    """Run the data-integrity CI chain against the current workspace runtime."""
    steps = [
        (
            [
                sys.executable,
                "-m",
                "flake8",
                "--max-complexity=15",
                "--max-line-length=160",
                "backend/sentinel/data_integrity.py",
                "backend/tests/unit/test_data_integrity.py",
            ],
            "flake8:data_integrity",
        ),
        ([sys.executable, "-m", "pytest", "backend/tests/unit/test_data_integrity.py", "-q"], "pytest:data_integrity"),
        ([sys.executable, "scripts/verify_data_integrity_e2e.py"], "e2e:data_integrity"),
    ]

    all_passed = True
    for command, step_name in steps:
        if _run_ci_step(command, step_name):
            continue
        all_passed = False
        if exit_on_fail:
            sys.exit(1)
        break
    return all_passed


def get_docker_client(retries: int = 3, delay: int = 2):  # type: ignore
    """Create a Docker client with bounded retries."""
    if docker is None:
        logger.error("docker package is not installed; run `pip install docker` first")
        return None

    docker_error_types = (OSError, ValueError, KeyError, RuntimeError, TypeError)
    if hasattr(docker, "errors") and hasattr(docker.errors, "DockerException"):
        docker_error_types = docker_error_types + (docker.errors.DockerException,)

    for attempt in range(retries):
        try:
            return docker.from_env()
        except docker_error_types as exc:
            logger.warning("Docker connection failed (%s/%s): %s", attempt + 1, retries, exc)
            if attempt < retries - 1:
                time.sleep(delay)
    return None


def wait_for_containers_ready(
    client,
    project_label: str,
    timeout: int = MAX_WAIT,
    allow_degraded: set[str] | None = None,
    required_healthy: set[str] | None = None,
) -> bool:
    """Wait until all project containers are healthy or running."""
    allowed = allow_degraded or set()
    must_be_healthy = required_healthy or set()
    start_time = time.time()

    while time.time() - start_time < timeout:
        containers = client.containers.list(all=True, filters={"label": project_label})
        if not containers:
            logger.error("No project containers found")
            return False

        all_ready = True
        for container in containers:
            container.reload()
            status = container.status
            health = container.attrs.get("State", {}).get("Health", {}).get("Status")
            ready = health == "healthy" if health else status == "running"
            state_desc = f"health={health}" if health else f"status={status}"

            if ready:
                continue
            if container.name in allowed and container.name not in must_be_healthy:
                logger.warning("Container %s is degraded but allowed (%s)", container.name, state_desc)
                continue
            all_ready = False
            logger.info("Container %s is not ready (%s)", container.name, state_desc)

        for critical_name in must_be_healthy:
            if any(container.name == critical_name for container in containers):
                continue
            logger.error("Required container missing: %s", critical_name)
            return False

        if all_ready:
            return True
        time.sleep(CHECK_INTERVAL)

    logger.error("Timed out waiting for containers to become ready (%ss)", timeout)
    return False


def scan_container_logs(container) -> list[str]:
    """Return suspicious log lines from the last 50 lines."""
    try:
        logs = container.logs(tail=50).decode("utf-8", errors="ignore")
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
        logger.warning("Failed to read container logs for %s: %s", container.name, exc)
        return []
    return [line.strip() for line in logs.split("\n") if FATAL_PATTERN.search(line)]


def verify_infrastructure(
    exit_on_fail: bool = True,
    allow_degraded: set[str] | None = None,
    required_healthy: set[str] | None = None,
) -> bool:
    """Verify project containers are up, healthy, and free of fatal logs."""
    logger.info("Starting infrastructure verification")

    client = get_docker_client()
    if not client:
        logger.error("Unable to connect to Docker daemon")
        if exit_on_fail:
            sys.exit(1)
        return False

    if not wait_for_containers_ready(
        client,
        PROJECT_LABEL,
        allow_degraded=allow_degraded,
        required_healthy=required_healthy,
    ):
        if exit_on_fail:
            sys.exit(1)
        return False

    containers = client.containers.list(all=True, filters={"label": PROJECT_LABEL})
    all_passed = True

    for container in containers:
        status = container.status
        health = container.attrs.get("State", {}).get("Health", {}).get("Status")
        health_info = f", health={health}" if health else ""
        logger.info("Container %s: status=%s%s", container.name, status, health_info)

        bad_lines = scan_container_logs(container)
        if not bad_lines or container.name in (allow_degraded or set()):
            continue

        logger.warning("Container %s has suspicious log lines:", container.name)
        for line in bad_lines[:3]:
            logger.warning("    -> %s", line)
        all_passed = False

    if all_passed:
        logger.info("Infrastructure verification passed")
        return True

    logger.error("Infrastructure verification failed")
    if exit_on_fail:
        sys.exit(1)
    return False


def _parse_allow_degraded(argv: list[str]) -> set[str]:
    raw = next((arg for arg in argv if arg.startswith("--allow-degraded=")), "")
    value = raw.replace("--allow-degraded=", "")
    return {item.strip() for item in value.split(",") if item.strip()}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    allow_degraded = _parse_allow_degraded(sys.argv)
    if "--ci" in sys.argv:
        run_ci_pipeline(exit_on_fail=True)
    else:
        verify_infrastructure(
            exit_on_fail=True,
            allow_degraded=allow_degraded,
            required_healthy=DEFAULT_CORE_CONTAINERS,
        )
