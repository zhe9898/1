from __future__ import annotations

from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock

from backend.platform.db.alembic_runtime import MigrationLockLease, _alembic_context_options, start_migration_lock_watchdog


def test_alembic_context_options_include_version_table() -> None:
    config = SimpleNamespace(get_main_option=lambda key: "alembic_version_test" if key == "version_table" else "")

    assert _alembic_context_options(config) == {"version_table": "alembic_version_test"}


def test_alembic_context_options_ignore_missing_version_table() -> None:
    config = SimpleNamespace(get_main_option=lambda key: "")

    assert _alembic_context_options(config) == {}


def test_start_migration_lock_watchdog_renews_lock_and_stops_cleanly() -> None:
    redis_client = MagicMock()
    redis_client.kv = MagicMock()
    lease = MigrationLockLease(key="zen70:DB_MIGRATION_LOCK", owner="test-owner")
    stop_event = Event()
    redis_client.kv.expire.side_effect = lambda *args, **kwargs: stop_event.set()

    watchdog = start_migration_lock_watchdog(redis_client, lease, stop_event)
    watchdog.join(timeout=1.0)

    assert not watchdog.is_alive()
    redis_client.kv.expire.assert_called_with("zen70:DB_MIGRATION_LOCK", 120)
