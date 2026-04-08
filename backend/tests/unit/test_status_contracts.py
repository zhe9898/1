from __future__ import annotations

import pytest

from backend.kernel.contracts.status import canonicalize_transport_status, normalize_persisted_status


def test_trigger_status_transport_alias_normalizes_to_inactive() -> None:
    with pytest.raises(ValueError):
        canonicalize_transport_status("triggers.status", "paused")
    with pytest.raises(ValueError):
        normalize_persisted_status("triggers.status", "paused")


def test_trigger_delivery_status_legacy_accepted_normalizes_to_delivered() -> None:
    with pytest.raises(ValueError):
        canonicalize_transport_status("trigger_deliveries.status", "accepted")
    with pytest.raises(ValueError):
        normalize_persisted_status("trigger_deliveries.status", "accepted")


def test_workflow_status_legacy_canceled_normalizes_to_cancelled() -> None:
    with pytest.raises(ValueError):
        canonicalize_transport_status("workflows.status", "canceled")
    with pytest.raises(ValueError):
        normalize_persisted_status("workflows.status", "canceled")


def test_job_status_legacy_canceled_normalizes_to_cancelled() -> None:
    with pytest.raises(ValueError):
        canonicalize_transport_status("jobs.status", "canceled")
    with pytest.raises(ValueError):
        normalize_persisted_status("jobs.status", "canceled")


def test_job_attempt_status_legacy_canceled_normalizes_to_cancelled() -> None:
    with pytest.raises(ValueError):
        canonicalize_transport_status("job_attempts.status", "canceled")
    with pytest.raises(ValueError):
        normalize_persisted_status("job_attempts.status", "canceled")


def test_node_enrollment_legacy_values_normalize_to_canonical_set() -> None:
    with pytest.raises(ValueError):
        canonicalize_transport_status("nodes.enrollment_status", "active")
    with pytest.raises(ValueError):
        normalize_persisted_status("nodes.enrollment_status", "revoked")


def test_job_attempt_timeout_legacy_value_normalizes_to_timeout() -> None:
    with pytest.raises(ValueError):
        canonicalize_transport_status("job_attempts.status", "expired")
    with pytest.raises(ValueError):
        normalize_persisted_status("job_attempts.status", "expired")


def test_workflow_step_pending_legacy_value_normalizes_to_waiting() -> None:
    with pytest.raises(ValueError):
        canonicalize_transport_status("workflow_steps.status", "pending")
    with pytest.raises(ValueError):
        normalize_persisted_status("workflow_steps.status", "pending")

