"""Static governance rules for the repository's dual migration topology."""

from __future__ import annotations

import ast
import configparser
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Final


@dataclass(frozen=True)
class MigrationChain:
    key: str
    label: str
    config_path: Path
    script_location: Path
    versions_dir: Path
    version_table: str
    execution_order: int
    runtime_managed: bool
    description: str


@dataclass(frozen=True)
class ApprovedTableOverlap:
    table_name: str
    canonical_chain: str
    rationale: str


_PROJECT_ROOT = Path(__file__).resolve().parents[2]

MIGRATION_CHAINS: Final[tuple[MigrationChain, ...]] = (
    MigrationChain(
        key="legacy",
        label="Legacy Alembic Chain",
        config_path=_PROJECT_ROOT / "backend" / "alembic.ini",
        script_location=_PROJECT_ROOT / "backend" / "alembic",
        versions_dir=_PROJECT_ROOT / "backend" / "alembic" / "versions",
        version_table="alembic_version_legacy",
        execution_order=10,
        runtime_managed=True,
        description="Historical chain still executed by deployment tooling for legacy/device/memory lineage.",
    ),
    MigrationChain(
        key="application",
        label="Application Alembic Chain",
        config_path=_PROJECT_ROOT / "backend" / "migrations.ini",
        script_location=_PROJECT_ROOT / "backend" / "migrations",
        versions_dir=_PROJECT_ROOT / "backend" / "migrations" / "versions",
        version_table="alembic_version_application",
        execution_order=20,
        runtime_managed=False,
        description="Authoritative application-schema chain for model-backed control-plane evolution and new work.",
    ),
)

MIGRATION_CHAINS_BY_KEY: Final[dict[str, MigrationChain]] = {chain.key: chain for chain in MIGRATION_CHAINS}

APPROVED_CROSS_STREAM_TABLE_OVERLAPS: Final[dict[str, ApprovedTableOverlap]] = {
    "job_attempts": ApprovedTableOverlap(
        table_name="job_attempts",
        canonical_chain="application",
        rationale="Future queue/scheduler evolution belongs to the application chain; legacy overlap is historical hardening debt.",
    ),
    "jobs": ApprovedTableOverlap(
        table_name="jobs",
        canonical_chain="application",
        rationale="The job contract is now governed by the application chain even though the legacy chain patched it earlier.",
    ),
    "memory_facts": ApprovedTableOverlap(
        table_name="memory_facts",
        canonical_chain="application",
        rationale="The current SQLAlchemy model matches the application-chain ARRAY(Float) storage contract.",
    ),
    "scheduling_decisions": ApprovedTableOverlap(
        table_name="scheduling_decisions",
        canonical_chain="application",
        rationale="Scheduling governance is owned by the application schema chain; legacy creation remains as guarded historical overlap.",
    ),
    "software_evaluations": ApprovedTableOverlap(
        table_name="software_evaluations",
        canonical_chain="application",
        rationale="Evaluation and system-log schema now evolve together in the application chain.",
    ),
    "tenant_scheduling_policies": ApprovedTableOverlap(
        table_name="tenant_scheduling_policies",
        canonical_chain="application",
        rationale="Tenant scheduling policy is part of the application control-plane contract.",
    ),
    "tenants": ApprovedTableOverlap(
        table_name="tenants",
        canonical_chain="application",
        rationale="Tenant aggregate ownership should stay with the application chain despite the earlier legacy bootstrap migration.",
    ),
    "trigger_deliveries": ApprovedTableOverlap(
        table_name="trigger_deliveries",
        canonical_chain="application",
        rationale="Trigger delivery history is an application control-plane concern going forward.",
    ),
    "triggers": ApprovedTableOverlap(
        table_name="triggers",
        canonical_chain="application",
        rationale="Trigger registry and ingress are part of the application control-plane schema.",
    ),
    "users": ApprovedTableOverlap(
        table_name="users",
        canonical_chain="application",
        rationale="User/account lifecycle should continue evolving in the application chain.",
    ),
    "webauthn_credentials": ApprovedTableOverlap(
        table_name="webauthn_credentials",
        canonical_chain="application",
        rationale="Auth credential schema now follows the application-chain identity roadmap.",
    ),
}

_TABLE_TOUCHING_OPERATIONS: Final[frozenset[str]] = frozenset(
    {
        "add_column",
        "alter_column",
        "create_foreign_key",
        "create_index",
        "create_table",
        "create_unique_constraint",
        "drop_column",
        "drop_constraint",
        "drop_index",
    }
)


def ordered_migration_chains() -> tuple[MigrationChain, ...]:
    return tuple(sorted(MIGRATION_CHAINS, key=lambda chain: chain.execution_order))


def runtime_managed_migration_chains() -> tuple[MigrationChain, ...]:
    return tuple(chain for chain in ordered_migration_chains() if chain.runtime_managed)


