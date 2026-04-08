from __future__ import annotations

from pathlib import Path

from backend.platform.db.migration_governance import (
    APPROVED_CROSS_STREAM_TABLE_OVERLAPS,
    APPROVED_LEGACY_MODEL_TABLE_CREATIONS,
    find_cross_stream_table_overlaps,
    find_unapproved_cross_stream_table_overlaps,
    find_unapproved_legacy_model_table_creations,
    load_alembic_config_options,
    ordered_migration_chains,
    runtime_managed_migration_chains,
    validate_migration_governance,
)


def test_migration_chain_artifacts_exist() -> None:
    for chain in ordered_migration_chains():
        assert chain.versions_dir.exists(), f"missing versions dir for {chain.key}: {chain.versions_dir}"
        assert chain.config_path.exists(), f"missing config for {chain.key}: {chain.config_path}"
        assert chain.script_location.exists(), f"missing script location for {chain.key}: {chain.script_location}"


def test_cross_stream_overlaps_are_fully_governed() -> None:
    overlaps = find_cross_stream_table_overlaps()

    assert set(overlaps) == set(APPROVED_CROSS_STREAM_TABLE_OVERLAPS)
    assert not find_unapproved_cross_stream_table_overlaps()


def test_approved_overlaps_reference_valid_canonical_chains() -> None:
    overlaps = find_cross_stream_table_overlaps()

    for table_name, policy in APPROVED_CROSS_STREAM_TABLE_OVERLAPS.items():
        assert table_name in overlaps
        assert len(overlaps[table_name]) > 1
        assert policy.canonical_chain in overlaps[table_name]
        assert policy.rationale


def test_migration_governance_validation_is_clean() -> None:
    assert validate_migration_governance() == []


def test_alembic_configs_match_governed_version_tables() -> None:
    for chain in ordered_migration_chains():
        options = load_alembic_config_options(chain)
        assert options["script_location"]
        assert options["version_table"] == chain.version_table


def test_runtime_managed_chain_set_is_explicit() -> None:
    managed = runtime_managed_migration_chains()
    assert managed
    assert tuple(chain.key for chain in managed) == ("legacy", "application")


def test_legacy_model_table_creations_are_fully_governed() -> None:
    assert APPROVED_LEGACY_MODEL_TABLE_CREATIONS
    assert find_unapproved_legacy_model_table_creations() == set()


def test_update_engine_uses_governed_migration_runner() -> None:
    source = (Path(__file__).resolve().parents[3] / "scripts" / "update.py").read_text(encoding="utf-8")

    assert "MIGRATION_RUNNER" in source
    assert '"python",' in source
    assert '"backend.scripts.migrate"' in source
    assert '"--managed-only"' in source
    assert "lock_acquire_script" not in source
    assert '"-m", "alembic"' not in source


def test_overlap_application_migrations_use_schema_guard() -> None:
    root = Path(__file__).resolve().parents[3] / "backend" / "migrations" / "versions"
    guarded = {
        "0006_failure_taxonomy.py",
        "0007_user_status.py",
        "0015_tenants.py",
        "0016_connectors.py",
        "0018_job_logs.py",
        "0019_scheduling_decisions_tenant_policies.py",
        "0020_triggers.py",
        "0022_memory_facts.py",
        "0023_software_evaluations_system_logs.py",
        "0024_job_preferred_device_profile.py",
        "0025_webauthn_credential_transports.py",
        "0026_dual_chain_reconciliation.py",
        "0027_webauthn_challenge_store.py",
        "0028_canonical_trigger_workflow_statuses.py",
        "0029_canonical_job_statuses.py",
        "0030_canonical_node_attempt_step_statuses.py",
    }
    for filename in guarded:
        source = (root / filename).read_text(encoding="utf-8")
        assert "SchemaGuard" in source


def test_repo_does_not_import_migration_env_modules_outside_entrypoints() -> None:
    root = Path(__file__).resolve().parents[3]
    violations: list[str] = []
    allowed = {
        "backend/alembic/env.py",
        "backend/migrations/env.py",
    }
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if rel in allowed or "/tests/" in f"/{rel}" or "__pycache__" in rel:
            continue
        source = path.read_text(encoding="utf-8", errors="ignore")
        if "backend.alembic.env" in source or "backend.migrations.env" in source:
            violations.append(rel)
    assert violations == []

