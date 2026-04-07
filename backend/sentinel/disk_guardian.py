#!/usr/bin/env python3
"""
ZEN70 磁盘容量守护进程 (Disk Guardian)。

法典 §3.3：系统盘 95% 时触发全局只读大闸，强制 pause 高频组件，防止死锁。
作为 sentinel 的子模块运行，可独立执行或由 topology_sentinel 调用。

逻辑：
  1. 每 60s 检测系统盘使用率
  2. ≥95% → 通过 Redis 发布 disk:critical 事件 + 设置全局只读标志
  3. ≥90% → 通过 Redis 发布 disk:warning 事件
  4. <90% 且之前触发过 → 自动解除只读标志

不直接操作 docker pause —— 遵循 ADR 0006 红线（探针不越权），
通过 Redis switch:events 通知网关层执行容器降级。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import time
from typing import Any

logger = logging.getLogger("disk-guardian")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DISK_CHECK_INTERVAL_SEC = 60
"""磁盘检测周期（秒）。"""

DISK_CRITICAL_THRESHOLD = 95.0
"""触发全局只读大闸的使用率阈值（%）。"""

DISK_WARNING_THRESHOLD = 90.0
"""触发警告的使用率阈值（%）。"""

# Phase 3 修复: 从 constants.py 导入 SSOT 常量，消除硬编码
try:
    from backend.core.constants import (
        CHANNEL_SWITCH_EVENTS,
        KEY_SYSTEM_READONLY_DISK,
    )
except ImportError:
    # 独立运行模式降级
    KEY_SYSTEM_READONLY_DISK = "zen70:disk:readonly"
    CHANNEL_SWITCH_EVENTS = "switch:events"

REDIS_KEY_DISK_READONLY = KEY_SYSTEM_READONLY_DISK
"""Redis 全局只读标志 key（从 constants.py SSOT 导入）。"""

REDIS_CHANNEL_DISK = CHANNEL_SWITCH_EVENTS
"""磁盘事件通过 switch:events 通道发布（网关层订阅并执行降级）。"""


# ---------------------------------------------------------------------------
# 核心逻辑
# ---------------------------------------------------------------------------


def get_system_disk_usage(path: str = "/") -> tuple[float, float, float]:
    """
    获取系统盘磁盘使用情况。

    Returns:
        (total_gb, used_gb, usage_percent)
    """
    try:
        usage = shutil.disk_usage(path)
        total_gb = usage.total / (1024**3)
        used_gb = usage.used / (1024**3)
        usage_percent = (usage.used / usage.total) * 100 if usage.total > 0 else 0.0
        return total_gb, used_gb, usage_percent
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
        logger.error("获取磁盘使用率失败: %s", e)
        return 0.0, 0.0, 0.0


def check_and_act(
    redis_client: Any | None = None,
    check_path: str = "/",
) -> str:
    """
    执行一次磁盘检测并根据阈值决策。

    Returns:
        "critical" | "warning" | "ok"
    """
    total_gb, used_gb, usage_pct = get_system_disk_usage(check_path)

    if usage_pct <= 0:
        logger.warning("无法获取磁盘使用率，跳过本轮检测")
        return "ok"

    if usage_pct >= DISK_CRITICAL_THRESHOLD:
        logger.critical(
            "🚨 系统盘使用率 %.1f%% >= %.0f%% — 触发全局只读大闸！" " (总容量: %.1fGB, 已用: %.1fGB)",
            usage_pct,
            DISK_CRITICAL_THRESHOLD,
            total_gb,
            used_gb,
        )
        if redis_client is not None:
            _publish_disk_event(redis_client, "critical", usage_pct)
            _set_readonly_flag(redis_client, True)
        return "critical"

    if usage_pct >= DISK_WARNING_THRESHOLD:
        logger.warning(
            "⚠️ 系统盘使用率 %.1f%% >= %.0f%% — 预警！" " (总容量: %.1fGB, 已用: %.1fGB)",
            usage_pct,
            DISK_WARNING_THRESHOLD,
            total_gb,
            used_gb,
        )
        if redis_client is not None:
            _publish_disk_event(redis_client, "warning", usage_pct)
        return "warning"

    # 正常：如果之前触发过只读，自动解除
    if redis_client is not None:
        _clear_readonly_if_set(redis_client, usage_pct)

    logger.debug("系统盘使用率 %.1f%% — 正常", usage_pct)
    return "ok"


def _publish_disk_event(
    redis_client: Any,
    level: str,
    usage_pct: float,
) -> None:
    """通过 Redis Pub/Sub 发布磁盘事件到 switch:events 通道。"""

    # Phase 3 链路 5 修复：补充 state + switch 字段，
    # 否则 SwitchEventPayload.from_redis_message() 检查 'state' in data 会静默丢弃。
    payload = json.dumps(
        {
            "state": "OFF" if level == "critical" else "ON",
            "switch": "disk_guardian",
            "name": "disk_guardian",
            "event": "disk_guardian",
            "level": level,
            "reason": f"disk usage {usage_pct:.1f}% — {level}",
            "usage_percent": float(round(usage_pct, 1)),
            "action": "readonly_lockdown" if level == "critical" else "warning_alert",
            "updated_at": str(time.time()),
            "updated_by": "disk_guardian",
        }
    )
    try:
        receiver_count = redis_client.publish(REDIS_CHANNEL_DISK, payload)
        if receiver_count == 0:
            logger.warning(
                "disk:%s 事件已发布到 %s，但当前无任何订阅者 — 事件可能未被处理",
                level,
                REDIS_CHANNEL_DISK,
            )
        else:
            logger.info("已发布 disk:%s 事件到 %s (%d 订阅者)", level, REDIS_CHANNEL_DISK, receiver_count)
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
        logger.error("发布 disk 事件失败: %s", e)


def _set_readonly_flag(redis_client: Any, value: bool) -> None:
    """设置/解除 Redis 全局只读标志。"""
    try:
        if value:
            redis_client.set(REDIS_KEY_DISK_READONLY, "1")
            logger.warning("已设置全局只读标志: %s=1", REDIS_KEY_DISK_READONLY)
        else:
            redis_client.delete(REDIS_KEY_DISK_READONLY)
            logger.info("已清除全局只读标志: %s", REDIS_KEY_DISK_READONLY)
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
        logger.error("设置只读标志失败: %s", e)


def _clear_readonly_if_set(redis_client: Any, usage_pct: float) -> None:
    """如果之前触发过只读但现在恢复正常，自动解除。"""
    try:
        current = redis_client.get(REDIS_KEY_DISK_READONLY)
        if current:
            logger.info(
                "系统盘使用率降至 %.1f%% < %.0f%% — 自动解除全局只读大闸",
                usage_pct,
                DISK_CRITICAL_THRESHOLD,
            )
            _set_readonly_flag(redis_client, False)
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
        logger.error("检查只读标志失败: %s", e)


# ---------------------------------------------------------------------------
# 独立运行入口
# ---------------------------------------------------------------------------


def main() -> None:
    """独立运行模式：连接 Redis，每 60s 检测一次系统盘。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )

    redis_host = os.getenv("REDIS_HOST", "redis")
    redis_port = int(os.getenv("REDIS_PORT", "6379"))
    redis_password = os.getenv("REDIS_PASSWORD") or None
    check_path = os.getenv("DISK_CHECK_PATH", "/")

    redis_client: Any | None = None
    try:
        import redis as redis_mod

        redis_client = redis_mod.Redis(
            host=redis_host,
            port=redis_port,
            password=redis_password,
            decode_responses=True,
            socket_timeout=5,
        )
        redis_client.ping()
        logger.info("Redis 连接成功: %s:%s", redis_host, redis_port)
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
        logger.warning("Redis 连接失败，将以无状态模式运行: %s", e)
        redis_client = None

    logger.info(
        "磁盘守护进程启动 — 检测路径: %s, 周期: %ss, 阈值: %.0f%%/%.0f%%",
        check_path,
        DISK_CHECK_INTERVAL_SEC,
        DISK_WARNING_THRESHOLD,
        DISK_CRITICAL_THRESHOLD,
    )

    while True:
        try:
            check_and_act(redis_client=redis_client, check_path=check_path)
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            logger.error("磁盘检测异常: %s", e)
        time.sleep(DISK_CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    main()
