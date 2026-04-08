"""Validate that model-backed schema evolution stays governed and traceable."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from backend.platform.db.migration_governance import (
    APPLICATION_BASELINE_MODEL_TABLES,
    APPROVED_LEGACY_MODEL_TABLE_CREATIONS,
    collect_created_tables_by_chain,
    collect_model_tables,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
VERSIONS_DIR = REPO_ROOT / "backend" / "migrations" / "versions"
ALEMBIC_VERSIONS_DIR = REPO_ROOT / "backend" / "alembic" / "versions"


def _collect_migrated_tables() -> set[str]:
    """Return every table name created in the application migration chain."""
    tables: set[str] = set()
    pattern = re.compile(r'op\.create_table\(\s*["\'](\w+)["\']')
    for path in VERSIONS_DIR.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for match in pattern.finditer(source):
            tables.add(match.group(1))
    return tables


def _collect_revision_graph(versions_dir: Path) -> tuple[set[str], set[str]]:
    revisions: set[str] = set()
    parent_refs: set[str] = set()
    for path in versions_dir.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        revision = None
        down_revision = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = node.targets
                value = node.value
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
                value = node.value
            else:
                continue
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                if target.id == "revision" and value is not None:
                    revision = ast.literal_eval(value)
                elif target.id == "down_revision" and value is not None:
                    down_revision = ast.literal_eval(value)
        if isinstance(revision, str):
            revisions.add(revision)
        if isinstance(down_revision, tuple):
            parent_refs.update(str(parent) for parent in down_revision)
        elif isinstance(down_revision, str):
            parent_refs.add(down_revision)
    return revisions, parent_refs


def test_all_model_tables_have_migrations() -> None:
    """Every model table must be covered by the application chain or a tracked baseline."""
    model_tables = collect_model_tables()
    migrated_tables = _collect_migrated_tables()
    covered = migrated_tables | APPLICATION_BASELINE_MODEL_TABLES

    missing = model_tables - covered
    assert not missing, (
        "The following tables have SQLAlchemy models but no application-chain coverage:\n"
        + "\n".join(f"  - {table_name}" for table_name in sorted(missing))
        + "\n\nAdd an Alembic migration in backend/migrations/versions/ for each missing table."
    )


def test_migration_chain_is_linear() -> None:
    """Each application migration must reference a known predecessor."""
    down_revisions: dict[str, str | None] = {}
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        rev_match = re.search(r'^revision\s*=\s*["\']([^"\']+)["\']', source, re.MULTILINE)
        down_match = re.search(r'^down_revision\s*=\s*["\']([^"\']+)["\']', source, re.MULTILINE)
        if not rev_match:
            continue
        revision = rev_match.group(1)
        down_revision = down_match.group(1) if down_match else None
        down_revisions[revision] = down_revision

    known = set(down_revisions.keys()) | {"0005"}
    for revision, down_revision in down_revisions.items():
        if down_revision is not None:
            assert down_revision in known, (
                f"Migration '{revision}' references unknown predecessor '{down_revision}'. " "Ensure the migration chain is continuous."
            )


def test_alembic_chain_has_single_head() -> None:
    """Legacy chain should still converge to a single head."""
    revisions, parent_refs = _collect_revision_graph(ALEMBIC_VERSIONS_DIR)
    heads = sorted(revisions - parent_refs)
    assert len(heads) == 1, f"Expected a single Alembic head, found {heads}"


def test_legacy_model_table_creations_are_frozen_to_approved_historical_set() -> None:
    """Legacy create_table coverage on model tables must not expand silently."""
    created = collect_created_tables_by_chain()
    legacy_created_model_tables = created["legacy"] & collect_model_tables()

    assert legacy_created_model_tables == APPROVED_LEGACY_MODEL_TABLE_CREATIONS

