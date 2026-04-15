from __future__ import annotations

import json
from pathlib import Path

import yaml

from backend.kernel.profiles.public_profile import DEFAULT_PRODUCT_NAME
from scripts.iac_core.profiles import HOST_FIRST_DEPLOYMENT_MODEL

REPO_ROOT = Path(__file__).resolve().parents[3]

KERNEL_COMPOSE_SERVICES = {
    "caddy",
    "nats",
    "postgres",
    "redis",
}

KERNEL_HOST_SERVICES = {
    "control-worker",
    "gateway",
    "routing-operator",
    "runner-agent",
    "topology-sentinel",
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
    assert manifest["deployment_model"] == HOST_FIRST_DEPLOYMENT_MODEL
    assert manifest["policy_injections"] == []
    assert manifest["tier3_warnings"] == []
    assert set(manifest["container_services_rendered"]) == KERNEL_COMPOSE_SERVICES
    assert set(manifest["infrastructure_containers_rendered"]) == KERNEL_COMPOSE_SERVICES
    assert manifest["optional_pack_containers_rendered"] == []
    assert set(manifest["host_processes_rendered"]) == KERNEL_HOST_SERVICES
    assert set(manifest["runtime_services_rendered"]) == KERNEL_COMPOSE_SERVICES | KERNEL_HOST_SERVICES
    assert manifest["migration_copy_plan"] == {
        "host_processes": sorted(KERNEL_HOST_SERVICES),
        "infrastructure_containers": sorted(KERNEL_COMPOSE_SERVICES),
        "optional_pack_containers": [],
    }
    assert set(manifest["container_services_rendered"]).isdisjoint(FORBIDDEN_KERNEL_SERVICES)


def test_rendered_docker_compose_stays_on_kernel_service_set() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    manifest = json.loads((REPO_ROOT / "render-manifest.json").read_text(encoding="utf-8"))

    services = set((compose.get("services") or {}).keys())
    assert services == KERNEL_COMPOSE_SERVICES
    assert services == set(manifest["container_services_rendered"])
    assert services.isdisjoint(FORBIDDEN_KERNEL_SERVICES)
    caddy_env = compose["services"]["caddy"]["environment"]
    caddy_extra_hosts = compose["services"]["caddy"]["extra_hosts"]
    postgres_ports = compose["services"]["postgres"]["ports"]
    redis_ports = compose["services"]["redis"]["ports"]
    nats_ports = compose["services"]["nats"]["ports"]
    assert "MACHINE_API_INTERNAL_HOST=${MACHINE_API_INTERNAL_HOST:-caddy}" in caddy_env
    assert "GATEWAY_UPSTREAM=${GATEWAY_UPSTREAM}" in caddy_env
    assert "host.docker.internal:host-gateway" in caddy_extra_hosts
    assert "127.0.0.1:5432:5432" in postgres_ports
    assert "127.0.0.1:6379:6379" in redis_ports
    assert "127.0.0.1:4222:4222" in nats_ports
