#!/usr/bin/env python3
"""
Shared utilities for ZEN70 deployment scripts.

Consolidates common helpers used by both ``bootstrap.py`` (Day-0 cold start)
and ``deployer.py`` (Day-N update / rollback).  All functions are thin,
side-effect-free path helpers or Docker CLI wrappers — safe to import from
any deployment entry-point.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def project_root() -> Path:
    """Return the project root (parent of the ``scripts/`` directory).

    Cross-platform, dynamic resolution via ``pathlib``.
    Never hard-code absolute paths — always derive from this function.
    """
    return Path(__file__).resolve().parent.parent


def scripts_dir() -> Path:
    """Return the ``scripts/`` directory."""
    return Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Docker compose conflict resolution
# ---------------------------------------------------------------------------


def resolve_name_conflict(root: Path, stderr: str) -> None:
    """Extract a conflicting container name from *stderr* and fix it.

    Strategy (per §1.2):
      - Container belongs to another compose project → ``compose -p <old> down``
      - Container has no compose label (manual creation) → ``docker rm -f``
      - Container belongs to current project (should not happen) → ``docker rm -f``
    """
    match = re.search(r'container name ["\'/]*(zen70-[a-zA-Z0-9_-]+)', stderr)
    if not match:
        logger.warning("[冲突修复] 无法从 stderr 提取冲突容器名")
        return

    cname = match.group(1)
    logger.info("[冲突修复] 冲突容器: %s", cname)

    old_project = _inspect_compose_project(root, cname)

    if old_project and old_project != "zen70":
        logger.info(
            "[冲突修复] 容器属于旧 project '%s'，执行 compose -p %s down...",
            old_project,
            old_project,
        )
        try:
            subprocess.run(
                ["docker", "compose", "-p", old_project, "down", "--remove-orphans"],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(root),
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.warning("[冲突修复] down 旧 project 失败: %s，降级 rm -f", e)
            _force_remove(root, cname)
    else:
        logger.info("[冲突修复] rm -f %s（无 project label 或状态损坏）", cname)
        _force_remove(root, cname)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _inspect_compose_project(root: Path, container_name: str) -> str:
    """Return the ``com.docker.compose.project`` label of a container, or ``""``."""
    try:
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                '{{index .Config.Labels "com.docker.compose.project"}}',
                container_name,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(root),
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def _force_remove(root: Path, container_name: str) -> None:
    """``docker rm -f <container>``."""
    try:
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(root),
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.warning("[冲突修复] rm -f 失败: %s", e)


# ---------------------------------------------------------------------------
# host runtime 服务管理（systemctl）
# ---------------------------------------------------------------------------


def start_host_services(config_path: Path, output_dir: Path) -> None:
    """为 system.yaml 中 runtime: host 的服务执行 systemctl daemon-reload + enable --now。

    仅在 Linux 系统上执行；unit 文件需已由 compiler.py 写入 output_dir/systemd/。

    Args:
        config_path: system.yaml 文件路径。
        output_dir: IaC 编译输出目录（含 systemd/ 子目录）。
    """
    if platform.system() != "Linux":
        logger.debug("非 Linux 环境，跳过 host 服务 systemctl 管理")
        return
    if not shutil.which("systemctl"):
        logger.warning("systemctl 不可用，跳过 host 服务启动")
        return
    if not config_path.exists():
        return

    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("无法解析 system.yaml 以启动 host 服务: %s", exc)
        return

    host_names: list[str] = []
    for name, svc in (data.get("services") or {}).items():
        if isinstance(svc, dict) and svc.get("runtime") == "host" and svc.get("enabled") is not False:
            host_names.append(name)

    if not host_names:
        return

    systemd_dir = output_dir / "systemd"
    if systemd_dir.exists():
        for unit_file in systemd_dir.glob("*.service"):
            dest = Path("/etc/systemd/system") / unit_file.name
            try:
                shutil.copy2(unit_file, dest)
                os.chmod(dest, 0o644)
                logger.info("[host] 已安装 unit 文件: %s", dest)
            except OSError as exc:
                logger.warning("[host] 安装 unit 文件失败 %s: %s", dest, exc)

    try:
        subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=15)
        logger.info("[host] systemctl daemon-reload 完成")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("[host] daemon-reload 失败: %s", exc)

    for name in host_names:
        unit = f"{name}.service"
        try:
            subprocess.run(["systemctl", "enable", "--now", unit], check=True, timeout=30)
            logger.info("[host] %s 已启动并设置开机自启", unit)
        except subprocess.CalledProcessError as exc:
            logger.warning("[host] enable --now %s 失败 (rc=%d)", unit, exc.returncode)
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.warning("[host] enable --now %s 异常: %s", unit, exc)
