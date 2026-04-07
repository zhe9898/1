from __future__ import annotations

from types import SimpleNamespace

from backend.core.alembic_runtime import _alembic_context_options


def test_alembic_context_options_include_version_table() -> None:
    config = SimpleNamespace(get_main_option=lambda key: "alembic_version_test" if key == "version_table" else "")

    assert _alembic_context_options(config) == {"version_table": "alembic_version_test"}


def test_alembic_context_options_ignore_missing_version_table() -> None:
    config = SimpleNamespace(get_main_option=lambda key: "")

    assert _alembic_context_options(config) == {}
