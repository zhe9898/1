from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.migration_governance import MIGRATION_CHAINS_BY_KEY, ordered_migration_chains, runtime_managed_migration_chains
from backend.core.migration_runner import build_alembic_config, resolve_migration_chains, run_governed_migrations


def test_build_alembic_config_uses_chain_config_path() -> None:
    chain = MIGRATION_CHAINS_BY_KEY["legacy"]
    config = build_alembic_config(chain)

    assert Path(str(config.config_file_name)).resolve() == chain.config_path.resolve()


def test_resolve_migration_chains_defaults_to_all_in_order() -> None:
    assert resolve_migration_chains() == ordered_migration_chains()


def test_resolve_migration_chains_runtime_managed_only_filters_scope() -> None:
    assert resolve_migration_chains(runtime_managed_only=True) == runtime_managed_migration_chains()


def test_resolve_migration_chains_rejects_unknown_key() -> None:
    with pytest.raises(ValueError, match="Unknown migration chain"):
        resolve_migration_chains(["missing"])


def test_resolve_migration_chains_rejects_non_runtime_managed_chain_when_filtered() -> None:
    with pytest.raises(ValueError, match="runtime-managed"):
        resolve_migration_chains(["application"], runtime_managed_only=True)


def test_run_governed_migrations_upgrades_in_execution_order() -> None:
    calls: list[tuple[str, str]] = []

    def fake_upgrade(config: object, revision: str) -> None:
        config_path = Path(str(getattr(config, "config_file_name"))).name
        calls.append((config_path, revision))

    executed = run_governed_migrations(chain_keys=["application", "legacy"], upgrade_fn=fake_upgrade)

    assert executed == ("legacy", "application")
    assert calls == [("alembic.ini", "head"), ("migrations.ini", "head")]


def test_run_governed_migrations_honors_runtime_managed_only() -> None:
    calls: list[str] = []

    def fake_upgrade(config: object, revision: str) -> None:
        calls.append(Path(str(getattr(config, "config_file_name"))).name)

    executed = run_governed_migrations(runtime_managed_only=True, upgrade_fn=fake_upgrade)

    assert executed == ("legacy",)
    assert calls == ["alembic.ini"]

