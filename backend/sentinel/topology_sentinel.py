#!/usr/bin/env python3
"""
ZEN70 拓扑探针守护进程 (Topology Sentinel)。

负责硬件热插拔检测、容器熔断、状态机更新；不采集周期性指标（由 Categraf 负责）。
以固定周期轮询挂载点存活与 GPU 心跳，滑动窗口防抖、悲观锁、三重核验后写 Redis 并发布事件。

Constants, MountPoint, Docker API helpers extracted to sentinel_helpers.py.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import cast

import redis

from backend.core.security_policy import normalize_metric_integer
from backend.sentinel.sentinel_helpers import (
    DEFAULT_PENDING_TTL,
    DISK_CRITICAL_THRESHOLD,
    REDIS_CHANNEL_EVENTS,
    REDIS_CHANNEL_MELTDOWN,
    REDIS_KEY_GPU,
    HWState,
    MountPoint,
    _docker_api_get,
    _docker_api_post,
)
from backend.sentinel.sentinel_helpers import set_logger as _set_helpers_logger
from backend.sentinel.sentinel_helpers import (
    setup_logging,
)

logger: logging.LoggerAdapter | None = None


# -------------------- 探针主类 --------------------


class TopologySentinel:
    """
    拓扑探针：轮询挂载点与 GPU，防抖后更新 Redis 状态并发布事件，必要时执行 docker pause/unpause。
    """

    def __init__(self) -> None:
        host = os.getenv("REDIS_HOST")
        if not host:
            raise RuntimeError("REDIS_HOST env var is required")
        self.redis_host = host
        self.redis_port = int(os.getenv("REDIS_PORT", "6379"))
        self.redis_password = os.getenv("REDIS_PASSWORD") or None
        self.mock = os.getenv("MOCK_HARDWARE", "false").lower() in ("true", "1")
        self.interval = max(1, int(os.getenv("PROBE_INTERVAL", "5")))
        self.window_size = max(1, min(10, int(os.getenv("DEBOUNCE_WINDOW", "3"))))
        self.pending_ttl = int(os.getenv("PENDING_LOCK_TTL", str(DEFAULT_PENDING_TTL)))

        # 法典 7.2.1: 边缘节点脑裂防护静默状态 (Stop-Pulling)
        self.is_zombie = False
        self.redis_timeout_count = 0
        self.max_redis_timeouts = max(2, int(os.getenv("MAX_REDIS_TIMEOUTS", "6")))  # 默认 6 * 5s = 30s 熔断

        # 边缘离线自治：Redis 断联时保留最后已知的期望状态用于本地闭环调谐
        self._cached_desired: set[str] = set()
        self._cached_managed: set[str] = set()

        # 磁盘污点机制：代替命令式 stop，由 _reconcile_loop 统一裁决
        self.has_disk_taint = False

        # 优雅关闭标志：SIGTERM / KeyboardInterrupt 触发后置为 True，主循环在下一轮检查时退出
        self._stop_event = threading.Event()

        self.mounts: list[MountPoint] = []
        mount_points_env = os.getenv("MOUNT_POINTS", "").strip()
        if mount_points_env:
            for part in mount_points_env.split(";"):
                part = part.strip()
                if not part:
                    continue
                seg = [s.strip() for s in part.split(",")]
                path = seg[0]
                uid = seg[1] if len(seg) > 1 and seg[1] else None
                min_gb = int(seg[2]) if len(seg) > 2 and seg[2].isdigit() else 1
                self.mounts.append(MountPoint(path, uid, min_gb))

        self._redis: redis.Redis | None = None
        self._connect_redis()

        if logger:
            logger.info(
                "TopologySentinel initialized mock=%s interval=%ss mounts=%s",
                self.mock,
                self.interval,
                len(self.mounts),
            )

    def _connect_redis(self) -> None:
        """连接 Redis，失败时指数退避重试（2/4/8/16/32s）后退出。"""
        backoff = [2, 4, 8, 16, 32]
        user = os.getenv("REDIS_USER", "default")
        for attempt in range(5):
            try:
                r = redis.Redis(
                    host=self.redis_host,
                    port=self.redis_port,
                    username=user if self.redis_password else None,
                    password=self.redis_password,
                    decode_responses=True,
                    socket_connect_timeout=5,
                )
                r.ping()
                self._redis = r
                if logger:
                    logger.info("Connected to Redis")
                return
            except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
                if logger:
                    logger.warning(
                        "Redis connection attempt %s/5 failed: %s, next retry in %ss",
                        attempt + 1,
                        e,
                        backoff[attempt],
                    )
                time.sleep(backoff[attempt])
        if logger:
            logger.error("Redis unavailable after retries, entering zombie mode (Split-Brain Prevention)")
        self.is_zombie = True

    def _redis_ok(self) -> bool:
        """检查 Redis 是否可用，多次超时进入脑裂防备的 zombie 态，恢复重置"""
        try:
            r = self._redis
            if r is not None:
                r.ping()
                if self.is_zombie and logger:
                    logger.info("Redis reconnected. Leaving zombie mode.")
                self.is_zombie = False
                self.redis_timeout_count = 0
                return True
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
            if logger:
                logger.debug("Redis ping failed: %s", exc)
            self.redis_timeout_count += 1
            if self.redis_timeout_count >= self.max_redis_timeouts and not self.is_zombie:
                if logger:
                    logger.warning("Redis ping threshold exceeded, entering zombie mode (Split-Brain Prevention)")
                self.is_zombie = True
        try:
            self._connect_redis()
            return True
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
            if logger:
                logger.debug("Redis reconnect failed: %s", exc)
            return False

    def _get_actual_running_containers(self) -> set[str]:
        """
        Observe: 获取当前真正处于 'running' 状态的 Docker 容器名列表。

        关键修复：Docker API `/containers/json` 默认返回所有非 exited 容器，
        包括 paused 容器。必须严格过滤 State == 'running'，否则 paused 容器
        会被调谐器误判为健康运行，导致永远无法自愈恢复。
        使用 ?filters={"status":["running"]} 参数让 Docker Engine 端精确过滤。
        """
        # 法典修复：使用 Docker API filter 只返回真正 running 的容器
        import urllib.parse

        filter_json = json.dumps({"status": ["running"]})
        query_path = f"/containers/json?filters={urllib.parse.quote(filter_json)}"
        status_code, data = _docker_api_get(query_path)
        if status_code != 200:
            if logger:
                logger.error("Observe failed: Docker API returned %s", status_code)
            return set()
        if not isinstance(data, list):
            return set()
        names: set[str] = set()
        for container in data:
            for n in container.get("Names", []):
                names.add(n.lstrip("/"))
        return names

    # -------------------- P0-2：安全容器操作（法典 3.1 三步极刑） --------------------

    # 有状态容器列表 — 由 IaC 编译器从 system.yaml sentinel.stateful_containers 注入
    # 禁止硬编码（法典 §1.2 IAC 唯一事实来源、.env.j2 注释同载）
    @staticmethod
    def _load_stateful_containers() -> set[str]:
        raw = os.getenv("SENTINEL_STATEFUL_CONTAINERS", "").strip()
        if raw:
            return {s.strip() for s in raw.split(",") if s.strip()}
        # 运行时兜底（仅在 .env 未注入时生效，零知识下保护数据库安全）
        return {"zen70-postgres", "zen70-redis"}

    @property
    def stateful_containers(self) -> set[str]:
        if not hasattr(self, "_stateful_containers_cache"):
            self._stateful_containers_cache = self._load_stateful_containers()
        return self._stateful_containers_cache

    def _safe_container_action(self, container_name: str, action: str) -> None:
        """
        法典 3.1：安全容器降级操作 (HTTP API 替代 CLI)。
        - 纯 I/O 容器：docker pause
        - 有状态容器：docker stop -t 10
        - action: 'stop' | 'start'

        离线自治：zombie mode 下仍然允许操作（Docker API 是本地 socket，不依赖 Redis）。
        """
        import urllib.parse

        encoded_name = urllib.parse.quote(container_name, safe="")

        if action == "stop":
            if container_name in self.stateful_containers:
                if logger:
                    logger.info("[SafeAction] Graceful stop (stateful) for %s", container_name)
                code, body = _docker_api_post(f"/containers/{encoded_name}/stop?t=10")
                if code not in (204, 304, 404):
                    if logger:
                        logger.warning("[SafeAction] stop failed for %s (code %s), escalating to kill", container_name, code)
                    _docker_api_post(f"/containers/{encoded_name}/kill")
            else:
                # 法典 3.1: 纯 I/O 容器 pause 包裹 3s 异步超时升级 SIGKILL
                if logger:
                    logger.info("[SafeAction] Pause (IO, 3s timeout) for %s", container_name)
                code, body = _docker_api_post(f"/containers/{encoded_name}/pause", timeout=3)
                if code not in (204, 304, 404):
                    if logger:
                        logger.warning(
                            "[SafeAction] pause failed/timeout for %s (code %s), escalating to SIGKILL",
                            container_name,
                            code,
                        )
                    _docker_api_post(f"/containers/{encoded_name}/kill")
        elif action == "start":
            # 先尝试 unpause (如果被 pause 了)，如果未被 pause 会返回 500 (Container is not paused)
            code, body = _docker_api_post(f"/containers/{encoded_name}/unpause")
            if code not in (204, 500, 304):
                if logger:
                    logger.warning("[SafeAction] unpause returned code %s for %s", code, container_name)
            # 无论 unpause 结果如何，都发送 start 以防容器是 Exited 状态
            code2, body2 = _docker_api_post(f"/containers/{encoded_name}/start")
            if code2 not in (204, 304, 404):
                if logger:
                    logger.error("[SafeAction] start failed for %s: %s", container_name, body2[:200])

    # -------------------- P0-1：系统盘 95% 物理熔断（法典 3.3） --------------------

    # 法典修复：受磁盘污点影响需降级的高频 I/O 容器列表
    DISK_TAINT_AFFECTED: set[str] = {"zen70-jellyfin", "zen70-frigate", "zen70-promtail"}

    def _check_disk_usage(self) -> None:
        """
        法典 3.3：系统盘使用率 ≥95% 时注入磁盘污点 (disk_taint)。

        关键修复：不再直接命令式 pause 容器（这会与声明式调谐循环 _reconcile_loop
        产生指令打架/Thrashing）。改为设置 self.has_disk_taint = True，交由
        _compute_desired_containers() 将受影响容器的期望状态强覆写为 OFF，
        再由 _reconcile_loop 统一执行平滑降级与自愈恢复。
        """
        try:
            usage = shutil.disk_usage("/")
        except OSError:
            # Windows 开发环境或路径不可达时降级检查系统盘
            try:
                usage = shutil.disk_usage(Path(__file__).resolve().anchor)
            except OSError:
                return

        used_pct = (usage.used / usage.total) * 100 if usage.total > 0 else 0

        if used_pct >= DISK_CRITICAL_THRESHOLD:
            if not self.has_disk_taint:
                if logger:
                    logger.critical(
                        "🔴 [DISK-TAINT] 系统盘使用率 %.1f%% ≥ %.0f%%，注入磁盘污点！" " 高频组件将由调谐循环统一降级。",
                        used_pct,
                        DISK_CRITICAL_THRESHOLD,
                    )
            self.has_disk_taint = True

            # 通过 Redis 发布紧急告警事件 + 写入持久化标记供网关读取
            r = self._redis
            if r is not None:
                try:
                    event = {
                        "type": "disk_critical",
                        "used_pct": round(used_pct, 1),
                        "threshold": DISK_CRITICAL_THRESHOLD,
                        "action": "taint_injected",
                        "timestamp": str(time.time()),
                    }
                    r.publish(REDIS_CHANNEL_EVENTS, json.dumps(event))
                    r.set("zen70:disk_breaker", "active", ex=300)
                except (OSError, ValueError, KeyError, RuntimeError, TypeError) as pub_err:
                    if logger:
                        logger.error("[DISK-TAINT] Redis publish failed: %s", pub_err)
        else:
            # 磁盘恢复正常，自动清除污点 → 调谐循环将自动恢复受影响容器
            if self.has_disk_taint:
                if logger:
                    logger.info(
                        "🟢 [DISK-TAINT] 系统盘使用率 %.1f%% < %.0f%%，清除磁盘污点。" " 受影响组件将由调谐循环自动恢复。",
                        used_pct,
                        DISK_CRITICAL_THRESHOLD,
                    )
                self.has_disk_taint = False
                r = self._redis
                if r is not None:
                    with contextlib.suppress(OSError, ValueError, KeyError, RuntimeError, TypeError):
                        r.delete("zen70:disk_breaker")

    def _get_gpu_taints(self) -> set[str]:
        gpu_taints = set()
        r = self._redis
        if r is not None:
            try:
                gpu_state_raw: dict[str, str] = r.hgetall(REDIS_KEY_GPU)
                if gpu_state_raw and gpu_state_raw.get("taint"):
                    gpu_taints.add(gpu_state_raw["taint"])
            except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
                if logger:
                    logger.debug("gpu taint read failed: %s", e)
        return gpu_taints

    def _get_switch_map(self) -> dict[str, str]:
        switch_map_raw = os.getenv("SWITCH_CONTAINER_MAP", "{}")
        try:
            parsed = json.loads(switch_map_raw)
            if isinstance(parsed, dict):
                return {str(k): str(v) for k, v in parsed.items()}
            return {}
        except json.JSONDecodeError:
            return {}

    def _compute_desired_containers(self) -> tuple[set[str], set[str]]:
        """
        计算期望运行的容器集，返回 (desired_running, managed_by_sentinel) 集合。

        污点(Taint)拦截优先级：
        1. GPU 过热污点 → 媒体类组件强制 OFF
        2. 磁盘满污点 → DISK_TAINT_AFFECTED 列表中的高频 I/O 组件强制 OFF
        3. 用户手动开关 switch_expected → 正常期望值
        """
        switch_map = self._get_switch_map()
        gpu_taints = self._get_gpu_taints()

        desired: set[str] = set()
        managed: set[str] = set()
        r = self._redis
        for switch_name, container_name in switch_map.items():
            managed.add(container_name)
            expected_state = "OFF"
            try:
                if r is not None:
                    redis_exp = r.get(f"switch_expected:{switch_name}")
                    if redis_exp:
                        expected_state = str(redis_exp)
            except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
                if logger:
                    logger.debug("switch_expected read failed: %s", e)

            # 污点拦截 1: GPU 过热 → 媒体类组件降级
            if "overheating:NoSchedule" in gpu_taints and "media" in switch_name.lower() and expected_state == "ON":
                if logger:
                    logger.warning("Taint Active (overheating). Forcing component '%s' to OFF.", switch_name)
                expected_state = "OFF"

            # 污点拦截 2: 磁盘满 → 高频 I/O 组件降级（法典 3.3 声明式融合）
            if self.has_disk_taint and container_name in self.DISK_TAINT_AFFECTED:
                if expected_state == "ON":
                    if logger:
                        logger.warning(
                            "Taint Active (disk_critical). Forcing component '%s' (%s) to OFF.",
                            switch_name,
                            container_name,
                        )
                    expected_state = "OFF"

            if expected_state == "ON":
                desired.add(container_name)
        # 边缘离线自治：缓存最后已知的期望状态
        self._cached_desired = set(desired)
        self._cached_managed = set(managed)
        return desired, managed

    def _reconcile_loop(self) -> None:
        """
        K3s-Inspired Reconciliation Loop (声明式控制循环): Observe -> Diff -> Act
        核心思想: 抛弃事件触发器, 改为保证系统实际状态向预期状态收敛.
        预期状态 (Desired state): yaml 中的 SWITCH_CONTAINER_MAP 配合用户手动设置 (存 Redis switch_expected:) + 污点 (Taints) 妥协.

        边缘离线自治 (Offline Autonomy):
        zombie mode 下使用最后已知的缓存期望状态继续本地调谐，
        保持容器拓扑闭环控制，直到 Redis 恢复。
        """
        if self.is_zombie:
            self._reconcile_loop_offline()
            return
        if not self._redis_ok() or not self._redis:
            return

        r = self._redis

        # 1. 整理预期状态 (Desired)
        desired_running_containers, containers_managed_by_sentinel = self._compute_desired_containers()

        # 2. 观察实际状态 (Observe)
        actual_running = self._get_actual_running_containers()

        # 3. 对比差异 (Diff) & 执行同步 (Act)
        # 只管治 SWITCH_CONTAINER_MAP 列表里的容器，不干涉核心网关等容器
        for container in containers_managed_by_sentinel:
            should_run = container in desired_running_containers
            is_running = container in actual_running

            if should_run and not is_running:
                # 状态弹簧对齐: 应该跑但没跑 -> HTTP API start
                if logger:
                    logger.info(
                        "[Reconcile] Diff detected: %s is OFF but expected ON. Act: HTTP start",
                        container,
                    )
                self._safe_container_action(str(container), "start")

            elif not should_run and is_running:
                if logger:
                    logger.info(
                        "[Reconcile] Diff detected: %s is ON but expected OFF (or Tainted). Act: safe_container_action stop",
                        container,
                    )
                self._safe_container_action(str(container), "stop")
                if r is not None:
                    try:
                        r.publish(
                            REDIS_CHANNEL_MELTDOWN,
                            json.dumps({"container": container, "action": "route_remove"}),
                        )
                    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as pub_err:
                        if logger:
                            logger.debug("Meltdown route event publish failed: %s", pub_err)

    def _reconcile_loop_offline(self) -> None:
        """
        边缘离线自治：zombie mode 下基于缓存期望状态的本地闭环调谐。

        策略：
        - 使用 _cached_desired / _cached_managed（最后一次 Redis 在线时的快照）
        - 本地磁盘污点仍然生效（保护物理磁盘安全）
        - 只做 start/stop，不尝试 Redis 发布（无法联通）
        - 容器观察通过 Docker API（纯本地 socket，不依赖 Redis）
        """
        if not self._cached_managed:
            return

        desired = set(self._cached_desired)

        # 磁盘满污点在离线时仍然降级高频 I/O 组件
        if self.has_disk_taint:
            for container_name in list(desired):
                if container_name in self.DISK_TAINT_AFFECTED:
                    if logger:
                        logger.warning(
                            "[Offline Reconcile] Taint Active (disk_critical). Forcing '%s' OFF.",
                            container_name,
                        )
                    desired.discard(container_name)

        actual_running = self._get_actual_running_containers()

        for container in self._cached_managed:
            should_run = container in desired
            is_running = container in actual_running

            if should_run and not is_running:
                if logger:
                    logger.info(
                        "[Offline Reconcile] %s is OFF but cached-desired ON. Act: start",
                        container,
                    )
                self._safe_container_action(str(container), "start")
            elif not should_run and is_running:
                if logger:
                    logger.info(
                        "[Offline Reconcile] %s is ON but cached-desired OFF. Act: stop",
                        container,
                    )
                self._safe_container_action(str(container), "stop")

    def _update_state(
        self,
        mount: MountPoint,
        state: str,
        reason: str = "",
    ) -> None:
        """更新 Redis 中 hw:<path> 哈希并发布 hardware:events 事件。"""
        r = self._redis
        if r is None:
            return
        key = f"hw:{mount.path}"
        data: dict[str, str] = {
            "path": str(mount.path),
            "uuid": mount.expected_uuid or "",
            "state": state,
            "timestamp": str(time.time()),
            "reason": reason,
        }
        try:
            r.hset(key, mapping=data)  # type: ignore[arg-type]
            event = {
                "type": "hardware_change",
                "path": str(mount.path),
                "state": state,
                "reason": reason,
            }
            r.publish(REDIS_CHANNEL_EVENTS, json.dumps(event))
            if logger:
                logger.info("State updated %s: %s (%s)", mount.path, state, reason)
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            if logger is not None:
                logger.error("Redis update_state failed: %s", e, exc_info=True)

    def _check_gpu(self) -> dict[str, str]:
        """检测 GPU 状态，并主动生成污点 (Taints) 信号供控制循环参考"""
        if self.mock:
            return {
                "online": "true",
                "temp": "45",
                "util": "30",
                "tags": json.dumps(["gpu_nvenc_v1"]),
            }
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=temperature.gpu,utilization.gpu",
                    "--format=csv,noheader",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return {"online": "false"}
            line = result.stdout.strip().split("\n")[0]
            parts = [p.strip().replace(" %", "").replace(" ", "") for p in line.split(",")]

            payload = {
                "online": "true",
                "tags": json.dumps(["gpu_nvenc_v1", "gpu_cuvid"]),
            }
            if len(parts) >= 2:
                temp_value = normalize_metric_integer(parts[0], field_name="gpu_temp", min_value=0, max_value=200)
                util_value = normalize_metric_integer(parts[1], field_name="gpu_util", min_value=0, max_value=100)
                if temp_value is not None:
                    payload["temp"] = temp_value
                if util_value is not None:
                    payload["util"] = util_value

                # 注入污点机制 (Taint: overheating:NoSchedule)
                try:
                    if temp_value is None:
                        raise ValueError("gpu temp is invalid")
                    target_temp = int(temp_value)
                    if target_temp > 85:
                        payload["taint"] = "overheating:NoSchedule"
                    else:
                        payload["taint"] = ""  # 清理污点
                except ValueError as e:
                    if logger:
                        logger.debug("GPU temp parse failed: %s", e)

            return payload
        except (
            subprocess.TimeoutExpired,
            subprocess.CalledProcessError,
            FileNotFoundError,
        ) as e:
            if logger:
                logger.warning("GPU check failed: %s", e)
            return {"online": "false", "tags": "[]"}

    def _process_mount_offline(self, mount: MountPoint, cur_state: str | None) -> None:
        if cur_state == HWState.PENDING:
            return
        r = self._redis
        try:
            if r is not None:
                r.setex(mount.pending_lock_key, self.pending_ttl, "PENDING")
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            if logger is not None:
                logger.error("Redis setex PENDING failed: %s", e)
        self._update_state(mount, HWState.PENDING, "offline detected")

    def _process_mount_online(self, mount: MountPoint, cur_state: str | None) -> None:
        if cur_state == HWState.PENDING:
            ok, reason = mount.verify_full()
            if ok:
                if logger:
                    logger.info("Mount %s passed verification", mount.path)
                self._update_state(mount, HWState.ONLINE, "verified online")
                r = self._redis
                try:
                    if r is not None:
                        r.delete(mount.pending_lock_key)
                except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
                    if logger:
                        logger.debug("Redis pending lock delete failed: %s", e)
            else:
                if logger is not None:
                    logger.warning("Mount %s logic verification failed: %s", mount.path, reason)
                self._update_state(mount, HWState.PENDING, f"verification failed: {reason}")
        elif cur_state != HWState.ONLINE:
            self._update_state(mount, HWState.ONLINE, "online")

    def _process_mount_state_change(self, mount: MountPoint, new_state: str, cur_state: str | None) -> None:
        """处理检测到的挂载状态变动并写入 Redis。"""
        if new_state == HWState.OFFLINE:
            self._process_mount_offline(mount, cur_state)
        else:
            self._process_mount_online(mount, cur_state)

    def _handle_mount(self, mount: MountPoint) -> None:
        """对单个挂载点执行检测防抖与状态更新；不再在此直接执行降级（交由 Reconcile）

        离线自治：zombie mode 下仍检测挂载点（纯本地 I/O），但跳过 Redis 状态读取。
        """
        if not self.is_zombie and not self._redis_ok():
            return
        exists: bool
        exists = (time.time() % 10) > 3 if self.mock else mount.check_exists()
        mount.state_cache.append(exists)

        if len(mount.state_cache) < self.window_size:
            return
        if not all(v == mount.state_cache[0] for v in mount.state_cache):
            return

        current_alive = mount.state_cache[0]
        new_state = HWState.ONLINE if current_alive else HWState.OFFLINE

        key = f"hw:{mount.path}"
        cur_state: str | None = None
        r = self._redis
        try:
            if r is not None:
                cur_state_val = r.hget(key, "state")
                if cur_state_val:
                    cur_state = str(cur_state_val)
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            if logger:
                logger.debug("Redis hw state read failed: %s", e)

        self._process_mount_state_change(mount, new_state, cur_state)

    def _probe_gpu(self) -> None:
        """Step 2: GPU 状态核验并打污点，支持离线自治（本地 nvidia-smi 不依赖 Redis）。"""
        r = self._redis
        if self._redis_ok() and r is not None:
            try:
                gpu_state = self._check_gpu()
                r.hset(REDIS_KEY_GPU, mapping=gpu_state)  # type: ignore[arg-type]
            except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
                if logger:
                    logger.warning("GPU state write failed: %s", e)
        elif self.is_zombie:
            try:
                self._check_gpu()
            except (OSError, ValueError, KeyError, RuntimeError, TypeError):
                if logger:
                    logger.debug("GPU check failed in zombie mode (non-critical)")

    def run_once(self) -> None:
        """执行一次检测周期与强一致性调谐。"""
        # Step 0: 系统盘 95% 物理熔断预检（法典 3.3 最高优先级）
        try:
            self._check_disk_usage()
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            if logger:
                logger.error("Disk usage check failed: %s", e)

        # Step 1: 处理挂载点心跳 (I/O)
        for mount in self.mounts:
            try:
                self._handle_mount(mount)
            except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
                if logger:
                    logger.error("Error handling mount %s: %s", mount.path, e)

        # Step 2: GPU 状态核验
        self._probe_gpu()

        # Step 3: K3s 调谐循环 (Reconcile loop)
        try:
            self._reconcile_loop()
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            if logger:
                logger.error("Reconcile loop crashed: %s", e, exc_info=True)

    def _process_switch_event_message(self, data: str | bytes) -> None:
        """解析单条 switch:events 消息并将期望状态压入 Redis"""
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        try:
            obj = json.loads(data) if isinstance(data, str) else data
            from backend.core.events_schema import SwitchEventPayload

            payload = SwitchEventPayload.from_redis_message(cast(dict[str, object], obj))
            if payload is None:
                return
            switch_name = payload.effective_switch_name()
            state = payload.state
            if not switch_name or not state:
                return
            if logger is not None:
                logger.info("Setting desired state for %s to %s", switch_name, state)
            if self._redis is not None:
                self._redis.set(f"switch_expected:{switch_name}", str(state))
        except (json.JSONDecodeError, TypeError) as e:
            if logger is not None:
                logger.debug("invalid switch event payload: %s", e)

    def _redis_listener_thread(self) -> None:
        """后台专职监听 Redis pub/sub，现在只做状态写入(Desired State)，不直接操作物理层。"""
        r = self._redis
        if r is None or self.is_zombie:
            return
        try:
            pubsub = r.pubsub()
            pubsub.subscribe("switch:events")
            if logger is not None:
                logger.info("Topology sentinel starting declarative Redis pub/sub listener on switch:events")
            # 法典 2.1：消费使用 get_message(timeout=...) 避免 listen() 无限阻塞，便于探针自愈与退出
            while not self.is_zombie and r and not self._stop_event.is_set():
                message = pubsub.get_message(timeout=3)
                if message is None:
                    continue
                if message.get("type") != "message":
                    continue
                data = message.get("data")
                if data:
                    self._process_switch_event_message(data)
        except redis.ConnectionError:
            if logger is not None:
                logger.warning("Redis listener connection lost (will exit loop)")
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            if logger is not None:
                logger.error("Redis listener thread crashed: %s", e)

    def run(self) -> None:
        """主循环：按间隔周期执行 run_once；单次超时（interval*2）则告警并等待本周期结束再下一轮。"""
        if logger:
            logger.info("Starting topology sentinel main loop")

        # 启动后台监听线程 (守护线程)
        listener = threading.Thread(target=self._redis_listener_thread, daemon=True)
        listener.start()

        cycle_timeout = max(self.interval * 2, 10)
        while not self._stop_event.is_set():
            t = threading.Thread(target=self._run_once_safe)
            t.start()
            t.join(timeout=cycle_timeout)
            if t.is_alive():
                if logger:
                    logger.warning(
                        "run_once exceeded %ss, waiting for cycle to finish",
                        cycle_timeout,
                    )
                t.join()
            # Use Event.wait instead of time.sleep so SIGTERM wakes us immediately
            self._stop_event.wait(timeout=self.interval)

        if logger:
            logger.info("Topology sentinel stopped gracefully")

    def _evict_zombie_tasks(self) -> None:
        """
        K3s 优雅驱逐 (Eviction & Tombstones): 探测失联超过 15 秒的 Worker 节点,
        并为其认领的积压任务颁发墓碑 (Tombstone), 防止脑裂双写。
        """
        r = self._redis
        if not self._redis_ok() or r is None:
            return

        stream_key = "zen70:iot:stream:commands"
        group_name = "zen70_iot_workers"

        try:
            # 1. 获取所有消费者信息
            consumers = r.xinfo_consumers(stream_key, group_name)
            for c in consumers:
                idle_ms = c.get("idle", 0)
                pending_count = c.get("pending", 0)
                consumer_name = c.get("name", "")

                # 2. 如果 Worker 失联超过 15 秒且手头有卡住的任务
                if idle_ms > 15000 and pending_count > 0:
                    if logger is not None:
                        logger.warning(
                            "🧟‍♂️ [Eviction] Worker %s is OFFINE (>15s). Evicting tasks!",
                            consumer_name,
                        )

                    # 3. 查出它卡住的 Message ID
                    pending_info = r.xpending_range(stream_key, group_name, "-", "+", pending_count, consumer_name)
                    for p in pending_info:
                        msg_id = p.get("message_id")
                        if not msg_id:
                            continue

                        # 4. 读取原始 Payload 获取 command_id
                        msg_data = r.xrange(stream_key, msg_id, msg_id)
                        if msg_data:
                            _, payload = msg_data[0]
                            command_id = payload.get("command_id")
                            if command_id:
                                # 5. 宣判物理死亡 (写入墓碑，保留 24 小时)
                                tombstone_key = f"zen70:tombstone:{command_id}"
                                r.setex(tombstone_key, 86400, "evicted")
                                if logger is not None:
                                    logger.info(
                                        "🪦 [Eviction] Tombstone written for dead command: %s",
                                        command_id,
                                    )

        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            # Redis 流可能还没初始化，或者命令不支持，容错处理
            if logger is not None:
                logger.debug("Eviction loop skipped: %s", e)

    def _run_once_safe(self) -> None:
        """run_once 的线程安全包装，捕获异常避免拖垮线程。"""
        try:
            self.run_once()
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            if logger:
                logger.error("run_once error: %s", e, exc_info=True)


# -------------------- 入口 --------------------


def main() -> None:
    """解析环境、初始化日志与探针并进入主循环。"""
    global logger
    logger = setup_logging(request_id=str(uuid.uuid4()))
    _set_helpers_logger(logger)
    sentinel = TopologySentinel()

    # 注册 SIGTERM 处理器，确保 K8s/Docker 优雅关闭期间能清理 Redis 连接和 event loop
    def _handle_sigterm(signum: int, frame: object) -> None:
        del signum, frame
        if logger:
            logger.info("SIGTERM received, initiating graceful shutdown")
        # Guard against the signal being delivered before sentinel is fully initialised.
        stop_event = getattr(sentinel, "_stop_event", None)
        if stop_event is not None:
            stop_event.set()

    signal.signal(signal.SIGTERM, _handle_sigterm)

    try:
        sentinel.run()
    except KeyboardInterrupt:
        if logger:
            logger.info("Shutting down by user (SIGINT)")
        sentinel._stop_event.set()
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
        if logger:
            logger.critical("Unhandled exception: %s", e, exc_info=True)
        sys.exit(1)
    finally:
        # 确保 Redis 连接在退出前关闭
        if sentinel._redis is not None:
            try:
                sentinel._redis.close()
            except Exception:
                if logger:
                    logger.debug("Redis close failed during shutdown", exc_info=True)


if __name__ == "__main__":
    main()
