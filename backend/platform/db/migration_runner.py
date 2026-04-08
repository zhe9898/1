"""Programmatic migration execution bound to repository governance."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable

from alembic import command
from alembic.config import Config

from backend.platform.db.migration_governance import (
    MIGRATION_CHAINS_BY_KEY,
    MigrationChain,
    ordered_migration_chains,
    runtime_managed_migration_chains,
    validate_migration_governance,
)

_LOGGER = logging.getLogger("backend.migration_runner")
UpgradeFn = Callable[[Config, str], object]


class MigrationGovernanceError(RuntimeError):
    """Raised when repository migration governance is unsafe to execute."""


def assert_migration_governance_clean() -> None:
    errors = validate_migration_governance()
    if not errors:
        return
    details = "\n".join(f"- {error}" for error in errors)
    raise MigrationGovernanceError(f"Migration governance validation failed:\n{details}")


def build_alembic_config(chain: MigrationChain) -> Config:
    if not chain.config_path.exists():
        raise FileNotFoundError(f"Missing Alembic config for chain {chain.key}: {chain.config_path}")
    return Config(str(chain.config_path))


def resolve_migration_chains(
    chain_keys: Iterable[str] | None = None,
    *,
    runtime_managed_only: bool = False,
) -> tuple[MigrationChain, ...]:
    if chain_keys is None:
        return runtime_managed_migration_chains() if runtime_managed_only else ordered_migration_chains()

    selected: list[MigrationChain] = []
    seen: set[str] = set()
    for raw_key in chain_keys:
        key = raw_key.strip()
        if not key or key in seen:
            continue
        chain = MIGRATION_CHAINS_BY_KEY.get(key)
        if chain is None:
            raise ValueError(f"Unknown migration chain: {key}")
        if runtime_managed_only and not chain.runtime_managed:
            raise ValueError(f"Migration chain '{key}' is not marked runtime-managed")
        selected.append(chain)
        seen.add(key)
    return tuple(sorted(selected, key=lambda chain: chain.execution_order))


def upgrade_chains(
    chains: Iterable[MigrationChain],
    *,
    revision: str = "head",
    upgrade_fn: UpgradeFn = command.upgrade,
) -> tuple[str, ...]:
    executed: list[str] = []
    for chain in chains:
        _LOGGER.info(
            "upgrading migration chain key=%s revision=%s config=%s version_table=%s",
            chain.key,
            revision,
            chain.config_path,
            chain.version_table,
        )
        upgrade_fn(build_alembic_config(chain), revision)
        executed.append(chain.key)
    return tuple(executed)


def run_governed_migrations(
    *,
    chain_keys: Iterable[str] | None = None,
    runtime_managed_only: bool = False,
    revision: str = "head",
    upgrade_fn: UpgradeFn = command.upgrade,
) -> tuple[str, ...]:
    assert_migration_governance_clean()
    chains = resolve_migration_chains(chain_keys, runtime_managed_only=runtime_managed_only)
    return upgrade_chains(chains, revision=revision, upgrade_fn=upgrade_fn)

