"""Shared Alembic runtime bootstrap for both migration chains."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
import threading
import time
from dataclasses import dataclass
from logging.config import fileConfig
from pathlib import Path
from typing import Any

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from backend.platform.redis import KEY_DB_MIGRATION_LOCK, SyncRedisClient

_MIGRATION_LOGGER = logging.getLogger("alembic.runtime")
_LOCK_IDENTITY = f"pid={os.getpid()}@{socket.gethostname()}"
_LOCK_TIMEOUT_SECONDS = 120
_LOCK_BLOCKING_TIMEOUT_SECONDS = 60


@dataclass(frozen=True, slots=True)
class MigrationLockLease:
    key: str
    owner: str


def _project_root(env_file: Path) -> Path:
    return env_file.resolve().parents[2]


def _ensure_project_root_on_sys_path(root_dir: Path) -> None:
    root = str(root_dir)
    if root not in sys.path:
        sys.path.insert(0, root)


def _resolve_postgres_dsn() -> str | None:
    postgres_dsn = os.getenv("POSTGRES_DSN")
    if not postgres_dsn:
        return None
    if postgres_dsn.startswith("postgresql://"):
        postgres_dsn = postgres_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    if os.getenv("DB_OFFLINE_LOCAL") == "1":
        pg_host = os.getenv("POSTGRES_HOST", "postgres")
        pgbouncer_host = os.getenv("PGBOUNCER_HOST", "pgbouncer")
        postgres_dsn = postgres_dsn.replace(f"@{pgbouncer_host}:5432/", "@localhost:5432/").replace(
            f"@{pg_host}:5432/",
            "@localhost:5432/",
        )
    return postgres_dsn


def prepare_alembic_environment(env_file: str | Path) -> tuple[Any, Any]:
    """Prepare shared Alembic runtime state and return `(config, metadata)`."""
    config = context.config
    if config.config_file_name is not None:
        fileConfig(config.config_file_name)

    root_dir = _project_root(Path(env_file))
    _ensure_project_root_on_sys_path(root_dir)
    load_dotenv(root_dir / ".env")

    postgres_dsn = _resolve_postgres_dsn()
    if postgres_dsn:
        config.set_main_option("sqlalchemy.url", postgres_dsn)

    from backend.models.registry import load_canonical_model_metadata

    return config, load_canonical_model_metadata()


def _alembic_context_options(config: Any) -> dict[str, Any]:
    options: dict[str, Any] = {}
    version_table = (config.get_main_option("version_table") or "").strip()
    if version_table:
        options["version_table"] = version_table
    return options


def run_migrations_offline(config: Any, target_metadata: Any) -> None:
    """Run migrations in offline mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_alembic_context_options(config),
    )

    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection, target_metadata: Any, config: Any) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, **_alembic_context_options(config))

    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations(config: Any, target_metadata: Any) -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations, target_metadata, config)

    await connectable.dispose()


def _watchdog_thread(redis_client: SyncRedisClient, lease: MigrationLockLease, stop_event: threading.Event) -> None:
    """Keep the migration lock alive while long-running DDL is in progress."""
    while not stop_event.is_set():
        try:
            redis_client.kv.expire(lease.key, _LOCK_TIMEOUT_SECONDS)
        except (OSError, ValueError, RuntimeError, TypeError) as exc:
            _MIGRATION_LOGGER.debug("migration lock renew failed [%s]: %s", _LOCK_IDENTITY, exc)
        stop_event.wait(10)


def start_migration_lock_watchdog(
    redis_client: SyncRedisClient,
    lease: MigrationLockLease,
    stop_event: threading.Event,
) -> threading.Thread:
    """Start the migration-lock watchdog thread and return the live thread."""
    watchdog = threading.Thread(target=_watchdog_thread, args=(redis_client, lease, stop_event), daemon=True)
    watchdog.start()
    return watchdog


