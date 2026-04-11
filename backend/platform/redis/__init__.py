from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.platform.redis.constants import (
    CHANNEL_CONNECTOR_EVENTS,
    CHANNEL_HARDWARE_EVENTS,
    CHANNEL_JOB_EVENTS,
    CHANNEL_NODE_EVENTS,
    CHANNEL_RESERVATION_EVENTS,
    CHANNEL_SESSION_EVENTS,
    CHANNEL_SWITCH_EVENTS,
    CHANNEL_TRIGGER_EVENTS,
    CHANNEL_USER_EVENTS,
    KEY_AUTH_CHALLENGE_PREFIX,
    KEY_CAPABILITIES,
    KEY_CAPABILITIES_PREFIX,
    KEY_DB_MIGRATION_LOCK,
    KEY_DISK_TAINT,
    KEY_HARDWARE_GPU_STATE,
    KEY_HW_PREFIX,
    KEY_INVITE_TOKEN_PREFIX,
    KEY_LOCK_PREFIX,
    KEY_NODE_PREFIX,
    KEY_SENTINEL_OVERRIDE_PREFIX,
    KEY_SWITCH_PREFIX,
    KEY_SYSTEM_READONLY_DISK,
    KEY_SYSTEM_UPS_STATUS,
)
from backend.platform.redis.runtime_state import export_runtime_state_contract
from backend.platform.redis.types import Capability, HardwareState, NodeInfo, SwitchState

if TYPE_CHECKING:
    from backend.platform.redis.client import RedisClient
    from backend.platform.redis.sync_client import SyncRedisClient


def __getattr__(name: str) -> Any:
    if name == "RedisClient":
        from backend.platform.redis.client import RedisClient

        return RedisClient
    if name == "SyncRedisClient":
        from backend.platform.redis.sync_client import SyncRedisClient

        return SyncRedisClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = (
    "CHANNEL_CONNECTOR_EVENTS",
    "CHANNEL_HARDWARE_EVENTS",
    "CHANNEL_JOB_EVENTS",
    "CHANNEL_NODE_EVENTS",
    "CHANNEL_RESERVATION_EVENTS",
    "CHANNEL_SESSION_EVENTS",
    "CHANNEL_SWITCH_EVENTS",
    "CHANNEL_TRIGGER_EVENTS",
    "CHANNEL_USER_EVENTS",
    "Capability",
    "HardwareState",
    "KEY_AUTH_CHALLENGE_PREFIX",
    "KEY_CAPABILITIES",
    "KEY_CAPABILITIES_PREFIX",
    "KEY_DB_MIGRATION_LOCK",
    "KEY_DISK_TAINT",
    "KEY_HARDWARE_GPU_STATE",
    "KEY_HW_PREFIX",
    "KEY_INVITE_TOKEN_PREFIX",
    "KEY_LOCK_PREFIX",
    "KEY_NODE_PREFIX",
    "KEY_SENTINEL_OVERRIDE_PREFIX",
    "KEY_SWITCH_PREFIX",
    "KEY_SYSTEM_READONLY_DISK",
    "KEY_SYSTEM_UPS_STATUS",
    "NodeInfo",
    "RedisClient",
    "SyncRedisClient",
    "SwitchState",
    "export_runtime_state_contract",
)
