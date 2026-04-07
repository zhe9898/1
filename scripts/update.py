#!/usr/bin/env python3
"""Zero-downtime update orchestration for the gateway deployment."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.yml"
REQUIREMENTS = PROJECT_ROOT / "backend" / "requirements.txt"
DOCKERFILE = PROJECT_ROOT / "backend" / "Dockerfile"
MIGRATION_RUNNER = PROJECT_ROOT / "backend" / "scripts" / "migrate.py"
GATEWAY_CONTAINER = "zen70-gateway"
GIT_REMOTES: list[str] = ["origin"]

GREEN = "\033[92m"
RED = "\033[91m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


class _JsonFormatter(logging.Formatter):
    """Render operational logs as JSON for CI and deployment pipelines."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "caller": f"{record.module}.{record.funcName}",
            "message": record.getMessage(),
        }
        return json.dumps(entry, ensure_ascii=False)


_handler = logging.StreamHandler(sys.stderr)
_handler.setFormatter(_JsonFormatter())
logger = logging.getLogger("update")
logger.setLevel(logging.INFO)
logger.addHandler(_handler)
logger.propagate = False


def _run(
    args: list[str],
    *,
    timeout: int = 60,
    cwd: Path | None = None,
    check: bool = False,
) -> tuple[int, str]:
    """Run a subprocess and return `(returncode, merged_output)`."""
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            cwd=str(cwd) if cwd else str(PROJECT_ROOT),
        )
    except FileNotFoundError:
        message = f"command not found: {args[0]}"
        if check:
            raise RuntimeError(message) from None
        return -1, message
    except subprocess.TimeoutExpired:
        message = f"command timed out after {timeout}s: {shlex.join(args)}"
        if check:
            raise RuntimeError(message) from None
        return -2, message

    output = (result.stdout + result.stderr).strip()
    if check and result.returncode != 0:
        raise RuntimeError(f"command failed (rc={result.returncode}): {shlex.join(args)}\n{output[:500]}")
    return result.returncode, output


def _file_hash(path: Path) -> str:
    if not path.exists():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_changed_files(diff_output: str) -> set[str]:
    return {
        line.strip().replace("\\", "/")
        for line in diff_output.splitlines()
        if line.strip()
    }


def _image_inputs_changed_from_diff(diff_output: str) -> bool:
    changed = _parse_changed_files(diff_output)
    tracked_inputs = {
        REQUIREMENTS.relative_to(PROJECT_ROOT).as_posix(),
        DOCKERFILE.relative_to(PROJECT_ROOT).as_posix(),
    }
    return bool(changed & tracked_inputs)


def _needs_image_rebuild_from_hashes(
    pre_requirements_hash: str,
    pre_dockerfile_hash: str,
    post_requirements_hash: str,
    post_dockerfile_hash: str,
) -> bool:
    return (
        pre_requirements_hash != post_requirements_hash
        or pre_dockerfile_hash != post_dockerfile_hash
    )


def _collect_dirty_files(status_output: str) -> list[str]:
    return [line.strip() for line in status_output.splitlines() if line.strip()]


def _parse_upstream_branch(ref_output: str) -> str | None:
    ref = ref_output.strip()
    if not ref or ref == "HEAD":
        return None
    if "/" in ref:
        branch = ref.rsplit("/", 1)[-1].strip()
        return branch or None
    return ref


