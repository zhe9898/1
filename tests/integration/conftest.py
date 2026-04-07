"""
集成测试公共配置与 fixture。

环境变量：BASE_URL（网关）、REDIS_URL 或 REDIS_HOST+REDIS_PORT、可选 CONTAINER_NAME。
对 localhost 请求禁用代理，避免 CI/本机代理干扰。
"""

from __future__ import annotations

import os

# 对 localhost 禁用代理
if "localhost" in os.getenv("BASE_URL", "http://localhost:8000") or "127.0.0.1" in os.getenv("BASE_URL", ""):
    os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,::1")
import time
from typing import Generator

import pytest
import requests
import redis

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_URL = os.getenv("REDIS_URL") or f"redis://{REDIS_HOST}:{REDIS_PORT}/0"
TEST_MOUNT_PATH = os.getenv("TEST_MOUNT_PATH", "/mnt/test")
CONTAINER_NAME = os.getenv("CONTAINER_NAME", "")
PROBE_DEBOUNCE_WAIT = int(os.getenv("PROBE_DEBOUNCE_WAIT", "25"))


def _no_proxy() -> dict | None:
    """对 localhost 返回禁用代理的 dict。"""
    if "localhost" in BASE_URL or "127.0.0.1" in BASE_URL:
        return {"http": None, "https": None}
    return None


def _gateway_ok() -> bool:
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=3, proxies=_no_proxy())
        return r.status_code == 200 and r.json().get("status") in ("healthy", "unhealthy")
    except (OSError, requests.RequestException, ValueError, KeyError):
        return False


def _redis_ok() -> bool:
    try:
        c = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True, socket_connect_timeout=2, socket_timeout=2)
        c.ping()
        c.close()
        return True
    except (OSError, ConnectionError, ValueError, RuntimeError, redis.RedisError):
        return False


# 用于 skipif：import 时评估一次，避免 fixture 在 collection 阶段未执行
GATEWAY_OK = _gateway_ok()
REDIS_OK = _redis_ok()


@pytest.fixture(scope="session")
def gateway_available() -> bool:
    """会话级：网关是否可用。"""
    return GATEWAY_OK


@pytest.fixture(scope="session")
def redis_available() -> bool:
    """会话级：Redis 是否可用。"""
    return REDIS_OK


@pytest.fixture(scope="session")
def redis_client() -> Generator:
    """会话级 Redis 客户端（decode_responses=True）。"""
    try:
        client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True, socket_connect_timeout=2, socket_timeout=2)
        client.ping()
        yield client
        client.close()
    except (OSError, ConnectionError, ValueError, RuntimeError, redis.RedisError):
        yield None


def get_hw_state(redis_client, path: str) -> dict | None:
    """从 Redis 读取 hw:<path> 哈希；redis_client 为 None 时返回 None。"""
    if redis_client is None:
        return None
    try:
        key = f"hw:{path}"
        data = redis_client.hgetall(key)
        return data or None
    except (OSError, ConnectionError, ValueError, RuntimeError, redis.RedisError):
        return None


def container_status(container_name: str) -> str:
    """通过 docker inspect 获取容器状态；无 docker 或失败返回空串。"""
    if not container_name:
        return ""
    import subprocess
    try:
        out = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", container_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0:
            return (out.stdout or "").strip()
        return ""
    except (OSError, subprocess.SubprocessError):
        return ""
