"""Topology sentinel helpers: constants, Docker API, MountPoint, logging setup.

Extracted from topology_sentinel.py for maintainability.
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import shutil
import subprocess
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from backend.platform.logging.structured import get_logger as _get_logger


class HWState:
    ONLINE: str = "online"
    OFFLINE: str = "offline"
    PENDING: str = "pending"
    UNKNOWN: str = "unknown"


REDIS_CHANNEL_EVENTS = "hardware:events"
REDIS_CHANNEL_MELTDOWN = "routing:meltdown"
REDIS_KEY_GPU = "hw:gpu"
DEFAULT_PENDING_TTL = 20
DISK_CRITICAL_THRESHOLD = 95.0


def _load_container_map() -> dict[str, str]:
    raw = os.getenv("MOUNT_CONTAINER_MAP", "{}")
    try:
        return json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return {}


CONTAINER_MAP: dict[str, str] = _load_container_map()


def setup_logging(request_id: str | None = None) -> logging.LoggerAdapter:
    base_logger = logging.getLogger("topology-sentinel")
    base_logger.handlers.clear()
    return _get_logger("topology-sentinel", request_id)


_docker_host_raw: str = os.getenv("DOCKER_HOST", "unix:///var/run/docker.sock")
if _docker_host_raw == "tcp://docker-proxy:2375":
    _sentinel_init_logger = logging.getLogger("topology-sentinel")
    _sentinel_init_logger.warning(
        "DOCKER_HOST is set to unauthenticated TCP endpoint %s. "
        "Consider using unix:///var/run/docker.sock or TLS-protected endpoint.",
        _docker_host_raw,
    )
_parsed = urlparse(_docker_host_raw)
DOCKER_API_HOST: str = _parsed.hostname or "docker-proxy"
DOCKER_API_PORT: int = _parsed.port or 2375

logger: logging.LoggerAdapter | None = None


def set_logger(lg: logging.LoggerAdapter | None) -> None:
    global logger
    logger = lg


def _docker_api_get(path: str) -> tuple[int, Any]:
    conn: http.client.HTTPConnection | None = None
    try:
        conn = http.client.HTTPConnection(DOCKER_API_HOST, DOCKER_API_PORT, timeout=5)
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read().decode("utf-8", errors="replace")
        if resp.status == 200:
            return resp.status, json.loads(body) if body else {}
        return resp.status, body
    except (OSError, json.JSONDecodeError) as exc:
        if logger:
            logger.error("Docker API GET failed (%s): %s", path, exc)
        return -1, str(exc)
    finally:
        if conn is not None:
            conn.close()


def _docker_api_post(path: str, timeout: int = 15) -> tuple[int, str]:
    conn: http.client.HTTPConnection | None = None
    try:
        conn = http.client.HTTPConnection(DOCKER_API_HOST, DOCKER_API_PORT, timeout=timeout)
        conn.request("POST", path, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        body = resp.read().decode("utf-8", errors="replace")
        return resp.status, body
    except OSError as exc:
        if logger:
            logger.error("Docker API POST failed (%s, timeout=%ss): %s", path, timeout, exc)
        return -1, str(exc)
    finally:
        if conn is not None:
            conn.close()


def systemctl_action(action: str, unit: str, timeout: int = 30) -> tuple[bool, str]:
    if not shutil.which("systemctl"):
        msg = "systemctl unavailable; skipping host service management"
        if logger:
            logger.warning("%s", msg)
        return False, msg
    try:
        result = subprocess.run(
            ["systemctl", action, unit],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = (result.stdout or result.stderr or "").strip()
        if result.returncode == 0:
            if logger:
                logger.debug("systemctl %s %s: OK", action, unit)
            return True, output
        if logger:
            logger.warning("systemctl %s %s failed (rc=%d): %s", action, unit, result.returncode, output)
        return False, output
    except subprocess.TimeoutExpired:
        msg = f"systemctl {action} {unit} timed out ({timeout}s)"
        if logger:
            logger.warning("%s", msg)
        return False, msg
    except (OSError, FileNotFoundError) as exc:
        msg = f"systemctl invocation failed: {exc}"
        if logger:
            logger.warning("%s", msg)
        return False, msg


class MountPoint:
    """Single mount-point configuration with cached recent probe results."""

    def __init__(
        self,
        path: str,
        expected_uuid: str | None = None,
        min_space_gb: int = 1,
    ) -> None:
        self.path = Path(path)
        self.expected_uuid = expected_uuid
        self.min_space_bytes = min_space_gb * (1024**3)
        self.state_cache: deque[bool] = deque(maxlen=3)
        self.pending_lock_key = f"lock:{path}"

    def check_exists(self) -> bool:
        try:
            return self.path.exists()
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
            if logger:
                logger.warning("check_exists failed for %s: %s", self.path, exc)
            return False

    def get_uuid(self) -> str | None:
        path_str = str(self.path.resolve())
        try:
            result = subprocess.run(
                ["findmnt", "-n", "-o", "SOURCE", "--target", path_str],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0 or not (result.stdout and result.stdout.strip()):
                return None
            device = result.stdout.strip()
            if not device or device == "rootfs":
                return None

            device_str = str(device)
            result = subprocess.run(
                ["blkid", "-s", "UUID", "-o", "value", device_str],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0 or not (result.stdout and result.stdout.strip()):
                return None
            return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            if logger:
                logger.debug("get_uuid failed for %s: %s", self.path, exc)
            return None

    def get_free_space(self) -> int:
        try:
            return shutil.disk_usage(self.path).free
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
            if logger:
                logger.warning("get_free_space failed for %s: %s", self.path, exc)
            return 0

    def verify_full(self) -> tuple[bool, str]:
        if not self.check_exists():
            return False, "path not exists"
        if self.expected_uuid is not None and self.expected_uuid != "":
            actual = self.get_uuid()
            if actual != self.expected_uuid:
                return False, f"UUID mismatch (expected {self.expected_uuid}, got {actual})"
        free = self.get_free_space()
        if free < self.min_space_bytes:
            return False, f"insufficient space: {free} < {self.min_space_bytes}"
        return True, "ok"