def _detect_pull_branch() -> str:
    env_branch = os.getenv("ZEN70_UPDATE_BRANCH", "").strip()
    if env_branch:
        return env_branch

    rc, upstream = _run(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"])
    if rc == 0:
        branch = _parse_upstream_branch(upstream)
        if branch:
            return branch

    rc, current = _run(["git", "branch", "--show-current"])
    if rc == 0 and current.strip():
        return current.strip()

    return "master"


def step_git_pull(dry_run: bool = False) -> str:
    """Pull the latest code and return the pre-update HEAD SHA."""
    logger.info("[STEP 1/6] Pull latest code")

    rc, old_head = _run(["git", "rev-parse", "HEAD"])
    if rc != 0:
        logger.warning("unable to resolve current HEAD: %s", old_head[:200])
        old_head = "HEAD~1"

    if dry_run:
        logger.info("  %s[DRY-RUN] skipping git pull%s", DIM, RESET)
        return old_head.strip()

    pull_branch = _detect_pull_branch()
    for remote in GIT_REMOTES:
        rc, output = _run(["git", "pull", "--rebase", remote, pull_branch], timeout=120)
        if rc == 0:
            _, new_head = _run(["git", "rev-parse", "--short", "HEAD"])
            logger.info("  code updated: %s -> %s", old_head[:8], new_head.strip() or "unknown")
            return old_head.strip()
        logger.warning("  git pull %s failed: %s", remote, output[:200])

    logger.error("  all git remotes failed; continuing with local workspace")
    return old_head.strip()


def step_check_deps_changed(force: bool = False) -> bool:
    """Return True when image-defining inputs changed and rebuild is required."""
    logger.info("[STEP 2/6] Inspect image-defining changes")
    if force:
        logger.info("  --force supplied, image rebuild required")
        return True

    tracked_inputs = [
        str(REQUIREMENTS.relative_to(PROJECT_ROOT)),
        str(DOCKERFILE.relative_to(PROJECT_ROOT)),
    ]
    rc, diff = _run(["git", "diff", "HEAD~1", "--name-only", "--", *tracked_inputs])
    changed = rc == 0 and _image_inputs_changed_from_diff(diff)
    if changed:
        logger.info("  detected requirements/Dockerfile changes; image rebuild required")
    else:
        logger.info("  image-defining inputs unchanged; skipping image rebuild")
    return changed


def step_rebuild_image(dry_run: bool = False) -> None:
    """Rebuild the gateway image when dependencies changed."""
    logger.info("[STEP 3/6] Rebuild gateway image")
    if dry_run:
        logger.info("  %s[DRY-RUN] skipping image rebuild%s", DIM, RESET)
        return

    cachebust = str(int(time.time()))
    _run(
        [
            "docker",
            "build",
            "--build-arg",
            f"CACHEBUST={cachebust}",
            "-f",
            str(DOCKERFILE),
            "-t",
            "zen70-gateway:update",
            ".",
        ],
        timeout=600,
        check=True,
    )
    logger.info("  image rebuild completed")


def step_db_migrate(dry_run: bool = False) -> None:
    """Run governed migrations inside the gateway container."""
    logger.info("[STEP 4/6] Run database migrations")
    if not MIGRATION_RUNNER.exists():
        logger.info("  migration runner missing; skipping database migrations")
        return

    if dry_run:
        logger.info("  %s[DRY-RUN] skipping database migrations%s", DIM, RESET)
        return

    rc, output = _run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "exec",
            "-T",
            "gateway",
            "python",
            "-m",
            "backend.scripts.migrate",
            "--managed-only",
        ],
        timeout=300,
    )
    if rc == 0:
        logger.info("  database migrations completed")
        return

    lowered = output.lower()
    if "already at head" in lowered or "no new" in lowered:
        logger.info("  database already at requested migration head")
        return

    raise RuntimeError(f"database migration failed (rc={rc}): {output[:300]}")


def step_rolling_update(dry_run: bool = False) -> None:
    """Refresh the deployment using docker compose rolling semantics."""
    logger.info("[STEP 5/6] Roll containers")
    if dry_run:
        logger.info("  %s[DRY-RUN] skipping compose up%s", DIM, RESET)
        return

    rc, output = _run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "up",
            "-d",
            "--remove-orphans",
        ],
        timeout=300,
    )
    if rc != 0:
        raise RuntimeError(f"compose up failed (rc={rc}): {output[:500]}")
    logger.info("  rolling update completed")


def step_health_check() -> bool:
    """Wait until the gateway container reports a healthy status."""
    logger.info("[STEP 6/6] Wait for healthy gateway")
    max_attempts = 15
    for attempt in range(1, max_attempts + 1):
        time.sleep(3)
        rc, output = _run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.State.Health.Status}}",
                GATEWAY_CONTAINER,
            ],
            timeout=10,
        )
        status = output.strip().lower()
        if rc == 0 and status == "healthy":
            logger.info("  gateway healthy on attempt %d/%d", attempt, max_attempts)
            return True
        logger.info("  gateway status: %s (%d/%d)", status or "<unknown>", attempt, max_attempts)

    logger.error("  gateway health check timed out")
    return False


