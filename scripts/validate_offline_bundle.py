from __future__ import annotations

import argparse
import json
import sys
from fnmatch import fnmatch
from pathlib import Path
from typing import Iterable

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.iac_core.profiles import HOST_FIRST_DEPLOYMENT_MODEL


# ── Source-layer required files ─────────────────────────────────────────────
# Must exist in the release zip. Checked unconditionally during post-package validation.
REQUIRED_FILES = (
    "backend/requirements-ci.lock",
    "frontend/package-lock.json",
    "docker-compose.yml",
    "backend/models/domain_registry.json",
    "docs/api/openapi_locked.json",    # ADR-0047: path-surface snapshot
)

# ── Deploy-layer required files ──────────────────────────────────────────────
# Generated at deploy/render time. Only checked when present in bundle root.
# Missing = not yet rendered (acceptable for source-only packaging).
REQUIRED_DEPLOY_FILES = (
    "system.yaml",
    "render-manifest.json",
    "docs/openapi-kernel.json",
    "contracts/openapi/zen70-gateway-kernel.openapi.json",
    "config/Caddyfile",
    "deploy/images.list",
)

FORBIDDEN_PATTERNS = (
    "frontend/build_*.txt",
    "frontend/eslint_*.txt",
    "frontend/vuetsc_*.txt",
    "frontend/full_build_*.txt",
    "frontend/test_output.txt",
    "frontend/test_result*.json",
    "config/system.yaml",
    "config/users.acl",
    "runtime/secrets/*",
    "runtime/tmp-compile/*",
)

REQUIRED_KERNEL_PATHS = (
    "/api/v1/profile",
    "/api/v1/console/overview",
    "/api/v1/nodes",
    "/api/v1/jobs",
    "/api/v1/connectors",
    "/api/v1/settings/schema",
)


def _collect_files(root: Path) -> list[str]:
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    )


def _matches_any(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch(path, pattern) for pattern in patterns)


def _load_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must parse to an object")
    return data


def _load_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must parse to an object")
    return data


