from __future__ import annotations

from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock

from backend.core.alembic_runtime import _alembic_context_options, start_migration_lock_watchdog


def test_alembic_context_options_include_version_table() -> None:
    config = SimpleNamespace(get_main_option=lambda key: "alembic_version_test" if key == "version_table" else "")

    assert _alembic_context_options(config) == {"version_table": "alembic_version_test"}


def test_alembic_context_options_ignore_missing_version_table() -> None:
    config = SimpleNamespace(get_main_option=lambda key: "")

    assert _alembic_context_options(config) == {}


def test_start_migration_lock_watchdog_renews_lock_and_stops_cleanly() -> None:
    redis_client = MagicMock()
    lock = SimpleNamespace(name="zen70:DB_MIGRATION_LOCK")
    stop_event = Event()
    redis_client.pexpire.side_effect = lambda *args, **kwargs: stop_event.set()

    watchdog = start_migration_lock_watchdog(redis_client, lock, stop_event)
    watchdog.join(timeout=1.0)

    assert not watchdog.is_alive()
    redis_client.pexpire.assert_called()
