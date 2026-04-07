#!/usr/bin/env python3
"""
ZEN70 运维自愈看门狗 (Runtime Self-Healing Watchdog)。

法典 3.2 / 2.5 / ADR 0006 强制：
- 持续巡检所有容器健康状态（每 60s 一轮）
- 通过 docker-proxy (TCP) 管理容器（ADR 0006: 严禁直连 docker.sock）
- 发现异常自动修复（死亡容器重启、不健康容器 recreate）
- 磁盘 ≥95% 触发紧急 GC（法典 §3.3）
- 结构化 JSON 日志输出（法典 §2.5）
- SIGTERM 优雅停机（法典 §2.5）
- 防重启风暴：同一容器每小时最多重启 5 次

运行方式:
    # docker-compose 服务（推荐）
    见 docker-compose.yml 中 watchdog 服务定义

    # 独立运行
    DOCKER_HOST=tcp://docker-proxy:2375 python scripts/watchdog.py
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import shutil
import signal
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

# ── 常量 ──────────────────────────────────────────────────────
POLL_INTERVAL_SEC: int = int(os.getenv("WATCHDOG_INTERVAL", "60"))
"""巡检间隔（秒），可由环境变量覆盖。"""

DISK_CRITICAL_PERCENT: int = 95
"""法典 §3.3：系统盘 95% 触发紧急 GC。"""

DISK_WARN_PERCENT: int = 85
"""磁盘告警阈值。"""

MAX_RESTART_PER_HOUR: int = 5
"""同一容器每小时最大自动重启次数（防重启风暴）。"""

CONTAINER_PREFIX: str = "zen70-"
"""容器名前缀过滤。"""

SELF_CONTAINER: str = "zen70-watchdog"
"""自身容器名，巡检时跳过自己。"""

# ── Docker Host 解析（ADR 0006: 通过 docker-proxy TCP）────────
_docker_host_raw: str = os.getenv("DOCKER_HOST", "tcp://docker-proxy:2375")
_parsed = urlparse(_docker_host_raw)
DOCKER_API_HOST: str = _parsed.hostname or "docker-proxy"
DOCKER_API_PORT: int = _parsed.port or 2375

# ── 路径 ──────────────────────────────────────────────────────
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

# ── 日志（结构化 JSON，法典 §2.5）──────────────────────────────


class _JsonFormatter(logging.Formatter):
    """法典 §2.5: 结构化 JSON 日志格式器。"""

    def format(self, record: logging.LogRecord) -> str:
        """将日志记录格式化为 JSON 字符串。"""
        entry: dict[str, Any] = {
            "timestamp": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "caller": f"{record.module}.{record.funcName}",
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            entry["exception"] = str(record.exc_info[1])
        return json.dumps(entry, ensure_ascii=False)


_handler = logging.StreamHandler(sys.stderr)
_handler.setFormatter(_JsonFormatter())
logger = logging.getLogger("watchdog")
logger.setLevel(logging.INFO)
logger.addHandler(_handler)
logger.propagate = False


# ── Docker Engine HTTP API (TCP via docker-proxy) ──────────────


def _docker_api_get(path: str) -> tuple[int, Any]:
    """
    向 Docker Engine API 发送 GET 请求（通过 docker-proxy TCP）。

    Args:
        path: API 路径，如 /containers/json。

    Returns:
        (HTTP 状态码, 解析后的 JSON 响应) 元组。
    """
    conn: http.client.HTTPConnection | None = None
    try:
        conn = http.client.HTTPConnection(DOCKER_API_HOST, DOCKER_API_PORT, timeout=10)
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read().decode("utf-8", errors="replace")
        if resp.status == 200:
            return resp.status, json.loads(body) if body else {}
        return resp.status, body
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Docker API GET 失败 (%s): %s", path, exc)
        return -1, str(exc)
    finally:
        if conn is not None:
            conn.close()


def _docker_api_post(path: str) -> tuple[int, str]:
    """
    向 Docker Engine API 发送 POST 请求（通过 docker-proxy TCP）。

    Args:
        path: API 路径，如 /containers/{id}/restart。

    Returns:
        (HTTP 状态码, 响应体) 元组。
    """
    conn: http.client.HTTPConnection | None = None
    try:
        conn = http.client.HTTPConnection(DOCKER_API_HOST, DOCKER_API_PORT, timeout=30)
        conn.request("POST", path, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        body = resp.read().decode("utf-8", errors="replace")
        return resp.status, body
    except OSError as exc:
        logger.error("Docker API POST 失败 (%s): %s", path, exc)
        return -1, str(exc)
    finally:
        if conn is not None:
            conn.close()


# ── 重启风暴防护 ──────────────────────────────────────────────
_restart_history: dict[str, list[float]] = {}
"""容器名 → 最近重启时间戳列表。"""


def _can_restart(container_name: str) -> bool:
    """
    检查是否允许重启该容器（防重启风暴）。

    法典 §7: 严禁无限死循环轮询。
    每小时最多重启 MAX_RESTART_PER_HOUR 次。

    Args:
        container_name: Docker 容器名称。

    Returns:
        允许重启返回 True，否则 False。
    """
    now = time.time()
    history = _restart_history.setdefault(container_name, [])
    cutoff = now - 3600
    _restart_history[container_name] = [t for t in history if t > cutoff]
    if len(_restart_history[container_name]) >= MAX_RESTART_PER_HOUR:
        logger.warning(
            "容器 %s 已达每小时重启上限 (%d 次)，停止自动重启（防风暴）",
            container_name,
            MAX_RESTART_PER_HOUR,
        )
        return False
    _restart_history[container_name].append(now)
    return True


# ── 检测器 ────────────────────────────────────────────────────


def detect_unhealthy_containers() -> list[dict[str, str]]:
    """
    通过 Docker Engine API 检测所有 zen70 容器状态。

    Returns:
        异常容器字典列表，每项含 name, state, health, issue, id 字段。
    """
    status_code, data = _docker_api_get("/containers/json?all=true")
    if status_code != 200:
        logger.error("Docker API 容器列表获取失败 (status=%s)", status_code)
        return []

    if not isinstance(data, list):
        logger.error("Docker API 返回非预期类型: %s", type(data).__name__)
        return []

    issues: list[dict[str, str]] = []
    for container in data:
        names = container.get("Names", [])
        name = names[0].lstrip("/") if names else ""
        if not name.startswith(CONTAINER_PREFIX):
            continue
        if name == SELF_CONTAINER:
            continue

        state = (container.get("State") or "").lower()
        status_text = (container.get("Status") or "").lower()
        container_id = container.get("Id", "")[:12]

        if state in ("exited", "dead"):
            issues.append(
                {
                    "name": name,
                    "state": state,
                    "health": "",
                    "issue": "container_dead",
                    "id": container_id,
                }
            )
        elif state == "restarting":
            issues.append(
                {
                    "name": name,
                    "state": state,
                    "health": "",
                    "issue": "container_restart_loop",
                    "id": container_id,
                }
            )
        elif "unhealthy" in status_text:
            issues.append(
                {
                    "name": name,
                    "state": state,
                    "health": "unhealthy",
                    "issue": "container_unhealthy",
                    "id": container_id,
                }
            )

    return issues


def detect_disk_pressure() -> tuple[int, bool]:
    """
    检测系统盘使用率。

    Returns:
        (使用百分比, 是否触发临界阈值) 元组。
    """
    try:
        total, used, _free = shutil.disk_usage(PROJECT_ROOT)
        pct = int(used / total * 100)
        return pct, pct >= DISK_CRITICAL_PERCENT
    except OSError as exc:
        logger.warning("磁盘使用率检测失败: %s", exc)
        return 0, False


# ── 修复器 ────────────────────────────────────────────────────


def fix_container(container_name: str, container_id: str, issue: str) -> bool:
    """
    通过 Docker Engine API 重启异常容器。

    Args:
        container_name: 容器名称。
        container_id: 容器 ID (短)。
        issue: 问题类型。

    Returns:
        修复成功返回 True。
    """
    if not _can_restart(container_name):
        return False

    logger.info("尝试修复容器: %s (issue=%s, id=%s)", container_name, issue, container_id)

    encoded_name = quote(container_name, safe="")
    status_code, body = _docker_api_post(f"/containers/{encoded_name}/restart?t=10")

    if status_code == 204:
        logger.info("容器 %s 已自动重启", container_name)
        return True
    logger.error("容器 %s 重启失败 (status=%s): %s", container_name, status_code, body[:200])
    return False


def fix_disk_pressure() -> bool:
    """
    法典 §3.7: 紧急 GC — 通过 Docker API 清理悬空镜像。

    Returns:
        GC 成功返回 True。
    """
    logger.warning("系统盘使用率 ≥%d%%，触发紧急 GC", DISK_CRITICAL_PERCENT)

    status_code, body = _docker_api_post("/images/prune")
    if status_code == 200:
        reclaimed = 0
        if isinstance(body, str):
            try:
                result = json.loads(body)
                reclaimed = result.get("SpaceReclaimed", 0) // (1024 * 1024)
            except (json.JSONDecodeError, TypeError):
                pass
        logger.info("紧急 GC 完成: 回收 %d MB", reclaimed)
        return True
    logger.error("紧急 GC 失败 (status=%s)", status_code)
    return False


# ── 主巡检循环 ────────────────────────────────────────────────

_shutdown_requested: bool = False


def _sigterm_handler(signum: int, _frame: object) -> None:
    """法典 §2.5：SIGTERM 优雅停机信号处理。"""
    global _shutdown_requested  # noqa: PLW0603
    logger.info("收到信号 %d，准备优雅停机", signum)
    _shutdown_requested = True


def run_patrol_cycle() -> dict[str, int]:
    """
    执行一轮完整巡检：容器健康 + 磁盘压力。

    Returns:
        巡检结果统计字典 (detected, fixed, failed)。
    """
    stats: dict[str, int] = {"detected": 0, "fixed": 0, "failed": 0}

    # ── 1. 容器健康检测 ────────────────────────────────────
    issues = detect_unhealthy_containers()
    stats["detected"] += len(issues)

    for issue_info in issues:
        name = issue_info["name"]
        issue_type = issue_info["issue"]
        container_id = issue_info["id"]
        logger.warning(
            "检测到异常: %s (%s, state=%s)",
            name,
            issue_type,
            issue_info["state"],
        )

        if fix_container(name, container_id, issue_type):
            stats["fixed"] += 1
        else:
            stats["failed"] += 1

    # ── 2. 磁盘使用率检测 ──────────────────────────────────
    disk_pct, is_critical = detect_disk_pressure()
    if is_critical:
        stats["detected"] += 1
        if fix_disk_pressure():
            stats["fixed"] += 1
        else:
            stats["failed"] += 1
    elif disk_pct >= DISK_WARN_PERCENT:
        logger.warning("系统盘使用率 %d%%（告警阈值 %d%%）", disk_pct, DISK_WARN_PERCENT)

    return stats


def _check_docker_proxy_reachable() -> bool:
    """预检：docker-proxy TCP 端点是否可达。"""
    try:
        status_code, _ = _docker_api_get("/version")
        return status_code == 200
    except OSError:
        return False


def main() -> None:
    """看门狗主入口：检查 docker-proxy → 注册信号 → 循环巡检。"""
    signal.signal(signal.SIGTERM, _sigterm_handler)
    signal.signal(signal.SIGINT, _sigterm_handler)

    logger.info(
        "ZEN70 看门狗启动: interval=%ds, disk_critical=%d%%, max_restart/h=%d, api=%s:%d",
        POLL_INTERVAL_SEC,
        DISK_CRITICAL_PERCENT,
        MAX_RESTART_PER_HOUR,
        DOCKER_API_HOST,
        DOCKER_API_PORT,
    )

    # 预检：等待 docker-proxy 就绪（最多 30s）
    logger.info("等待 docker-proxy 就绪...")
    for attempt in range(1, 31):
        if _shutdown_requested:
            return
        if _check_docker_proxy_reachable():
            logger.info("docker-proxy 就绪 (第 %d 次)", attempt)
            break
        time.sleep(1)
    else:
        logger.error("docker-proxy 30s 内未就绪，看门狗将尝试继续运行")

    # 首轮巡检前额外等待（给其他容器启动时间）
    logger.info("首轮巡检将在 10s 后开始")
    for _ in range(10):
        if _shutdown_requested:
            break
        time.sleep(1)

    while not _shutdown_requested:
        try:
            stats = run_patrol_cycle()
            if stats["detected"] > 0:
                logger.info(
                    "巡检完成: 发现=%d, 修复=%d, 失败=%d",
                    stats["detected"],
                    stats["fixed"],
                    stats["failed"],
                )
            else:
                logger.debug("巡检完成: 系统健康")
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
            logger.exception("巡检异常: %s", exc)

        # 可中断的睡眠
        for _ in range(POLL_INTERVAL_SEC):
            if _shutdown_requested:
                break
            time.sleep(1)

    logger.info("看门狗优雅停机完成")


if __name__ == "__main__":
    main()