def _validate_runtime_contract_consistency(root: Path, issues: list[str]) -> None:
    system_yaml = _load_yaml(root / "system.yaml")
    deployment = system_yaml.get("deployment") or {}
    manifest = _load_json(root / "render-manifest.json")
    compose = _load_yaml(root / "docker-compose.yml")
    openapi_docs = _load_json(root / "docs" / "openapi-kernel.json")
    openapi_contract = _load_json(root / "contracts" / "openapi" / "zen70-gateway-kernel.openapi.json")
    caddyfile = (root / "config" / "Caddyfile").read_text(encoding="utf-8")

    if deployment.get("profile") != "gateway-kernel":
        issues.append(f"system.yaml deployment.profile must be gateway-kernel: {deployment.get('profile')}")
    if deployment.get("available_profiles") != ["gateway-kernel"]:
        issues.append(f"system.yaml available_profiles must be ['gateway-kernel']: {deployment.get('available_profiles')}")

    if manifest.get("product") != deployment.get("product"):
        issues.append(
            "render-manifest.json product does not match system.yaml deployment.product: "
            f"{manifest.get('product')} != {deployment.get('product')}"
        )
    if manifest.get("profile") != deployment.get("profile"):
        issues.append(
            "render-manifest.json profile does not match system.yaml deployment.profile: "
            f"{manifest.get('profile')} != {deployment.get('profile')}"
        )
    if manifest.get("requested_packs") != deployment.get("packs", []):
        issues.append(
            "render-manifest.json requested_packs does not match system.yaml deployment.packs: "
            f"{manifest.get('requested_packs')} != {deployment.get('packs', [])}"
        )

    if manifest.get("deployment_model") != HOST_FIRST_DEPLOYMENT_MODEL:
        issues.append(
            "render-manifest.json deployment_model must be host-first: "
            f"{manifest.get('deployment_model')}"
        )

    compose_services = sorted((compose.get("services") or {}).keys())
    rendered_containers = sorted(manifest.get("container_services_rendered") or [])
    infrastructure_containers = sorted(manifest.get("infrastructure_containers_rendered") or [])
    optional_pack_containers = sorted(manifest.get("optional_pack_containers_rendered") or [])
    host_processes = sorted(manifest.get("host_processes_rendered") or [])
    runtime_services = sorted(manifest.get("runtime_services_rendered") or [])
    migration_copy_plan = manifest.get("migration_copy_plan") or {}

    if compose_services != rendered_containers:
        issues.append(
            "docker-compose.yml services do not match render-manifest.json container_services_rendered: "
            f"{compose_services} != {rendered_containers}"
        )
    if sorted(infrastructure_containers + optional_pack_containers) != rendered_containers:
        issues.append(
            "render-manifest.json container copy classes do not add up to container_services_rendered: "
            f"{infrastructure_containers} + {optional_pack_containers} != {rendered_containers}"
        )
    if sorted(set(rendered_containers) | set(host_processes)) != runtime_services:
        issues.append(
            "render-manifest.json runtime_services_rendered does not match host/container union: "
            f"{runtime_services}"
        )
    if sorted(migration_copy_plan.get("host_processes") or []) != host_processes:
        issues.append(
            "render-manifest.json migration_copy_plan.host_processes does not match host_processes_rendered: "
            f"{migration_copy_plan.get('host_processes')} != {host_processes}"
        )
    if sorted(migration_copy_plan.get("infrastructure_containers") or []) != infrastructure_containers:
        issues.append(
            "render-manifest.json migration_copy_plan.infrastructure_containers does not match "
            f"infrastructure_containers_rendered: {migration_copy_plan.get('infrastructure_containers')} "
            f"!= {infrastructure_containers}"
        )
    if sorted(migration_copy_plan.get("optional_pack_containers") or []) != optional_pack_containers:
        issues.append(
            "render-manifest.json migration_copy_plan.optional_pack_containers does not match "
            f"optional_pack_containers_rendered: {migration_copy_plan.get('optional_pack_containers')} "
            f"!= {optional_pack_containers}"
        )

    if openapi_docs != openapi_contract:
        issues.append("docs/openapi-kernel.json and contracts/openapi/zen70-gateway-kernel.openapi.json must match")

    openapi_paths = openapi_docs.get("paths") or {}
    missing_paths = [path for path in REQUIRED_KERNEL_PATHS if path not in openapi_paths]
    if missing_paths:
        issues.append(f"docs/openapi-kernel.json is missing required kernel paths: {missing_paths}")

    if "https://{$MACHINE_API_INTERNAL_HOST:caddy}" not in caddyfile:
        issues.append("config/Caddyfile must expose the internal machine TLS site")
    if "tls internal" not in caddyfile:
        issues.append("config/Caddyfile must enable tls internal for machine traffic")


def validate_bundle(root: Path) -> list[str]:
    issues: list[str] = []
    if not root.exists():
        return [f"bundle root does not exist: {root}"]
    if not root.is_dir():
        return [f"bundle root is not a directory: {root}"]

    files = _collect_files(root)
    for required in REQUIRED_FILES:
        if not (root / required).exists():
            issues.append(f"missing required file: {required}")

    for rel in files:
        if _matches_any(rel, FORBIDDEN_PATTERNS):
            issues.append(f"forbidden artifact present: {rel}")

    images_list = root / "deploy" / "images.list"
    if images_list.exists():
        for line_number, raw_line in enumerate(images_list.read_text(encoding="utf-8").splitlines(), 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "@sha256:" not in line:
                issues.append(f"deploy/images.list:{line_number} is not digest pinned: {line}")

    if not issues:
        # Runtime contract consistency check only runs if all deployment artifacts exist.
        # These are generated at deploy time, not at build time.
        required_for_consistency = [
            root / "system.yaml",
            root / "render-manifest.json",
            root / "docs" / "openapi-kernel.json",
            root / "contracts" / "openapi" / "zen70-gateway-kernel.openapi.json",
        ]
        if all(p.exists() for p in required_for_consistency):
            try:
                _validate_runtime_contract_consistency(root, issues)
            except (OSError, ValueError, KeyError, json.JSONDecodeError, yaml.YAMLError) as exc:
                issues.append(f"bundle consistency validation failed: {exc}")

    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate offline bundle contents.")
    parser.add_argument("bundle_root", type=Path, help="Path to the staged offline bundle root")
    args = parser.parse_args()

    issues = validate_bundle(args.bundle_root)
    if issues:
        for issue in issues:
            print(f"FATAL: {issue}")
        return 1
    print(f"OK: offline bundle validated at {args.bundle_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
