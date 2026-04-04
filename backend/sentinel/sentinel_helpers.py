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
import sys
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


# -------------------- 常量 --------------------
class HWState:
    ONLINE: str = "online"
    OFFLINE: str = "offline"
    PENDING: str = "pending"
    UNKNOWN: str = "unknown"


REDIS_CHANNEL_EVENTS = "hardware:events"
REDIS_CHANNEL_MELTDOWN = "routing:meltdown"
REDIS_KEY_GPU = "hw:gpu"
DEFAULT_PENDING_TTL = 20
DISK_CRITICAL_THRESHOLD = 95.0  # 法典 3.3：系统盘 95% 绝对物理熔断


def _load_container_map() -> dict[str, str]:
    """路径解耦：挂载路径→容器名仅来自 .env（由 compiler 从 system.yaml 写入）。"""
    raw = os.getenv("MOUNT_CONTAINER_MAP", "{}")
    try:
        return json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return {}


CONTAINER_MAP: dict[str, str] = _load_container_map()

# -------------------- 日志（复用 backend.core 集中模块） --------------------
try:
    from backend.core.structured_logging import get_logger as _get_logger
except ImportError:
    # 兼容单文件调试，使用标准 logging
    def _get_logger(name: str, req_id: str | None) -> logging.LoggerAdapter:  # type: ignore[misc]
        logger = logging.getLogger(name)
        if not logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
        return logging.LoggerAdapter(logger, extra={})


def setup_logging(request_id: str | None = None) -> logging.LoggerAdapter:
    """配置 JSON 日志并返回带 request_id 的 LoggerAdapter。"""
    base_logger = logging.getLogger("topology-sentinel")
    base_logger.handlers.clear()
    return _get_logger("topology-sentinel", request_id)


# -------------------- Docker Engine HTTP API --------------------
_docker_host_raw: str = os.getenv("DOCKER_HOST", "unix:///var/run/docker.sock")
if _docker_host_raw == "tcp://docker-proxy:2375":
    _sentinel_init_logger = logging.getLogger("topology-sentinel")
    _sentinel_init_logger.warning(
        "DOCKER_HOST is set to unauthenticated TCP endpoint %s. " "Consider using unix:///var/run/docker.sock or TLS-protected endpoint.",
        _docker_host_raw,
    )
_parsed = urlparse(_docker_host_raw)
DOCKER_API_HOST: str = _parsed.hostname or "docker-proxy"
DOCKER_API_PORT: int = _parsed.port or 2375

# Module-level logger reference; set by topology_sentinel.main()
logger: logging.LoggerAdapter | None = None


def set_logger(lg: logging.LoggerAdapter | None) -> None:
    """Allow topology_sentinel to inject its logger into this module."""
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
    """法典 3.1: 支持可变超时的 Docker HTTP API POST。pause 场景使用 3s 超时。"""
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


# -------------------- 挂载点配置 --------------------


class MountPoint:
    """
    单个挂载点配置：路径、期望 UUID、最小剩余空间；维护滑动窗口状态缓存。
    """

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
        """检查挂载路径是否存在。"""
        try:
            return self.path.exists()
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            if logger:
                logger.warning("check_exists failed for %s: %s", self.path, e)
            return False

    def get_uuid(self) -> str | None:
        """法典 3.2：通过 Linux 原生命令 findmnt + blkid 获取挂载点对应设备 UUID。自动降级防腐。"""
        path_str = str(self.path.resolve())
        try:
            # 1) findmnt 取挂载点对应设备（如 /dev/sda1）
            r1 = subprocess.run(
                ["findmnt", "-n", "-o", "SOURCE", "--target", path_str],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if r1.returncode != 0 or not (r1.stdout and r1.stdout.strip()):
                return None
            device = r1.stdout.strip()
            if not device or device == "rootfs":
                return None

            # str() conversion for type checkers
            s_device = str(device)
            # 2) blkid 取该设备 UUID
            r2 = subprocess.run(
                ["blkid", "-s", "UUID", "-o", "value", s_device],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if r2.returncode != 0 or not (r2.stdout and r2.stdout.strip()):
                return None
            return r2.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            if logger:
                logger.debug("get_uuid failed for %s: %s", self.path, e)
            return None

    def get_free_space(self) -> int:
        """返回挂载点可用空间字节数；失败返回 0。"""
        try:
            return shutil.disk_usage(self.path).free
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            if logger:
                logger.warning("get_free_space failed for %s: %s", self.path, e)
            return 0

    def verify_full(self) -> tuple[bool, str]:
        """
        三重交叉核验：路径存在、UUID 匹配（若配置）、最小剩余空间。
        返回 (是否通过, 原因说明)。
        """
        if not self.check_exists():
            return False, "path not exists"
        if self.expected_uuid is not None and self.expected_uuid != "":
            actual = self.get_uuid()
            if actual != self.expected_uuid:
                return (
                    False,
                    f"UUID mismatch (expected {self.expected_uuid}, got {actual})",
                )
        free = self.get_free_space()
        if free < self.min_space_bytes:
            return False, f"insufficient space: {free} < {self.min_space_bytes}"
        return True, "ok"
