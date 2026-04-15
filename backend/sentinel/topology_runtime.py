from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from backend.sentinel.sentinel_helpers import DEFAULT_PENDING_TTL, MountPoint


@dataclass(frozen=True, slots=True)
class SentinelRuntimeSettings:
    redis_host: str
    redis_port: int
    redis_user: str | None
    redis_password: str | None
    mock: bool
    interval: int
    window_size: int
    pending_ttl: int
    max_redis_timeouts: int
    mounts: tuple[MountPoint, ...]
    switch_container_map: dict[str, str]
    stateful_containers: frozenset[str]


@dataclass(frozen=True, slots=True)
class DesiredContainerPlan:
    desired: frozenset[str]
    managed: frozenset[str]
    gpu_forced_off: frozenset[str]
    disk_forced_off: frozenset[str]


@dataclass(frozen=True, slots=True)
class ReconcileAction:
    container_name: str
    action: str


def load_runtime_settings(env: Mapping[str, str | None]) -> SentinelRuntimeSettings:
    host = (env.get("REDIS_HOST") or "").strip()
    if not host:
        raise RuntimeError("REDIS_HOST env var is required")

    redis_password = env.get("REDIS_PASSWORD") or None
    redis_user = (env.get("REDIS_USER") or "default") if redis_password else None
    mount_points = _parse_mount_points(env.get("MOUNT_POINTS"))

    return SentinelRuntimeSettings(
        redis_host=host,
        redis_port=int(env.get("REDIS_PORT") or "6379"),
        redis_user=redis_user,
        redis_password=redis_password,
        mock=_parse_bool(env.get("MOCK_HARDWARE")),
        interval=max(1, int(env.get("PROBE_INTERVAL") or "5")),
        window_size=max(1, min(10, int(env.get("DEBOUNCE_WINDOW") or "3"))),
        pending_ttl=int(env.get("PENDING_LOCK_TTL") or str(DEFAULT_PENDING_TTL)),
        max_redis_timeouts=max(2, int(env.get("MAX_REDIS_TIMEOUTS") or "6")),
        mounts=tuple(mount_points),
        switch_container_map=_parse_switch_container_map(env.get("SWITCH_CONTAINER_MAP")),
        stateful_containers=frozenset(_parse_stateful_containers(env.get("SENTINEL_STATEFUL_CONTAINERS"))),
    )


def compute_desired_containers(
    *,
    switch_map: Mapping[str, str],
    gpu_taints: set[str],
    has_disk_taint: bool,
    disk_taint_affected: set[str],
    read_expected_state: Callable[[str, str], str | None],
) -> DesiredContainerPlan:
    desired: set[str] = set()
    managed: set[str] = set()
    gpu_forced_off: set[str] = set()
    disk_forced_off: set[str] = set()

    for switch_name, container_name in switch_map.items():
        managed.add(container_name)
        expected_state = str(read_expected_state(switch_name, container_name) or "OFF")

        if "overheating:NoSchedule" in gpu_taints and "media" in switch_name.lower() and expected_state == "ON":
            expected_state = "OFF"
            gpu_forced_off.add(switch_name)

        if has_disk_taint and container_name in disk_taint_affected and expected_state == "ON":
            expected_state = "OFF"
            disk_forced_off.add(switch_name)

        if expected_state == "ON":
            desired.add(container_name)

    return DesiredContainerPlan(
        desired=frozenset(desired),
        managed=frozenset(managed),
        gpu_forced_off=frozenset(gpu_forced_off),
        disk_forced_off=frozenset(disk_forced_off),
    )


def compute_reconcile_actions(
    *,
    managed_containers: set[str],
    desired_running: set[str],
    actual_running: set[str],
) -> list[ReconcileAction]:
    actions: list[ReconcileAction] = []
    for container_name in sorted(managed_containers):
        should_run = container_name in desired_running
        is_running = container_name in actual_running
        if should_run and not is_running:
            actions.append(ReconcileAction(container_name=container_name, action="start"))
        elif not should_run and is_running:
            actions.append(ReconcileAction(container_name=container_name, action="stop"))
    return actions


def _parse_mount_points(raw: str | None) -> list[MountPoint]:
    mounts: list[MountPoint] = []
    if not raw:
        return mounts
    for part in raw.strip().split(";"):
        part = part.strip()
        if not part:
            continue
        segments = [segment.strip() for segment in part.split(",")]
        path = segments[0]
        uid = segments[1] if len(segments) > 1 and segments[1] else None
        min_gb = int(segments[2]) if len(segments) > 2 and segments[2].isdigit() else 1
        mounts.append(MountPoint(path, uid, min_gb))
    return mounts


def _parse_stateful_containers(raw: str | None) -> set[str]:
    if raw:
        return {item.strip() for item in raw.split(",") if item.strip()}
    return {"zen70-postgres", "zen70-redis"}


def _parse_switch_container_map(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): str(value) for key, value in parsed.items()}


def _parse_bool(raw: str | None) -> bool:
    return (raw or "false").lower() in {"true", "1"}
