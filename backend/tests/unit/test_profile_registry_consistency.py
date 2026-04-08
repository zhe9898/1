from __future__ import annotations

import yaml

from backend.kernel.profiles.public_profile import DEFAULT_PRODUCT_NAME
from backend.kernel.profiles.public_profile import PROFILE_ALIASES as BACKEND_PROFILE_ALIASES
from backend.tests.unit._repo_paths import repo_path
from scripts.iac_core.profiles import PROFILE_ALIASES as IAC_PROFILE_ALIASES


def test_backend_and_iac_profile_aliases_are_consistent() -> None:
    for alias, canonical in BACKEND_PROFILE_ALIASES.items():
        assert alias in IAC_PROFILE_ALIASES
        assert IAC_PROFILE_ALIASES[alias] == canonical


def test_system_yaml_product_matches_backend_constant() -> None:
    config = yaml.safe_load(repo_path("system.yaml").read_text(encoding="utf-8"))
    assert config["deployment"]["product"] == DEFAULT_PRODUCT_NAME
    assert config["deployment"]["packs"] == []
    assert config["deployment"]["available_packs"] == [
        "iot-pack",
        "ops-pack",
        "media-pack",
        "health-pack",
        "vector-pack",
    ]


def test_system_yaml_default_kernel_has_no_plaintext_tunnel_or_phantom_switches() -> None:
    config = yaml.safe_load(repo_path("system.yaml").read_text(encoding="utf-8"))
    assert config.get("secrets") == {}
    sentinel = config.get("sentinel") or {}
    assert sentinel.get("mount_container_map") == {}
    assert sentinel.get("switch_container_map") == {}
    assert sentinel.get("switch_service_ports") == {}
