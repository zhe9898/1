from __future__ import annotations

from copy import deepcopy
from typing import Any

import yaml

from backend.tests.unit._repo_paths import repo_path
from scripts.iac_core.loader import prepare_services


def _load_system_config() -> dict[str, Any]:
    return yaml.safe_load(repo_path("system.yaml").read_text(encoding="utf-8"))


def _render_services(profile: str, overrides: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    config = deepcopy(_load_system_config())
    config["deployment"] = dict(config.get("deployment") or {})
    config["deployment"]["profile"] = profile

    if overrides:
        for key, value in overrides.items():
            config[key] = value

    services = prepare_services(config)
    return {svc["name"]: svc for svc in services}


def test_gateway_kernel_profile_renders_minimal_service_set() -> None:
    services = _render_services("gateway-kernel")

    assert set(services) == {"caddy", "gateway", "postgres", "redis", "sentinel", "docker-proxy", "runner-agent"}
    assert "target: gateway-kernel" in services["gateway"]["build_block"]


def test_gateway_iot_profile_can_enable_mqtt_and_iot_image_target() -> None:
    config = deepcopy(_load_system_config())
    config["deployment"] = dict(config.get("deployment") or {})
    config["deployment"]["profile"] = "gateway-kernel"
    config["deployment"]["packs"] = ["iot-pack"]
    config["services"]["mosquitto"]["enabled"] = True

    services = {svc["name"]: svc for svc in prepare_services(config)}

    assert "mosquitto" in services
    assert "runner-agent" in services
    assert "target: gateway-iot" in services["gateway"]["build_block"]
