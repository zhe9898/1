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
import re
import subprocess
from pathlib import Path

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
