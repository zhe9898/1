from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from backend.kernel.contracts.events_schema import build_hardware_state_event
from backend.platform.events.channels import CHANNEL_ROUTING_MELTDOWN, CHANNEL_SWITCH_COMMANDS
from backend.platform.events.publisher import SyncEventPublisher, event_bus_settings_from_env
from backend.platform.events.subscriber import SyncInternalSignalSubscriber, SyncSignalSubscription
from backend.platform.redis import SyncRedisClient
from backend.platform.redis.constants import CHANNEL_HARDWARE_EVENTS, KEY_DISK_TAINT, KEY_HARDWARE_GPU_STATE, KEY_SWITCH_PREFIX
from backend.platform.redis.runtime_state import hardware_state_key, sentinel_override_key
from backend.sentinel.sentinel_helpers import MountPoint

_RUNTIME_IO_EXCEPTIONS = (OSError, ValueError, KeyError, RuntimeError, TypeError)


@dataclass(slots=True)
class TopologyRuntimeIO:
    logger: logging.Logger | logging.LoggerAdapter | None = None
    redis_client: SyncRedisClient | None = None
    event_publisher: SyncEventPublisher | None = None
    signal_subscriber: SyncInternalSignalSubscriber | None = None

    def replace_redis(self, redis_client: SyncRedisClient | None) -> None:
        previous_redis = self.redis_client
        self.close(close_redis=False)
        self.redis_client = redis_client
        if previous_redis is not None and previous_redis is not redis_client:
            try:
                previous_redis.close()
            except _RUNTIME_IO_EXCEPTIONS:
                if self.logger is not None:
                    self.logger.debug("Previous redis close failed during rebind", exc_info=True)
        if redis_client is None:
            return
        self.event_publisher = SyncEventPublisher(
            settings=event_bus_settings_from_env(),
            redis=redis_client,
            logger=self.logger,
        )
        self.signal_subscriber = SyncInternalSignalSubscriber(redis_client)

    def close(self, *, close_redis: bool = True) -> None:
        if self.event_publisher is not None:
            try:
                self.event_publisher.close()
            except _RUNTIME_IO_EXCEPTIONS:
                if self.logger is not None:
                    self.logger.debug("Event publisher close failed", exc_info=True)
        self.event_publisher = None
        self.signal_subscriber = None
        if close_redis and self.redis_client is not None:
            try:
                self.redis_client.close()
            except _RUNTIME_IO_EXCEPTIONS:
                if self.logger is not None:
                    self.logger.debug("Redis close failed", exc_info=True)
            finally:
                self.redis_client = None

    def publish_control(self, subject: str, payload: dict[str, object]) -> bool:
        publisher = self.event_publisher
        if publisher is None:
            return False
        return publisher.publish_control(subject, json.dumps(payload))

    def publish_signal(self, subject: str, payload: dict[str, object]) -> int:
        publisher = self.event_publisher
        if publisher is None:
            return 0
        return publisher.publish_signal(subject, json.dumps(payload))

    def publish_route_meltdown(self, container_name: str) -> None:
        try:
            receiver_count = self.publish_signal(
                CHANNEL_ROUTING_MELTDOWN,
                {"container": container_name, "action": "route_remove"},
            )
            if receiver_count == 0 and self.logger is not None:
                self.logger.debug("Meltdown route signal emitted without active routing subscribers")
        except _RUNTIME_IO_EXCEPTIONS as exc:
            if self.logger is not None:
                self.logger.debug("Meltdown route event publish failed: %s", exc)

    def set_disk_taint(self) -> None:
        redis_client = self.redis_client
        if redis_client is None:
            return
        redis_client.kv.set(KEY_DISK_TAINT, "active", ex=300)

    def clear_disk_taint(self) -> None:
        redis_client = self.redis_client
        if redis_client is None:
            return
        redis_client.kv.delete(KEY_DISK_TAINT)

    def read_gpu_taints(self) -> set[str]:
        redis_client = self.redis_client
        if redis_client is None:
            return set()
        gpu_state_raw = redis_client.hashes.get_all(KEY_HARDWARE_GPU_STATE)
        if gpu_state_raw and gpu_state_raw.get("taint"):
            return {gpu_state_raw["taint"]}
        return set()

    def read_switch_base_state(self, switch_name: str) -> str | None:
        redis_client = self.redis_client
        if redis_client is None:
            return None
        data = redis_client.hashes.get_all(f"{KEY_SWITCH_PREFIX}{switch_name}")
        state = data.get("state")
        return str(state) if state else None

    def read_runtime_override(self, target_names: tuple[str, ...]) -> str | None:
        redis_client = self.redis_client
        if redis_client is None:
            return None
        for target_name in target_names:
            override = redis_client.kv.get(sentinel_override_key(target_name))
            if override:
                return str(override)
        return None

    def write_runtime_override(self, target_names: tuple[str, ...], state: str) -> None:
        redis_client = self.redis_client
        if redis_client is None:
            return
        for target_name in target_names:
            redis_client.kv.set(sentinel_override_key(target_name), state)

    def clear_runtime_override(self, target_names: tuple[str, ...]) -> None:
        redis_client = self.redis_client
        if redis_client is None or not target_names:
            return
        keys = [sentinel_override_key(name) for name in target_names]
        redis_client.kv.delete(*keys)

    def write_mount_state(self, mount: MountPoint, state: str, reason: str = "") -> None:
        redis_client = self.redis_client
        if redis_client is None:
            return
        key = hardware_state_key(str(mount.path))
        data: dict[str, str] = {
            "path": str(mount.path),
            "uuid": mount.expected_uuid or "",
            "state": state,
            "timestamp": str(time.time()),
            "reason": reason,
        }
        redis_client.hashes.set_mapping(key, data)
        event = build_hardware_state_event(
            str(mount.path),
            state,
            reason=reason,
            uuid_val=mount.expected_uuid,
            timestamp=float(data["timestamp"]),
        )
        if not self.publish_control(CHANNEL_HARDWARE_EVENTS, event) and self.logger is not None:
            self.logger.warning("Hardware control event publish did not complete for %s", mount.path)
        if self.logger is not None:
            self.logger.info("State updated %s: %s (%s)", mount.path, state, reason)

    def read_mount_state(self, mount: MountPoint) -> str | None:
        redis_client = self.redis_client
        if redis_client is None:
            return None
        current_state = redis_client.hashes.get(hardware_state_key(str(mount.path)), "state")
        if current_state:
            return str(current_state)
        return None

    def set_mount_pending_lock(self, mount: MountPoint, *, pending_ttl: int) -> None:
        redis_client = self.redis_client
        if redis_client is None:
            return
        redis_client.kv.setex(mount.pending_lock_key, pending_ttl, "PENDING")

    def clear_mount_pending_lock(self, mount: MountPoint) -> None:
        redis_client = self.redis_client
        if redis_client is None:
            return
        redis_client.kv.delete(mount.pending_lock_key)

    def write_gpu_state(self, gpu_state: dict[str, str]) -> None:
        redis_client = self.redis_client
        if redis_client is None:
            return
        redis_client.hashes.set_mapping(KEY_HARDWARE_GPU_STATE, gpu_state)

    def subscribe_switch_commands(self) -> SyncSignalSubscription | None:
        subscriber = self.signal_subscriber
        if subscriber is None:
            return None
        return subscriber.subscribe((CHANNEL_SWITCH_COMMANDS,))


__all__ = ("TopologyRuntimeIO",)
