"""Restic backup helpers with load-aware admission control."""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path

import psutil

from backend.platform.security.normalization import (
    default_restic_allowed_roots,
    parse_allowed_roots,
    resolve_path_within_roots,
    split_csv_values,
)
from backend.platform.http.webhooks import post_public_webhook

logger = logging.getLogger("zen70.sentinel.restic_backup")
_GPU_UTILIZATION_PATTERN = re.compile(r"^nvidia_gpu_utilization(?:\{[^}]*\})?\s+([0-9]+(?:\.[0-9]+)?)\s*$")
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _extract_max_gpu_utilization(metrics_text: str) -> float | None:
    """Return the highest GPU utilization value found in Prometheus text."""
    max_gpu_usage: float | None = None
    for raw_line in metrics_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _GPU_UTILIZATION_PATTERN.match(line)
        if not match:
            continue
        gpu_usage = float(match.group(1))
        max_gpu_usage = gpu_usage if max_gpu_usage is None else max(max_gpu_usage, gpu_usage)
    return max_gpu_usage


def check_system_load_for_backup() -> bool:
    """Block backups when CPU or observed GPU load is too high."""
    cpu_usage = psutil.cpu_percent(interval=1)
    if cpu_usage > 75.0:
        logger.warning("backup_skipped_high_cpu: cpu_usage=%.2f", cpu_usage)
        return False

    gpu_metrics_url = os.getenv("CATEGRAF_GPU_METRICS_URL", "").strip()
    if gpu_metrics_url:
        try:
            import httpx

            resp = httpx.get(gpu_metrics_url, timeout=3.0)
            if resp.status_code == 200:
                gpu_usage = _extract_max_gpu_utilization(resp.text)
                if gpu_usage is not None and gpu_usage > 80.0:
                    logger.warning("backup_skipped_high_gpu: gpu_usage=%.2f", gpu_usage)
                    return False
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as gpu_err:
            logger.debug("categraf_gpu_metrics_query_failed: %s", gpu_err)
    return True


def run_restic_backup(
    repo_url: str,
    repository_password: str,
    target_paths: list[str],
    aws_access_key_id: str,
    aws_secret_access_key: str,
) -> bool:
    """Run a restic backup command with the required secret environment."""
    if not check_system_load_for_backup():
        return False

    env = os.environ.copy()
    env["RESTIC_PASSWORD"] = repository_password
    env["AWS_ACCESS_KEY_ID"] = aws_access_key_id
    env["AWS_SECRET_ACCESS_KEY"] = aws_secret_access_key

    cmd = ["restic", "-r", repo_url, "backup", *target_paths]

    try:
        logger.info("restic_backup_started: targets=%s", target_paths)
        result = subprocess.run(
            cmd,
            shell=False,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=3600,
        )
        if result.stdout:
            logger.info(result.stdout.strip())
        logger.info("restic_backup_succeeded")
        return True
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        logger.error("restic_backup_failed: %s", stderr)
        alert_webhook = os.getenv("ALERT_WEBHOOK_URL", "").strip()
        if alert_webhook:
            post_public_webhook(
                alert_webhook,
                {
                    "level": "critical",
                    "title": "Restic backup failed",
                    "message": f"Backup failed: {stderr[:500]}",
                    "source": "restic_backup",
                },
                timeout=5.0,
                logger=logger,
                context="restic_backup",
            )
        return False
    except subprocess.TimeoutExpired:
        logger.error("restic_backup_timed_out")
        return False


def _get_required_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value if value else None


def load_restic_target_paths(raw_value: str) -> list[str]:
    allowed_roots = parse_allowed_roots(
        os.getenv("RESTIC_ALLOWED_ROOTS", ""),
        field_name="RESTIC_ALLOWED_ROOTS",
        default_roots=default_restic_allowed_roots(_PROJECT_ROOT),
    )
    return [
        str(resolve_path_within_roots(path, field_name="RESTIC_TARGET_PATHS", roots=allowed_roots, must_exist=True))
        for path in split_csv_values(raw_value, field_name="RESTIC_TARGET_PATHS")
    ]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info("restic_backup_bootstrap_starting")
    repo = _get_required_env("RESTIC_REPOSITORY")
    pw = _get_required_env("RESTIC_PASSWORD")
    ak = _get_required_env("AWS_ACCESS_KEY_ID")
    sk = _get_required_env("AWS_SECRET_ACCESS_KEY")
    targets_raw = os.getenv("RESTIC_TARGET_PATHS", "")
    try:
        targets = load_restic_target_paths(targets_raw)
    except ValueError as exc:
        logger.error("restic_backup_invalid_targets: %s", exc)
        raise SystemExit(2) from exc

    missing = [
        key
        for key, value in (
            ("RESTIC_REPOSITORY", repo),
            ("RESTIC_PASSWORD", pw),
            ("AWS_ACCESS_KEY_ID", ak),
            ("AWS_SECRET_ACCESS_KEY", sk),
        )
        if not value
    ]
    if missing or not targets:
        logger.error("restic_backup_missing_configuration: missing=%s targets=%s", missing, targets)
        raise SystemExit(2)

    run_restic_backup(
        repo_url=repo,  # type: ignore[arg-type]
        repository_password=pw,  # type: ignore[arg-type]
        target_paths=targets,
        aws_access_key_id=ak,  # type: ignore[arg-type]
        aws_secret_access_key=sk,  # type: ignore[arg-type]
    )

