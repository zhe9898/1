"""Registered Redis runtime-state contracts for ephemeral control-plane state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from backend.platform.redis.constants import (
    KEY_DISK_TAINT,
    KEY_HARDWARE_GPU_STATE,
    KEY_HW_PREFIX,
    KEY_SENTINEL_OVERRIDE_PREFIX,
    KEY_SYSTEM_READONLY_DISK,
    KEY_SYSTEM_UPS_STATUS,
)

RuntimeStateRole = Literal["observation", "safety-interlock", "runtime-override"]
RuntimeKeyMatch = Literal["exact", "prefix"]


@dataclass(frozen=True, slots=True)
class RedisRuntimeStateSpec:
    pattern: str
    match: RuntimeKeyMatch
    role: RuntimeStateRole
    authoritative: bool
    decision_gate: bool
    description: str


def hardware_state_key(path: str) -> str:
    return f"{KEY_HW_PREFIX}{path}"


def sentinel_override_key(name: str) -> str:
    return f"{KEY_SENTINEL_OVERRIDE_PREFIX}{name}"


RUNTIME_STATE_SPECS: tuple[RedisRuntimeStateSpec, ...] = (
    RedisRuntimeStateSpec(
        pattern=KEY_SYSTEM_READONLY_DISK,
        match="exact",
        role="safety-interlock",
        authoritative=False,
        decision_gate=True,
        description="Ephemeral readonly lock asserted by safety guardians to reject mutating API traffic.",
    ),
    RedisRuntimeStateSpec(
        pattern=KEY_SYSTEM_UPS_STATUS,
        match="exact",
        role="safety-interlock",
        authoritative=False,
        decision_gate=True,
        description="Ephemeral UPS status snapshot used for write-protection gating during power emergencies.",
    ),
    RedisRuntimeStateSpec(
        pattern=KEY_DISK_TAINT,
        match="exact",
        role="safety-interlock",
        authoritative=False,
        decision_gate=True,
        description="Ephemeral disk-pressure taint latch used by sentinel and middleware safety paths.",
    ),
    RedisRuntimeStateSpec(
        pattern=KEY_HARDWARE_GPU_STATE,
        match="exact",
        role="observation",
        authoritative=False,
        decision_gate=True,
        description="Ephemeral GPU probe snapshot and taint metadata produced by topology sentinel.",
    ),
    RedisRuntimeStateSpec(
        pattern=KEY_HW_PREFIX,
        match="prefix",
        role="observation",
        authoritative=False,
        decision_gate=True,
        description="Ephemeral hardware probe snapshots for mount and device health state.",
    ),
    RedisRuntimeStateSpec(
        pattern=KEY_SENTINEL_OVERRIDE_PREFIX,
        match="prefix",
        role="runtime-override",
        authoritative=False,
        decision_gate=True,
        description="Ephemeral sentinel safety overrides that temporarily suppress container intent without becoming durable authority.",
    ),
)


def match_runtime_state_spec(key: str) -> RedisRuntimeStateSpec | None:
    for spec in RUNTIME_STATE_SPECS:
        if spec.match == "exact" and key == spec.pattern:
            return spec
        if spec.match == "prefix" and key.startswith(spec.pattern):
            return spec
    return None


def is_runtime_state_key(key: str) -> bool:
    return match_runtime_state_spec(key) is not None


def export_runtime_state_contract() -> dict[str, object]:
    return {
        "authoritative_redis_runtime_state_allowed": False,
        "redis_ephemeral_runtime_state": [
            {
                "pattern": spec.pattern,
                "match": spec.match,
                "role": spec.role,
                "authoritative": spec.authoritative,
                "decision_gate": spec.decision_gate,
                "description": spec.description,
            }
            for spec in RUNTIME_STATE_SPECS
        ],
    }


__all__ = (
    "RUNTIME_STATE_SPECS",
    "RedisRuntimeStateSpec",
    "export_runtime_state_contract",
    "hardware_state_key",
    "is_runtime_state_key",
    "match_runtime_state_spec",
    "sentinel_override_key",
)
