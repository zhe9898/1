#!/usr/bin/env python3
"""
ZEN70              (Topology Sentinel) ?
                                               ?Categraf       ?                   ?GPU                               ?Redis          ?
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


# --------------------        --------------------


class TopologySentinel:
    """
                    ?GPU          Redis                      docker pause/unpause ?    """

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

        #     7.2.1:                   ?(Stop-Pulling)
        self.is_zombie = False
        self.redis_timeout_count = 0
        self.max_redis_timeouts = max(2, int(os.getenv("MAX_REDIS_TIMEOUTS", "6")))  #     6 * 5s = 30s

        #            edis                                  ?        self._cached_desired: set[str] = set()
        self._cached_managed: set[str] = set()

        #                    stop    _reconcile_loop
        self.has_disk_taint = False

        #            IGTERM / KeyboardInterrupt        ?True                    ?        self._stop_event = threading.Event()

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
        """    Redis                 2/4/8/16/32s        ?""
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
        """   ?Redis                          zombie          """
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
        Observe:              'running'       Docker          ?
                ocker API `/containers/json`             exited     ?            paused              ?State == 'running'    ?paused
                                            ?            ?filters={"status":["running"]}     ?Docker Engine          ?        """
        #           ?Docker API filter        ?running     ?        import urllib.parse

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

    # -------------------- P0-2                3.1        ?--------------------

    #           ? ? ?IaC        system.yaml sentinel.stateful_containers
    #               1.2 IAC           ?env.j2        ?    @staticmethod
    def _load_stateful_containers() -> set[str]:
        raw = os.getenv("SENTINEL_STATEFUL_CONTAINERS", "").strip()
        if raw:
            return {s.strip() for s in raw.split(",") if s.strip()}
        #              .env                             ?        return {"zen70-postgres", "zen70-redis"}

    @property
    def stateful_containers(self) -> set[str]:
        if not hasattr(self, "_stateful_containers_cache"):
            self._stateful_containers_cache = self._load_stateful_containers()
        return self._stateful_containers_cache

    def _safe_container_action(self, container_name: str, action: str) -> None:
        """
            3.1             ?(HTTP API     CLI) ?        -  ?I/O      ocker pause
        -          docker stop -t 10
        - action: 'stop' | 'start'

                ombie mode             Docker API     ?socket       Redis   ?        """
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
                #     3.1:  ?I/O     pause     3s           SIGKILL
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
            #     ?unpause (    ?pause  ?       ?pause     ?500 (Container is not paused)
            code, body = _docker_api_post(f"/containers/{encoded_name}/unpause")
            if code not in (204, 500, 304):
                if logger:
                    logger.warning("[SafeAction] unpause returned code %s for %s", code, container_name)
            #     unpause             ?start        ?Exited    ?            code2, body2 = _docker_api_post(f"/containers/{encoded_name}/start")
            if code2 not in (204, 304, 404):
                if logger:
                    logger.error("[SafeAction] start failed for %s: %s", container_name, body2[:200])

    # -------------------- P0-1       95%           ?3.3 ?--------------------

    #                            ?I/O
    DISK_TAINT_AFFECTED: set[str] = {"zen70-jellyfin", "zen70-frigate", "zen70-promtail"}

    def _check_disk_usage(self) -> None:
        """
            3.3          ? ?5%           ?(disk_taint) ?
                           pause                    ?_reconcile_loop
                 /Thrashing         ?self.has_disk_taint = True    ?        _compute_desired_containers()                       ?OFF ?            _reconcile_loop                      ?        """
        try:
            usage = shutil.disk_usage("/")
        except OSError:
            # Windows
            try:
                usage = shutil.disk_usage(Path(__file__).resolve().anchor)
            except OSError:
                return

        used_pct = (usage.used / usage.total) * 100 if usage.total > 0 else 0

        if used_pct >= DISK_CRITICAL_THRESHOLD:
            if not self.has_disk_taint:
                if logger:
                    logger.critical(
                        "   [DISK-TAINT]           %.1f%%  ?%.0f%%            " "                       ?,
                        used_pct,
                        DISK_CRITICAL_THRESHOLD,
                    )
            self.has_disk_taint = True

            #     Redis             ?+
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
            #                    ? ?
            if self.has_disk_taint:
                if logger:
                    logger.info(
                        "   [DISK-TAINT]           %.1f%% < %.0f%%            ? "                         ?,
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
                           ?(desired_running, managed_by_sentinel)     ?
           (Taint)
        1. GPU         ?          ?OFF
        2.        ? ?DISK_TAINT_AFFECTED           I/O        OFF
        3.          ?switch_expected  ?       ?        """
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

            #        1: GPU      ?          ?            if "overheating:NoSchedule" in gpu_taints and "media" in switch_name.lower() and expected_state == "ON":
                if logger:
                    logger.warning("Taint Active (overheating). Forcing component '%s' to OFF.", switch_name)
                expected_state = "OFF"

            #        2:     ? ?    I/O           ?3.3
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
        #                            ?        self._cached_desired = set(desired)
        self._cached_managed = set(managed)
        return desired, managed

    def _reconcile_loop(self) -> None:
        """
        K3s-Inspired Reconciliation Loop (          ?: Observe -> Diff -> Act
              :           ?                           ?
              ?(Desired state): yaml     SWITCH_CONTAINER_MAP              ( ?Redis switch_expected:) +     (Taints)    .

                  (Offline Autonomy):
        zombie mode
                           ?Redis     ?        """
        if self.is_zombie:
            self._reconcile_loop_offline()
            return
        if not self._redis_ok() or not self._redis:
            return

        r = self._redis

        # 1.          ?(Desired)
        desired_running_containers, containers_managed_by_sentinel = self._compute_desired_containers()

        # 2.          ?(Observe)
        actual_running = self._get_actual_running_containers()

        # 3.        (Diff) &        (Act)
        #     ?SWITCH_CONTAINER_MAP                          ?        for container in containers_managed_by_sentinel:
            should_run = container in desired_running_containers
            is_running = container in actual_running

            if should_run and not is_running:
                #          ?           -> HTTP API start
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
                   ombie mode                          ?
            ?        -     _cached_desired / _cached_managed       ?Redis           ?        -
        -     start/stop       Redis
        -           Docker API       socket       Redis ?        """
        if not self._cached_managed:
            return

        desired = set(self._cached_desired)

        #                       ?I/O
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
        """    Redis  ?hw:<path>        ?hardware:events     ?""
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
        """   ?GPU                ?(Taints)              ?""
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

                #           (Taint: overheating:NoSchedule)
                try:
                    if temp_value is None:
                        raise ValueError("gpu temp is invalid")
                    target_temp = int(temp_value)
                    if target_temp > 85:
                        payload["taint"] = "overheating:NoSchedule"
                    else:
                        payload["taint"] = ""  #
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
        """                        Redis ?""
        if new_state == HWState.OFFLINE:
            self._process_mount_offline(mount, cur_state)
        else:
            self._process_mount_online(mount, cur_state)

    def _handle_mount(self, mount: MountPoint) -> None:
        """                                               ?Reconcile ?
                ombie mode                   I/O       ?Redis         ?        """
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
        """Step 2: GPU                            ?nvidia-smi     ?Redis   ?""
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
        """                         ?""
        # Step 0:     ?95%              ?3.3          ?        try:
            self._check_disk_usage()
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            if logger:
                logger.error("Disk usage check failed: %s", e)

        # Step 1:           ?(I/O)
        for mount in self.mounts:
            try:
                self._handle_mount(mount)
            except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
                if logger:
                    logger.error("Error handling mount %s: %s", mount.path, e)

        # Step 2: GPU       ?        self._probe_gpu()

        # Step 3: K3s        (Reconcile loop)
        try:
            self._reconcile_loop()
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            if logger:
                logger.error("Reconcile loop crashed: %s", e, exc_info=True)

    def _process_switch_event_message(self, data: str | bytes) -> None:
        """       switch:events                ?Redis"""
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
        """          Redis pub/sub             ?Desired State)               ?""
        r = self._redis
        if r is None or self.is_zombie:
            return
        try:
            pubsub = r.pubsub()
            pubsub.subscribe("switch:events")
            if logger is not None:
                logger.info("Topology sentinel starting declarative Redis pub/sub listener on switch:events")
            #     2.1       ?get_message(timeout=...)     listen()                      ?            while not self.is_zombie and r and not self._stop_event.is_set():
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
        """                ?run_once         interval*2                          ?""
        if logger:
            logger.info("Starting topology sentinel main loop")

        #              (      )
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
        K3s       ?(Eviction & Tombstones):           15     Worker    ,
                              (Tombstone),           ?        """
        r = self._redis
        if not self._redis_ok() or r is None:
            return

        stream_key = "zen70:iot:stream:commands"
        group_name = "zen70_iot_workers"

        try:
            # 1.               ?            consumers = r.xinfo_consumers(stream_key, group_name)
            for c in consumers:
                idle_ms = c.get("idle", 0)
                pending_count = c.get("pending", 0)
                consumer_name = c.get("name", "")

                # 2.     Worker        15
                if idle_ms > 15000 and pending_count > 0:
                    if logger is not None:
                        logger.warning(
                            "      ?[Eviction] Worker %s is OFFINE (>15s). Evicting tasks!",
                            consumer_name,
                        )

                    # 3.           Message ID
                    pending_info = r.xpending_range(stream_key, group_name, "-", "+", pending_count, consumer_name)
                    for p in pending_info:
                        msg_id = p.get("message_id")
                        if not msg_id:
                            continue

                        # 4.        Payload     command_id
                        msg_data = r.xrange(stream_key, msg_id, msg_id)
                        if msg_data:
                            _, payload = msg_data[0]
                            command_id = payload.get("command_id")
                            if command_id:
                                # 5.           (          ?24    )
                                tombstone_key = f"zen70:tombstone:{command_id}"
                                r.setex(tombstone_key, 86400, "evicted")
                                if logger is not None:
                                    logger.info(
                                        "   [Eviction] Tombstone written for dead command: %s",
                                        command_id,
                                    )

        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            # Redis                                ?            if logger is not None:
                logger.debug("Eviction loop skipped: %s", e)

    def _run_once_safe(self) -> None:
        """run_once                             ?""
        try:
            self.run_once()
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            if logger:
                logger.error("run_once error: %s", e, exc_info=True)


# --------------------     --------------------


def main() -> None:
    """                              ?""
    global logger
    logger = setup_logging(request_id=str(uuid.uuid4()))
    _set_helpers_logger(logger)
    sentinel = TopologySentinel()

    #     SIGTERM           K8s/Docker              ?Redis     ?event loop
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
        #     Redis
        if sentinel._redis is not None:
            try:
                sentinel._redis.close()
            except Exception:
                if logger:
                    logger.debug("Redis close failed during shutdown", exc_info=True)


if __name__ == "__main__":
    main()
