"""Shared Alembic runtime bootstrap for both migration chains."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
import time
from logging.config import fileConfig
from pathlib import Path
from typing import Any

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from backend.platform.db.advisory_locks import advisory_lock_id

_MIGRATION_LOGGER = logging.getLogger("alembic.runtime")
_LOCK_IDENTITY = f"pid={os.getpid()}@{socket.gethostname()}"
_LOCK_BLOCKING_TIMEOUT_SECONDS = 60


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


def _migration_lock_id(config: Any) -> int:
    version_table = (config.get_main_option("version_table") or "alembic_version").strip() or "alembic_version"
    script_location = (config.get_main_option("script_location") or "alembic").strip() or "alembic"
    return advisory_lock_id("zen70.db.migration", script_location, version_table)


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


async def _try_acquire_migration_lock(connection: Any, lock_id: int) -> bool:
    result = await connection.execute(text("SELECT pg_try_advisory_lock(:lock_id)"), {"lock_id": lock_id})
    return bool(result.scalar())


async def _acquire_migration_lock(connection: Any, lock_id: int) -> None:
    _MIGRATION_LOGGER.info(
        "acquiring migration advisory lock [%s] lock_id=%s blocking_timeout=%ss",
        _LOCK_IDENTITY,
        lock_id,
        _LOCK_BLOCKING_TIMEOUT_SECONDS,
    )
    deadline = time.monotonic() + _LOCK_BLOCKING_TIMEOUT_SECONDS
    while True:
        if await _try_acquire_migration_lock(connection, lock_id):
            _MIGRATION_LOGGER.info("migration advisory lock acquired [%s] lock_id=%s", _LOCK_IDENTITY, lock_id)
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"ZEN-DB-MIGRATION-LOCKED: could not acquire advisory lock {lock_id} within " f"{_LOCK_BLOCKING_TIMEOUT_SECONDS}s [{_LOCK_IDENTITY}]"
            )
        await asyncio.sleep(1.0)


async def _release_migration_lock(connection: Any, lock_id: int) -> None:
    result = await connection.execute(text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": lock_id})
    released = result.scalar()
    if released is not True:
        raise RuntimeError(f"advisory lock {lock_id} was not held by this migration session")
    _MIGRATION_LOGGER.info("migration advisory lock released [%s] lock_id=%s", _LOCK_IDENTITY, lock_id)


async def _run_async_migrations(config: Any, target_metadata: Any) -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    lock_id = _migration_lock_id(config)
    try:
        async with connectable.connect() as connection:
            await _acquire_migration_lock(connection, lock_id)

            migration_error: BaseException | None = None
            cleanup_error: BaseException | None = None
            try:
                await connection.run_sync(_do_run_migrations, target_metadata, config)
            except BaseException as exc:
                migration_error = exc
            finally:
                try:
                    await _release_migration_lock(connection, lock_id)
                except Exception as exc:
                    cleanup_error = RuntimeError(f"ZEN-DB-MIGRATION-CLEANUP-FAILED: failed to release advisory lock {lock_id} [{_LOCK_IDENTITY}]")
                    cleanup_error.__cause__ = exc
                    _MIGRATION_LOGGER.error(
                        "migration advisory lock release failed [%s]: %s",
                        _LOCK_IDENTITY,
                        exc,
                        exc_info=True,
                    )

            if migration_error is not None:
                if cleanup_error is not None:
                    migration_error.add_note(str(cleanup_error))
                raise migration_error.with_traceback(migration_error.__traceback__)
            if cleanup_error is not None:
                raise cleanup_error
    finally:
        await connectable.dispose()


def run_migrations_online(config: Any, target_metadata: Any) -> None:
    """Run migrations in online mode with a Postgres advisory lock."""
    asyncio.run(_run_async_migrations(config, target_metadata))


def run_alembic_env(env_file: str | Path) -> None:
    """Execute the current Alembic environment using the shared runtime."""
    config, target_metadata = prepare_alembic_environment(env_file)
    if context.is_offline_mode():
        run_migrations_offline(config, target_metadata)
    else:
        run_migrations_online(config, target_metadata)