def iter_migration_files(chain: MigrationChain) -> tuple[Path, ...]:
    return tuple(sorted(path for path in chain.versions_dir.glob("*.py") if path.name != "__init__.py"))


def load_alembic_config_options(chain: MigrationChain) -> dict[str, str]:
    parser = configparser.RawConfigParser()
    parser.read(chain.config_path, encoding="utf-8")
    if not parser.has_section("alembic"):
        return {}
    return {key: value for key, value in parser.items("alembic")}


def _resolve_configured_path(chain: MigrationChain, configured_path: str) -> Path:
    resolved = configured_path.replace("%(here)s", str(chain.config_path.parent))
    return Path(resolved).resolve()


def _literal(node: ast.AST) -> object | None:
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def _extract_touched_tables(path: Path) -> set[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    tables: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if not isinstance(func.value, ast.Name) or func.value.id != "op":
            continue
        if func.attr not in _TABLE_TOUCHING_OPERATIONS:
            continue

        if func.attr == "create_table":
            if node.args:
                table_name = _literal(node.args[0])
                if isinstance(table_name, str):
                    tables.add(table_name)
            continue

        table_arg_index = 1 if func.attr in {"create_index", "drop_index", "create_foreign_key"} else 0
        if len(node.args) <= table_arg_index:
            continue
        table_name = _literal(node.args[table_arg_index])
        if isinstance(table_name, str):
            tables.add(table_name)

    return tables


def collect_table_touches_by_chain() -> dict[str, set[str]]:
    touched: dict[str, set[str]] = {}
    for chain in ordered_migration_chains():
        chain_tables: set[str] = set()
        for path in iter_migration_files(chain):
            chain_tables.update(_extract_touched_tables(path))
        touched[chain.key] = chain_tables
    return touched


def find_cross_stream_table_overlaps() -> dict[str, tuple[str, ...]]:
    owners: dict[str, set[str]] = defaultdict(set)
    for chain in ordered_migration_chains():
        for path in iter_migration_files(chain):
            for table_name in _extract_touched_tables(path):
                owners[table_name].add(chain.key)
    return {
        table_name: tuple(sorted(chain_keys))
        for table_name, chain_keys in owners.items()
        if len(chain_keys) > 1
    }


def find_unapproved_cross_stream_table_overlaps() -> dict[str, tuple[str, ...]]:
    overlaps = find_cross_stream_table_overlaps()
    return {
        table_name: chain_keys
        for table_name, chain_keys in overlaps.items()
        if table_name not in APPROVED_CROSS_STREAM_TABLE_OVERLAPS
    }


def validate_migration_governance() -> list[str]:
    errors: list[str] = []
    overlaps = find_cross_stream_table_overlaps()
    execution_orders = [chain.execution_order for chain in MIGRATION_CHAINS]
    version_tables = [chain.version_table for chain in MIGRATION_CHAINS]

    if len(execution_orders) != len(set(execution_orders)):
        errors.append("migration chains must declare unique execution_order values")

    if len(version_tables) != len(set(version_tables)):
        errors.append("migration chains must declare unique version_table values")

    for chain in ordered_migration_chains():
        if not chain.versions_dir.exists():
            errors.append(f"{chain.key}: missing versions dir {chain.versions_dir}")
        if not chain.config_path.exists():
            errors.append(f"{chain.key}: missing config {chain.config_path}")
            continue

        config_options = load_alembic_config_options(chain)
        configured_script_location = config_options.get("script_location")
        if not configured_script_location:
            errors.append(f"{chain.key}: missing alembic.script_location in {chain.config_path}")
        elif _resolve_configured_path(chain, configured_script_location) != chain.script_location.resolve():
            errors.append(
                f"{chain.key}: config script_location does not match governance ({configured_script_location} != {chain.script_location})"
            )

        configured_version_table = config_options.get("version_table")
        if configured_version_table != chain.version_table:
            errors.append(
                f"{chain.key}: config version_table must be {chain.version_table}, got {configured_version_table or '<missing>'}"
            )

    for table_name, chain_keys in find_unapproved_cross_stream_table_overlaps().items():
        errors.append(f"unapproved cross-stream overlap: {table_name} touched by {', '.join(chain_keys)}")

    for table_name, approved in APPROVED_CROSS_STREAM_TABLE_OVERLAPS.items():
        if table_name not in overlaps:
            errors.append(f"approved overlap no longer present and manifest should be cleaned up: {table_name}")
            continue
        if approved.canonical_chain not in MIGRATION_CHAINS_BY_KEY:
            errors.append(f"{table_name}: unknown canonical chain {approved.canonical_chain}")
            continue
        if approved.canonical_chain not in overlaps[table_name]:
            errors.append(
                f"{table_name}: canonical chain {approved.canonical_chain} is not one of the touching chains {overlaps[table_name]}"
            )

    return errors
