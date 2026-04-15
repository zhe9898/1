from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.platform.db.alembic_runtime import (
    _alembic_context_options,
    _migration_lock_id,
    _release_migration_lock,
)


def _config(*, version_table: str = "alembic_version_test", script_location: str = "backend/alembic") -> SimpleNamespace:
    values = {
        "version_table": version_table,
        "script_location": script_location,
    }
    return SimpleNamespace(
        config_ini_section="alembic",
        get_main_option=lambda key: values.get(key, ""),
        get_section=lambda _section, _default=None: {},
    )


class _AsyncConnectContext:
    def __init__(self, connection: AsyncMock) -> None:
        self._connection = connection

    async def __aenter__(self) -> AsyncMock:
        return self._connection

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        del exc_type, exc, tb
        return False


def _connectable(connection: AsyncMock) -> SimpleNamespace:
    return SimpleNamespace(
        connect=lambda: _AsyncConnectContext(connection),
        dispose=AsyncMock(),
    )


def test_alembic_context_options_include_version_table() -> None:
    assert _alembic_context_options(_config()) == {"version_table": "alembic_version_test"}


def test_alembic_context_options_ignore_missing_version_table() -> None:
    assert _alembic_context_options(_config(version_table="")) == {}


def test_migration_lock_id_depends_on_script_location_and_version_table() -> None:
    primary = _migration_lock_id(_config(version_table="alembic_version", script_location="backend/alembic"))
    secondary = _migration_lock_id(_config(version_table="tenant_version", script_location="backend/alembic"))
    tertiary = _migration_lock_id(_config(version_table="alembic_version", script_location="backend/other"))

    assert primary != secondary
    assert primary != tertiary


@pytest.mark.anyio
async def test_release_migration_lock_raises_when_lock_is_not_held() -> None:
    connection = AsyncMock()
    result = MagicMock()
    result.scalar.return_value = False
    connection.execute = AsyncMock(return_value=result)

    with pytest.raises(RuntimeError, match="was not held"):
        await _release_migration_lock(connection, 123)


@pytest.mark.anyio
async def test_run_migrations_online_raises_when_lock_cleanup_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.platform.db import alembic_runtime as runtime

    connection = AsyncMock()
    acquired = MagicMock()
    acquired.scalar.return_value = True
    released = MagicMock()
    released.scalar.return_value = False
    connection.execute = AsyncMock(side_effect=[acquired, released])
    connection.run_sync = AsyncMock(return_value=None)
    connectable = _connectable(connection)

    monkeypatch.setattr(runtime, "async_engine_from_config", lambda *args, **kwargs: connectable)

    with pytest.raises(RuntimeError, match="ZEN-DB-MIGRATION-CLEANUP-FAILED"):
        await runtime._run_async_migrations(_config(), SimpleNamespace())

    connectable.dispose.assert_awaited_once()


@pytest.mark.anyio
async def test_run_migrations_online_preserves_migration_failure_when_cleanup_also_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.platform.db import alembic_runtime as runtime

    connection = AsyncMock()
    acquired = MagicMock()
    acquired.scalar.return_value = True
    released = MagicMock()
    released.scalar.return_value = False
    connection.execute = AsyncMock(side_effect=[acquired, released])
    migration_error = RuntimeError("migration failed")
    connection.run_sync = AsyncMock(side_effect=migration_error)
    connectable = _connectable(connection)

    monkeypatch.setattr(runtime, "async_engine_from_config", lambda *args, **kwargs: connectable)

    with pytest.raises(RuntimeError, match="migration failed") as exc_info:
        await runtime._run_async_migrations(_config(), SimpleNamespace())

    notes = getattr(exc_info.value, "__notes__", [])
    assert any("ZEN-DB-MIGRATION-CLEANUP-FAILED" in note for note in notes)
    connectable.dispose.assert_awaited_once()