def _check_workspace_clean() -> None:
    """Fail fast when the git workspace is dirty before update/rollback."""
    rc, output = _run(["git", "status", "--porcelain"])
    if rc != 0:
        logger.warning("unable to inspect git status (rc=%d): %s", rc, output[:200])
        return

    dirty_files = _collect_dirty_files(output)
    if not dirty_files:
        logger.info("  workspace clean")
        return

    logger.error("workspace is dirty; refusing update to avoid destructive rollback")
    for path in dirty_files[:5]:
        logger.error("  dirty: %s", path)
    if len(dirty_files) > 5:
        logger.error("  ... and %d more files", len(dirty_files) - 5)
    raise RuntimeError(
        "workspace has uncommitted changes; run `git stash` or `git commit` before using scripts/update.py"
    )


def rollback(old_head: str) -> None:
    """Return the workspace and deployment to the pre-update commit."""
    logger.warning("%s%sstarting rollback%s", YELLOW, BOLD, RESET)
    logger.warning("  target commit: %s", old_head[:8] if len(old_head) >= 8 else old_head)

    pre_requirements_hash = _file_hash(REQUIREMENTS)
    pre_dockerfile_hash = _file_hash(DOCKERFILE)

    logger.warning("  [rollback 1/3] git reset --hard %s", old_head[:8] if len(old_head) >= 8 else old_head)
    rc, output = _run(["git", "reset", "--hard", old_head])
    if rc != 0:
        logger.error("  rollback git reset failed: %s", output[:300])
        return

    post_requirements_hash = _file_hash(REQUIREMENTS)
    post_dockerfile_hash = _file_hash(DOCKERFILE)
    needs_image_rebuild = _needs_image_rebuild_from_hashes(
        pre_requirements_hash,
        pre_dockerfile_hash,
        post_requirements_hash,
        post_dockerfile_hash,
    )

    if needs_image_rebuild:
        logger.warning("  [rollback 2/3] rebuild image for restored commit")
        rc_build, build_output = _run(
            [
                "docker",
                "build",
                "-f",
                str(DOCKERFILE),
                "-t",
                "zen70-gateway:update",
                ".",
            ],
            timeout=600,
        )
        if rc_build != 0:
            logger.error("  rollback image rebuild failed (rc=%d): %s", rc_build, build_output[:300])
        else:
            logger.info("  rollback image rebuild completed")
    else:
        logger.info("  [rollback 2/3] image inputs unchanged; rebuild skipped")

    logger.warning("  [rollback 3/3] restore containers")
    _run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "up",
            "-d",
            "--remove-orphans",
        ],
        timeout=300,
    )
    logger.info("  rollback completed")


def main() -> None:
    parser = argparse.ArgumentParser(description="ZEN70 zero-downtime update engine")
    parser.add_argument("--dry-run", action="store_true", help="Inspect only; do not change code, images, or containers.")
    parser.add_argument("--force", action="store_true", help="Force a gateway image rebuild.")
    args = parser.parse_args()

    logger.info("%s%sStarting ZEN70 update%s", BOLD, CYAN, RESET)
    start_time = time.time()

    logger.info("[STEP 0/6] Verify clean workspace")
    _check_workspace_clean()

    old_head = "HEAD~1"
    try:
        old_head = step_git_pull(dry_run=args.dry_run)
        needs_rebuild = step_check_deps_changed(force=args.force)
        if needs_rebuild:
            step_rebuild_image(dry_run=args.dry_run)
        step_db_migrate(dry_run=args.dry_run)
        step_rolling_update(dry_run=args.dry_run)

        if not args.dry_run and not step_health_check():
            logger.error("post-update health check failed; initiating rollback")
            rollback(old_head)
            sys.exit(1)

        elapsed = int(time.time() - start_time)
        logger.info("%s%supdate completed (%ds)%s", GREEN, BOLD, elapsed, RESET)
    except RuntimeError as exc:
        logger.error("%supdate failed: %s%s", RED, exc, RESET)
        logger.error("attempting automatic rollback")
        rollback(old_head)
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("update interrupted by user")
        sys.exit(130)


if __name__ == "__main__":
    main()
