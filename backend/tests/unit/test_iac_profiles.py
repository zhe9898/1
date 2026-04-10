from __future__ import annotations

from copy import deepcopy
from typing import Any

import yaml

from backend.tests.unit._repo_paths import repo_path
from scripts.iac_core.loader import prepare_host_services, prepare_services


def _load_system_config() -> dict[str, Any]:
    return yaml.safe_load(repo_path("system.yaml").read_text(encoding="utf-8"))


def _render_services(profile: str, overrides: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    config = deepcopy(_load_system_config())
    config["deployment"] = dict(config.get("deployment") or {})
    config["deployment"]["profile"] = profile
    config["__project_root__"] = str(repo_path())

    if overrides:
        for key, value in overrides.items():
            config[key] = value

    services = prepare_services(config)
    return {svc["name"]: svc for svc in services}


def test_gateway_kernel_profile_renders_minimal_service_set() -> None:
    services = _render_services("gateway-kernel")

    assert set(services) == {"caddy", "postgres", "redis", "nats"}


def test_gateway_kernel_profile_renders_host_control_plane_services() -> None:
    config = deepcopy(_load_system_config())
    config["deployment"] = dict(config.get("deployment") or {})
    config["deployment"]["profile"] = "gateway-kernel"
    config["__project_root__"] = str(repo_path())
    config["__output_root__"] = str(repo_path())

    host_services = {svc["name"]: svc for svc in prepare_host_services(config)}

    assert set(host_services) == {"gateway", "topology-sentinel", "control-worker", "routing-operator", "runner-agent"}
    assert host_services["gateway"]["port"] == 8000
    assert "python3 -m uvicorn" in host_services["gateway"]["exec_start"]
    assert "--workers 2" in host_services["gateway"]["exec_start"]
    assert "--timeout-graceful-shutdown 15" in host_services["gateway"]["exec_start"]
    assert "bash -lc" not in host_services["gateway"]["exec_start"]
    assert host_services["gateway"]["environment_file"].endswith(".env")
    runner_exec_start = host_services["runner-agent"]["exec_start"].replace("\\\\", "/").replace("\\", "/")
    assert "runtime/host/bin/runner-agent" in runner_exec_start
    assert host_services["runner-agent"]["build_plan"]["kind"] == "go_binary"


def test_explicit_iot_pack_can_enable_mqtt_and_iot_image_target() -> None:
    config = deepcopy(_load_system_config())
    config["deployment"] = dict(config.get("deployment") or {})
    config["deployment"]["profile"] = "gateway-kernel"
    config["deployment"]["packs"] = ["iot-pack"]
    config["__project_root__"] = str(repo_path())
    config["services"]["mosquitto"]["enabled"] = True

    services = {svc["name"]: svc for svc in prepare_services(config)}

    assert "mosquitto" in services
    assert "runner-agent" not in services
