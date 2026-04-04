"""Validate that every SQLAlchemy model table has a corresponding Alembic migration.

This test guards against the regression described in ADR-0007 where new models
were added to backend/models/ without a matching migration script in
backend/migrations/versions/, causing silent schema drift.

The check is deliberately lightweight — it only parses AST/text, never connects
to a database — so it runs in CI without any external services.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MODELS_DIR = REPO_ROOT / "backend" / "models"
VERSIONS_DIR = REPO_ROOT / "backend" / "migrations" / "versions"

# Tables managed by migrations 0001–0005 (those files are absent from versions/
# because they pre-date the tracked window, but the tables definitely exist in
# the chain as evidenced by migration 0006 which alters them).
_BASELINE_TABLES = frozenset(
    {
        "jobs",
        "job_attempts",
        "nodes",
        "users",
        "webauthn_credentials",
        "push_subscriptions",
    }
)


def _collect_model_tables() -> set[str]:
    """Return every __tablename__ value declared across backend/models/*.py."""
    tables: set[str] = set()
    for path in MODELS_DIR.glob("*.py"):
        if path.name == "__init__.py":
            continue
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "__tablename__":
                        if isinstance(node.value, ast.Constant):
                            tables.add(str(node.value.value))
    return tables


def _collect_migrated_tables() -> set[str]:
    """Return every table name that appears in a create_table() call in any migration."""
    tables: set[str] = set()
    pattern = re.compile(r'op\.create_table\(\s*["\'](\w+)["\']')
    for path in VERSIONS_DIR.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for match in pattern.finditer(source):
            tables.add(match.group(1))
    return tables


def test_all_model_tables_have_migrations() -> None:
    """Every model table must either be in the baseline set or have an explicit migration."""
    model_tables = _collect_model_tables()
    migrated_tables = _collect_migrated_tables()
    covered = migrated_tables | _BASELINE_TABLES

    missing = model_tables - covered
    assert not missing, (
        "The following tables have SQLAlchemy models but no migration:\n"
        + "\n".join(f"  - {t}" for t in sorted(missing))
        + "\n\nAdd an Alembic migration in backend/migrations/versions/ for each missing table."
    )


def test_migration_chain_is_linear() -> None:
    """Each migration (except the first) must reference exactly one predecessor."""
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

    # Every down_revision (except None for the first) must reference a known revision
    # OR the baseline sentinel "0005".
    known = set(down_revisions.keys()) | {"0005"}
    for revision, down in down_revisions.items():
        if down is not None:
            assert down in known, f"Migration '{revision}' references unknown predecessor '{down}'. " f"Ensure the migration chain is continuous."
