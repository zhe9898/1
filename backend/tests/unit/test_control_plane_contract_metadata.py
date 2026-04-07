from __future__ import annotations

import json
from pathlib import Path


def _control_plane_migration_text() -> str:
    path = Path(__file__).resolve().parents[3] / "backend" / "alembic" / "versions" / "9f2c7a1d4e61_control_plane_schema_hardening.py"
    return path.read_text(encoding="utf-8")


def _trigger_migration_text() -> str:
    path = Path(__file__).resolve().parents[3] / "backend" / "alembic" / "versions" / "b7c8d9e0f1a2_trigger_control_plane_tables.py"
    return path.read_text(encoding="utf-8")


def _queue_lane_migration_text() -> str:
    path = Path(__file__).resolve().parents[3] / "backend" / "alembic" / "versions" / "e6f7a8b9c0d1_queue_lane_worker_pool_contracts.py"
    return path.read_text(encoding="utf-8")


def _contracts_metadata() -> dict[str, object]:
    path = Path(__file__).resolve().parents[3] / "contracts" / "metadata.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_control_plane_schema_migration_covers_node_and_job_protocol_columns() -> None:
    rendered = _control_plane_migration_text()

    assert "_ensure_jobs_schema" in rendered
    assert '"idempotency_key"' in rendered
    assert '"lease_token"' in rendered
    assert '"attempt"' in rendered
    assert '"priority"' in rendered
    assert '"target_os"' in rendered
    assert '"required_capabilities"' in rendered
    assert "_ensure_nodes_schema" in rendered
    assert '"executor"' in rendered
    assert '"os"' in rendered
    assert '"arch"' in rendered
    assert '"protocol_version"' in rendered
    assert '"lease_version"' in rendered
    assert '"agent_version"' in rendered
    assert '"max_concurrency"' in rendered
    assert '"drain_status"' in rendered
    assert '"health_reason"' in rendered
    assert '"auth_token_hash"' in rendered
    assert '"auth_token_version"' in rendered
    assert '"enrollment_status"' in rendered
    assert "_ensure_connectors_schema" in rendered
    assert '"last_test_ok"' in rendered
    assert '"last_test_status"' in rendered
    assert '"last_test_message"' in rendered
    assert '"last_invoke_status"' in rendered
    assert '"last_invoke_message"' in rendered
    assert '"last_invoke_job_id"' in rendered
    assert "_ensure_job_attempts_schema" in rendered
    assert '"ux_jobs_tenant_idempotency_key"' in rendered
    assert '"ux_nodes_tenant_node_id"' in rendered


def test_trigger_control_plane_migration_covers_trigger_tables() -> None:
    rendered = _trigger_migration_text()

    assert "_ensure_triggers_schema" in rendered
    assert "_ensure_trigger_deliveries_schema" in rendered
    assert '"triggers"' in rendered
    assert '"trigger_deliveries"' in rendered
    assert '"trigger_id"' in rendered
    assert '"delivery_id"' in rendered
    assert '"ux_triggers_tenant_trigger_id"' in rendered
    assert '"ux_trigger_deliveries_tenant_trigger_idempotency"' in rendered


def test_queue_lane_migration_covers_job_and_node_worker_contracts() -> None:
    rendered = _queue_lane_migration_text()

    assert "_ensure_job_queue_contract_schema" in rendered
    assert "_ensure_node_worker_pool_schema" in rendered
    assert '"queue_class"' in rendered
    assert '"worker_pool"' in rendered
    assert '"worker_pools"' in rendered
    assert '"ix_jobs_queue_class"' in rendered
    assert '"ix_jobs_worker_pool"' in rendered


def test_contracts_metadata_indexes_trigger_contracts() -> None:
    metadata = _contracts_metadata()
    contracts = metadata["contracts"]
    contracts_root = Path(__file__).resolve().parents[3] / "contracts"

    assert "triggers" in contracts
    assert "triggers/README.md" in contracts["triggers"]
    assert "triggers/manual-trigger.example.json" in contracts["triggers"]
    for relative_path in contracts["triggers"]:
        assert (contracts_root / relative_path).exists()


def test_contracts_metadata_indexes_reservation_contracts() -> None:
    metadata = _contracts_metadata()
    contracts = metadata["contracts"]
    contracts_root = Path(__file__).resolve().parents[3] / "contracts"

    assert "reservations" in contracts
    assert "reservations/README.md" in contracts["reservations"]
    assert "reservations/manual-reservation.example.json" in contracts["reservations"]
    for relative_path in contracts["reservations"]:
        assert (contracts_root / relative_path).exists()
