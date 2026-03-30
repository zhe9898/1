#!/usr/bin/env python3
"""ZEN70 backup and encrypted ashbox snapshot tooling."""

from __future__ import annotations

import logging
import os
import stat
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

try:
    import pyzipper  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    pyzipper = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [BACKUP-ENGINE] %(message)s",
)
logger = logging.getLogger(__name__)

_APP_YAML = Path("/app/system.yaml")
SYSTEM_YAML_PATH = _APP_YAML if _APP_YAML.exists() else Path(__file__).resolve().parent.parent / "system.yaml"
DEFAULT_POSTGRES_CONTAINER = "zen70-postgres"
ASHBOX_PASSWORD_ENV = "ASHBOX_PASSWORD"


def check_system_load() -> bool:
    """Skip heavy backup work when the host is already under high load."""
    try:
        loadavg_path = Path("/proc/loadavg")
        if loadavg_path.exists():
            load_1m = float(loadavg_path.read_text(encoding="utf-8").split()[0])
            cores = os.cpu_count() or 1
            if (load_1m / cores) > 0.75:
                logger.warning("CPU load too high (1m load=%s, cores=%s); skipping backup", load_1m, cores)
                return False

        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=3,
            )
        except FileNotFoundError:
            result = None

        if result and result.returncode == 0:
            for value in (result.stdout or "").strip().splitlines():
                try:
                    if int(value.strip()) > 80:
                        logger.warning("GPU load too high (%s%%); skipping backup", value.strip())
                        return False
                except ValueError:
                    continue
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.error("System load detection failed: %s; continuing defensively", exc)
    return True


def get_critical_volumes() -> list[str]:
    """Extract critical backup sources from system.yaml."""
    volumes: list[str] = []
    try:
        config_path = SYSTEM_YAML_PATH.resolve()
        if not config_path.exists():
            logger.error("Cannot find %s; backup aborted", config_path)
            return volumes

        data: dict[str, object] = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        services = data.get("services", {})
        for service in (services or {}).values():
            if not isinstance(service, dict):
                continue
            for volume in service.get("volumes", []) or []:
                if isinstance(volume, str):
                    host_mount = volume.split(":", 1)[0]
                    if any(token in host_mount for token in ("data", "config", "db")):
                        volumes.append(host_mount)
                elif isinstance(volume, dict):
                    if volume.get("backup_tier") == "critical" and isinstance(volume.get("source"), str):
                        volumes.append(volume["source"])
    except (OSError, RuntimeError, TypeError, ValueError, yaml.YAMLError) as exc:
        logger.error("Failed to parse %s: %s", SYSTEM_YAML_PATH, exc)
    return sorted({value for value in volumes if value})


def execute_restic_backup(targets: list[str]) -> None:
    """Push critical volumes to the configured restic repository."""
    for target in targets:
        path = Path(target)
        if not path.exists() or not path.is_dir():
            logger.warning("Skipping invalid backup source: %s", target)
            continue
        logger.info("Backing up %s", target)
        subprocess.run(["restic", "backup", str(path)], check=True, timeout=3600)


def verify_backup_integrity() -> None:
    """Run a partial encrypted readback to detect silent corruption."""
    logger.info("Running restic integrity probe (5%% readback subset)")
    subprocess.run(["restic", "check", "--read-data-subset=5%"], check=True, timeout=600)
    logger.info("Restic integrity probe passed")


def _require_ashbox_password() -> bytes:
    password = os.getenv(ASHBOX_PASSWORD_ENV, "").strip()
    if not password:
        raise RuntimeError(f"{ASHBOX_PASSWORD_ENV} must be provided externally; generated password files are forbidden")
    return password.encode("utf-8")


def _require_pyzipper_module():
    if pyzipper is None:
        raise RuntimeError("pyzipper is required for ashbox backups; unencrypted zip fallback is forbidden")
    return pyzipper


def _capture_postgres_dump() -> bytes:
    logger.info("Exporting PostgreSQL database into encrypted archive buffer")
    pg_user = os.getenv("POSTGRES_USER", "zen70")
    pg_db = os.getenv("POSTGRES_DB", "zen70")
    container_name = os.getenv("POSTGRES_CONTAINER_NAME", DEFAULT_POSTGRES_CONTAINER)
    result = subprocess.run(
        ["docker", "exec", container_name, "pg_dump", "-U", pg_user, pg_db],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return result.stdout or b""


def _read_optional_file(path: Path) -> bytes | None:
    if not path.exists() or not path.is_file():
        return None
    return path.read_bytes()


def _chmod_owner_only(path: Path) -> None:
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError as exc:  # pragma: no cover - platform dependent
        logger.debug("Unable to tighten file permissions for %s: %s", path, exc)


def create_ashbox_backup() -> Path:
    """Create an encrypted offline snapshot without leaving plaintext artifacts on disk."""
    password = _require_ashbox_password()
    pyzipper_module = _require_pyzipper_module()
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    zip_path = Path.cwd() / f"ZEN70_Snapshot_{timestamp}.zip"
    db_dump_bytes = _capture_postgres_dump()
    env_bytes = _read_optional_file(Path.cwd() / ".env")
    media_path = Path(os.getenv("MEDIA_PATH", str(Path.cwd() / "media")))

    try:
        with pyzipper_module.AESZipFile(
            zip_path,
            mode="w",
            compression=pyzipper_module.ZIP_DEFLATED,
            encryption=pyzipper_module.WZ_AES,
        ) as archive:
            archive.setpassword(password)
            if env_bytes is not None:
                archive.writestr(".env", env_bytes)
            archive.writestr("db_dump.sql", db_dump_bytes)
            if media_path.exists() and media_path.is_dir():
                logger.info("Packing media assets from %s", media_path)
                for full_path in media_path.rglob("*"):
                    if not full_path.is_file():
                        continue
                    arcname = f"media/{full_path.relative_to(media_path).as_posix()}"
                    archive.write(full_path, arcname)
    except Exception:
        zip_path.unlink(missing_ok=True)
        raise

    _chmod_owner_only(zip_path)
    logger.info("Encrypted ashbox snapshot created: %s", zip_path)
    return zip_path


def main() -> None:
    if "--ashbox" in sys.argv:
        try:
            create_ashbox_backup()
        except (OSError, RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired, zipfile.BadZipFile) as exc:
            logger.error("Ashbox backup failed: %s", exc)
            sys.exit(1)
        sys.exit(0)

    if os.getenv("BACKUP_ENABLED", "true").lower() == "false":
        logger.info("Backup engine disabled via configuration; exiting")
        sys.exit(0)

    if not os.getenv("RESTIC_REPOSITORY"):
        logger.error("Missing RESTIC_REPOSITORY; backup engine cannot start")
        sys.exit(1)

    if not check_system_load():
        logger.info("Skipping backup due to high system load")
        sys.exit(0)

    targets = get_critical_volumes()
    if not targets:
        logger.warning("No critical backup volumes discovered; exiting")
        sys.exit(0)

    try:
        execute_restic_backup(targets)
        verify_backup_integrity()
    except (OSError, RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        logger.exception("Backup or verification failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
