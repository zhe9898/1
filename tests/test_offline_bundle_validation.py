from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml


REQUIRED_KERNEL_PATHS = (
    "/api/v1/profile",
    "/api/v1/console/overview",
    "/api/v1/nodes",
    "/api/v1/jobs",
    "/api/v1/connectors",
    "/api/v1/settings/schema",
)


def _write_minimal_bundle(root: Path) -> None:
    (root / "backend" / "models").mkdir(parents=True, exist_ok=True)
    (root / "frontend").mkdir(parents=True)
    (root / "config").mkdir(parents=True)
    (root / "deploy").mkdir(parents=True)
    (root / "docs" / "api").mkdir(parents=True)
    (root / "contracts" / "openapi").mkdir(parents=True)

    system_config = {
        "deployment": {
            "product": "ZEN70 Gateway Kernel",
            "profile": "gateway-kernel",
            "packs": [],
            "available_profiles": ["gateway-kernel"],
            "available_packs": ["iot-pack", "ops-pack", "health-pack", "vector-pack"],

        }
    }
    manifest = {
        "product": "ZEN70 Gateway Kernel",
        "profile": "gateway-kernel",
        "requested_packs": [],
        "resolved_packs": [],
        "services_rendered": ["caddy", "gateway", "postgres", "redis", "runner-agent", "sentinel", "docker-proxy"],
    }
    compose = {
        "services": {name: {} for name in manifest["services_rendered"]},
    }
    openapi = {
        "openapi": "3.1.0",
        "info": {"title": "ZEN70 API", "version": "1.58.0"},
        "paths": {path: {} for path in REQUIRED_KERNEL_PATHS},
    }

    (root / "system.yaml").write_text(yaml.safe_dump(system_config, sort_keys=False, allow_unicode=True), encoding="utf-8")
    (root / "backend" / "requirements-ci.lock").write_text("--hash=sha256:deadbeef\n", encoding="utf-8")
    (root / "backend" / "models" / "domain_registry.json").write_text(json.dumps({"domains": []}, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "frontend" / "package-lock.json").write_text("{\"lockfileVersion\": 3}\n", encoding="utf-8")
    (root / "render-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "docker-compose.yml").write_text(yaml.safe_dump(compose, sort_keys=False, allow_unicode=True), encoding="utf-8")
    (root / "config" / "Caddyfile").write_text(
        "https://{$MACHINE_API_INTERNAL_HOST:caddy} {\n  tls internal\n}\n",
        encoding="utf-8",
    )
    (root / "deploy" / "images.list").write_text(
        "python:3.11-slim@sha256:" + ("0" * 64) + "\n",
        encoding="utf-8",
    )
    (root / "docs" / "openapi-kernel.json").write_text(json.dumps(openapi, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "docs" / "api" / "openapi_locked.json").write_text(json.dumps(openapi, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "contracts" / "openapi" / "zen70-gateway-kernel.openapi.json").write_text(
        json.dumps(openapi, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _run_validator(bundle_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/validate_offline_bundle.py", str(bundle_root)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def test_validate_offline_bundle_accepts_clean_bundle(tmp_path: Path) -> None:
    _write_minimal_bundle(tmp_path)
    result = _run_validator(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_validate_offline_bundle_rejects_forbidden_artifacts(tmp_path: Path) -> None:
    _write_minimal_bundle(tmp_path)
    (tmp_path / "frontend" / "build_final.txt").write_text("noise\n", encoding="utf-8")

    result = _run_validator(tmp_path)
    assert result.returncode != 0
    assert "forbidden artifact present" in (result.stdout + result.stderr)


def test_validate_offline_bundle_rejects_manifest_profile_drift(tmp_path: Path) -> None:
    _write_minimal_bundle(tmp_path)
    manifest = json.loads((tmp_path / "render-manifest.json").read_text(encoding="utf-8"))
    manifest["profile"] = "gateway-typo"
    (tmp_path / "render-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    result = _run_validator(tmp_path)
    assert result.returncode != 0
    assert "render-manifest.json profile does not match" in (result.stdout + result.stderr)


def test_validate_offline_bundle_rejects_compose_manifest_service_drift(tmp_path: Path) -> None:
    _write_minimal_bundle(tmp_path)
    compose = yaml.safe_load((tmp_path / "docker-compose.yml").read_text(encoding="utf-8"))
    compose["services"]["grafana"] = {}
    (tmp_path / "docker-compose.yml").write_text(yaml.safe_dump(compose, sort_keys=False, allow_unicode=True), encoding="utf-8")

    result = _run_validator(tmp_path)
    assert result.returncode != 0
    assert "services do not match render-manifest.json services_rendered" in (result.stdout + result.stderr)


def test_validate_offline_bundle_rejects_openapi_contract_drift(tmp_path: Path) -> None:
    _write_minimal_bundle(tmp_path)
    openapi = json.loads((tmp_path / "docs" / "openapi-kernel.json").read_text(encoding="utf-8"))
    openapi["paths"].pop("/api/v1/profile")
    (tmp_path / "docs" / "openapi-kernel.json").write_text(json.dumps(openapi, ensure_ascii=False, indent=2), encoding="utf-8")

    result = _run_validator(tmp_path)
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert (
        "docs/openapi-kernel.json and contracts/openapi/zen70-gateway-kernel.openapi.json must match" in combined
        or "docs/openapi-kernel.json is missing required kernel paths" in combined
    )
