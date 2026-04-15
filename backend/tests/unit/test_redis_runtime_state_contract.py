from __future__ import annotations

from backend.platform.redis.constants import (
    KEY_DISK_TAINT,
    KEY_HARDWARE_GPU_STATE,
    KEY_SYSTEM_READONLY_DISK,
    KEY_SYSTEM_UPS_STATUS,
)
from backend.platform.redis.runtime_state import (
    RUNTIME_STATE_SPECS,
    hardware_state_key,
    is_runtime_state_key,
    match_runtime_state_spec,
    sentinel_override_key,
)


def test_runtime_state_contract_registers_known_ephemeral_keys() -> None:
    assert match_runtime_state_spec(KEY_SYSTEM_READONLY_DISK) is not None
    assert match_runtime_state_spec(KEY_SYSTEM_UPS_STATUS) is not None
    assert match_runtime_state_spec(KEY_DISK_TAINT) is not None
    assert match_runtime_state_spec(KEY_HARDWARE_GPU_STATE) is not None
    assert match_runtime_state_spec(hardware_state_key("/mnt/media")) is not None
    assert match_runtime_state_spec(sentinel_override_key("media")) is not None


def test_runtime_state_contract_marks_registered_keys_as_non_authoritative() -> None:
    assert RUNTIME_STATE_SPECS
    assert all(spec.authoritative is False for spec in RUNTIME_STATE_SPECS)


def test_switch_expected_key_family_is_no_longer_registered_runtime_authority() -> None:
    assert is_runtime_state_key("switch_expected:media") is False
