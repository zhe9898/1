#!/usr/bin/env python3
"""Host-runtime asset materialization helpers."""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


def prepare_host_runtime_assets(config_path: Path, output_dir: Path, *, project_root: Path) -> None:
    """Materialize runtime assets required by ``runtime: host`` services."""
    if platform.system() != "Linux":
        logger.debug("Skipping host runtime asset materialization outside Linux")
        return
    if not config_path.exists():
        return

    for svc in _load_enabled_host_service_specs(config_path, output_dir, project_root=project_root):
        build_plan = svc.get("build_plan")
        if isinstance(build_plan, dict):
            _materialize_host_build_plan(str(svc.get("name") or ""), build_plan)


def _load_enabled_host_service_specs(
    config_path: Path,
    output_dir: Path,
    *,
    project_root: Path,
) -> list[dict[str, object]]:
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"failed to parse host runtime config: {exc}") from exc
    if not isinstance(data, dict):
        return []

    from scripts.iac_core.loader import prepare_host_services

    data["__project_root__"] = str(project_root.resolve())
    data["__output_root__"] = str(output_dir.resolve())
    return prepare_host_services(data, output_root=output_dir)


def _materialize_host_build_plan(service_name: str, build_plan: dict[str, object]) -> None:
    build_kind = str(build_plan.get("kind") or "").strip()
    if build_kind != "go_binary":
        raise RuntimeError(f"unsupported host build kind for {service_name}: {build_kind or '<empty>'}")

    source_dir = Path(str(build_plan.get("source_dir") or "")).expanduser().resolve()
    output_path = Path(str(build_plan.get("output_path") or "")).expanduser().resolve()
    package = str(build_plan.get("package") or "").strip()
    env_overrides = build_plan.get("env")
    trimpath = bool(build_plan.get("trimpath", True))
    ldflags = str(build_plan.get("ldflags") or "-s -w").strip() or "-s -w"

    if not source_dir.exists():
        raise RuntimeError(f"host build source dir for {service_name} does not exist: {source_dir}")
    if not package:
        raise RuntimeError(f"host build package for {service_name} is missing")

    if not _host_build_needs_refresh(source_dir, output_path):
        logger.info("[host] reuse existing runtime artifact for %s: %s", service_name, output_path)
        return

    go_bin = shutil.which("go")
    if not go_bin:
        raise RuntimeError(
            f"host service {service_name} requires Go to build {output_path.name}, but 'go' was not found in PATH"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [go_bin, "build"]
    if trimpath:
        command.append("-trimpath")
    if ldflags:
        command.extend(["-ldflags", ldflags])
    command.extend(["-o", str(output_path), package])

    env = os.environ.copy()
    if isinstance(env_overrides, dict):
        env.update({str(key): str(value) for key, value in env_overrides.items()})

    logger.info("[host] building runtime artifact for %s -> %s", service_name, output_path)
    try:
        subprocess.run(
            command,
            cwd=str(source_dir),
            env=env,
            check=True,
            timeout=180,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise RuntimeError(f"failed to build host runtime artifact for {service_name}: {detail}") from exc
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise RuntimeError(f"failed to build host runtime artifact for {service_name}: {exc}") from exc

    try:
        os.chmod(output_path, 0o755)
    except OSError:
        logger.debug("[host] chmod skipped for %s", output_path, exc_info=True)


def _host_build_needs_refresh(source_dir: Path, output_path: Path) -> bool:
    if not output_path.exists():
        return True
    try:
        return output_path.stat().st_mtime < _latest_tree_mtime(source_dir)
    except OSError:
        return True


def _latest_tree_mtime(root: Path) -> float:
    latest = root.stat().st_mtime
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            latest = max(latest, path.stat().st_mtime)
        except OSError:
            continue
    return latest