def _build_sync_redis_client() -> SyncRedisClient:
    client = SyncRedisClient()
    client.connect()
    return client


def _try_claim_migration_lock(redis_client: SyncRedisClient) -> MigrationLockLease | None:
    lease = MigrationLockLease(key=KEY_DB_MIGRATION_LOCK, owner=_LOCK_IDENTITY)
    claimed = redis_client.kv.set(lease.key, lease.owner, nx=True, ex=_LOCK_TIMEOUT_SECONDS)
    if claimed is True:
        return lease
    return None


def _acquire_migration_lock() -> tuple[SyncRedisClient, MigrationLockLease, threading.Event, threading.Thread] | None:
    try:
        redis_client = _build_sync_redis_client()
    except (OSError, ValueError, RuntimeError, TypeError) as exc:
        if os.getenv("SKIP_DB_MIGRATION_LOCK"):
            _MIGRATION_LOGGER.warning(
                "SKIP_DB_MIGRATION_LOCK=1, skipping migration lock acquisition [%s]: %s",
                _LOCK_IDENTITY,
                exc,
            )
            return None
        raise

    try:
        _MIGRATION_LOGGER.info(
            "acquiring migration lock [%s] key=%s blocking_timeout=%ss",
            _LOCK_IDENTITY,
            KEY_DB_MIGRATION_LOCK,
            _LOCK_BLOCKING_TIMEOUT_SECONDS,
        )
        deadline = time.monotonic() + _LOCK_BLOCKING_TIMEOUT_SECONDS
        while True:
            lease = _try_claim_migration_lock(redis_client)
            if lease is not None:
                stop_event = threading.Event()
                watchdog = start_migration_lock_watchdog(redis_client, lease, stop_event)
                _MIGRATION_LOGGER.info(
                    "migration lock acquired [%s] key=%s ttl=%ds",
                    _LOCK_IDENTITY,
                    KEY_DB_MIGRATION_LOCK,
                    _LOCK_TIMEOUT_SECONDS,
                )
                return redis_client, lease, stop_event, watchdog
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"ZEN-DB-MIGRATION-LOCKED: could not acquire {KEY_DB_MIGRATION_LOCK} within {_LOCK_BLOCKING_TIMEOUT_SECONDS}s [{_LOCK_IDENTITY}]"
                )
            threading.Event().wait(1)
    except Exception:
        redis_client.close()
        raise


def _release_migration_lock(redis_client: SyncRedisClient, lease: MigrationLockLease) -> None:
    current_owner = redis_client.kv.get(lease.key)
    if current_owner == lease.owner:
        redis_client.kv.delete(lease.key)


def run_migrations_online(config: Any, target_metadata: Any) -> None:
    """Run migrations in online mode with the shared Redis lock."""
    lock_bundle = _acquire_migration_lock()
    try:
        asyncio.run(_run_async_migrations(config, target_metadata))
    finally:
        if lock_bundle is None:
            return
        redis_client, lease, stop_event, watchdog = lock_bundle
        try:
            stop_event.set()
            watchdog.join(timeout=2.0)
            _release_migration_lock(redis_client, lease)
            _MIGRATION_LOGGER.info("migration lock released [%s] key=%s", _LOCK_IDENTITY, KEY_DB_MIGRATION_LOCK)
        except Exception as exc:
            _MIGRATION_LOGGER.warning(
                "migration lock release failed [%s]: %s; TTL expiry will clean it up",
                _LOCK_IDENTITY,
                exc,
            )
        try:
            redis_client.close()
        except Exception as exc:
            _MIGRATION_LOGGER.debug("redis close failed during migration cleanup: %s", exc)


def run_alembic_env(env_file: str | Path) -> None:
    """Execute the current Alembic environment using the shared runtime."""
    config, target_metadata = prepare_alembic_environment(env_file)
    if context.is_offline_mode():
        run_migrations_offline(config, target_metadata)
    else:
        run_migrations_online(config, target_metadata)
