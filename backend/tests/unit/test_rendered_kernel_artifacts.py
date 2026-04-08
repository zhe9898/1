from __future__ import annotations

import json
from pathlib import Path

import yaml

from backend.kernel.profiles.public_profile import DEFAULT_PRODUCT_NAME

REPO_ROOT = Path(__file__).resolve().parents[3]

KERNEL_SERVICES = {
    "caddy",
    "docker-proxy",
    "gateway",
    "postgres",
    "redis",
    "runner-agent",
    "sentinel",
}

FORBIDDEN_KERNEL_SERVICES = {
    "alertmanager",
    "categraf",
    "cloudflared",
    "grafana",
    "jellyfin",
    "loki",
    "mosquitto",
    "pgbouncer",
    "promtail",
    "victoriametrics",
    "vmalert",
    "watchdog",
}


def test_render_manifest_matches_kernel_source_of_truth() -> None:
    system_config = yaml.safe_load((REPO_ROOT / "system.yaml").read_text(encoding="utf-8"))
    manifest = json.loads((REPO_ROOT / "render-manifest.json").read_text(encoding="utf-8"))

    assert system_config["deployment"]["product"] == DEFAULT_PRODUCT_NAME
    assert system_config["deployment"]["profile"] == "gateway-kernel"
    assert manifest["product"] == DEFAULT_PRODUCT_NAME
    assert manifest["profile"] == "gateway-kernel"
    assert manifest["requested_packs"] == []
    assert manifest["resolved_packs"] == []
    assert manifest["gateway_image_target"] == "gateway-kernel"
    assert manifest["policy_injections"] == []
    assert manifest["tier3_warnings"] == []
    assert set(manifest["services_rendered"]) == KERNEL_SERVICES
    assert set(manifest["services_rendered"]).isdisjoint(FORBIDDEN_KERNEL_SERVICES)


def test_rendered_docker_compose_stays_on_kernel_service_set() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    manifest = json.loads((REPO_ROOT / "render-manifest.json").read_text(encoding="utf-8"))

    services = set((compose.get("services") or {}).keys())
    assert services == KERNEL_SERVICES
    assert services == set(manifest["services_rendered"])
    assert services.isdisjoint(FORBIDDEN_KERNEL_SERVICES)
    assert compose["services"]["gateway"]["build"]["target"] == "gateway-kernel"
    assert compose["services"]["sentinel"]["command"] == [
        "python",
        "backend/sentinel/control_plane_supervisor.py",
    ]
    sentinel_env = compose["services"]["sentinel"]["environment"]
    sentinel_volumes = compose["services"]["sentinel"]["volumes"]
    gateway_env = compose["services"]["gateway"]["environment"]
    runner_env = compose["services"]["runner-agent"]["environment"]
    runner_volumes = compose["services"]["runner-agent"]["volumes"]
    caddy_env = compose["services"]["caddy"]["environment"]
    assert "GATEWAY_PACKS=${GATEWAY_PACKS}" in gateway_env
    assert "BITROT_SCAN_DIRS=${BITROT_SCAN_DIRS}" in sentinel_env
    assert "SWITCH_SERVICE_PORTS=${SWITCH_SERVICE_PORTS}" in sentinel_env
    assert "ROUTING_STATE_FILE=/app/runtime/control-plane/routes.json" in sentinel_env
    assert "CADDY_ADMIN_URL=http://caddy:2019/load" in sentinel_env
    assert "MACHINE_API_INTERNAL_HOST=${MACHINE_API_INTERNAL_HOST:-caddy}" in caddy_env
    assert "GATEWAY_BASE_URL=https://${MACHINE_API_INTERNAL_HOST:-caddy}" in runner_env
    assert "GATEWAY_CA_FILE=/caddy-data/caddy/pki/authorities/local/root.crt" in runner_env
    assert "caddy_data:/caddy-data:ro" in runner_volumes
    assert "./scripts:/app/scripts:ro" in sentinel_volumes
    assert "./iac:/app/iac:ro" in sentinel_volumes
    assert "./system.yaml:/app/system.yaml:ro" in sentinel_volumes
    assert "./config:/app/config:ro" in sentinel_volumes
    assert "./runtime/control-plane:/app/runtime/control-plane" in sentinel_volumes
