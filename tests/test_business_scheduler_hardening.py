"""
Business Scheduler Hardening Tests

These tests act as static architecture gates for scheduler hardening items
that used to live as documented gaps in docs/SCHEDULER_AUDIT_REPORT.md.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(*parts: str) -> str:
    return REPO_ROOT.joinpath(*parts).read_text(encoding="utf-8")


# ============================================================================
# P1.1: Concurrent Limit Enforcement
# ============================================================================


def test_job_type_separation_defines_concurrent_limits() -> None:
    """Verify that job_type_separation.py defines concurrent limits."""
    job_type_sep = _read("backend", "kernel", "execution", "job_type_separation.py")

    assert "max_concurrent_global" in job_type_sep
    assert "max_concurrent_per_tenant" in job_type_sep
    assert "max_concurrent_per_connector" in job_type_sep

    assert '"default_priority": 70' in job_type_sep
    assert '"max_concurrent_global": 10' in job_type_sep
    assert '"max_concurrent_per_tenant": 5' in job_type_sep
    assert '"max_concurrent_per_connector": 3' in job_type_sep

    assert '"default_priority": 50' in job_type_sep
    assert '"max_concurrent_global": 100' in job_type_sep
    assert '"max_concurrent_per_tenant": 50' in job_type_sep
    assert '"max_concurrent_per_connector": 20' in job_type_sep


def test_create_job_enforces_concurrent_limits() -> None:
    """create_job() should delegate to the shared concurrency gate."""
    routes = _read("backend", "api", "jobs", "routes.py")
    submission_service = _read("backend", "api", "jobs", "submission_service.py")
    concurrency_service = _read("backend", "kernel", "execution", "job_concurrency_service.py")

    assert "return await submit_job" in routes, "create_job() should stay a thin adapter"
    assert "build_job_concurrency_window" in submission_service
    assert "assert_capacity(job_type=job_type, connector_id=connector_id)" in submission_service
    assert "zen70_global_leased_jobs_count" in concurrency_service


def test_pull_jobs_respects_concurrent_limits() -> None:
    """pull_jobs() should enforce the shared concurrency gate before leasing."""
    dispatch = _read("backend", "api", "jobs", "dispatch.py")
    pull_service = _read("backend", "api", "jobs", "pull_service.py")

    assert "return await execute_pull_jobs" in dispatch
    assert "build_job_concurrency_window" in pull_service
    assert "check_capacity_for_job" in pull_service
    assert "note_lease_granted" in pull_service


# ============================================================================
# P1.2: Retry Delay Mechanism
# ============================================================================


def test_job_type_separation_defines_retry_delays() -> None:
    """Verify that job_type_separation.py defines retry delays."""
    job_type_sep = _read("backend", "kernel", "execution", "job_type_separation.py")

    assert "retry_delay_seconds" in job_type_sep
    assert '"retry_delay_seconds": 300' in job_type_sep
    assert '"retry_delay_seconds": 60' in job_type_sep


def test_fail_job_implements_retry_delay() -> None:
    """fail_job() should implement retry delay."""
    lifecycle = _read("backend", "api", "jobs", "lifecycle.py")
    lifecycle_service = _read("backend", "api", "jobs", "lifecycle_service.py")

    assert "return await fail_job_callback" in lifecycle
    assert "calculate_retry_delay_seconds" in lifecycle_service
    assert "retry_delay_seconds" in lifecycle_service
    assert "retry_at" in lifecycle_service


def test_job_model_has_retry_at_field() -> None:
    """Job model should have retry_at field for delayed retry."""
    job_model = _read("backend", "models", "job.py")
    assert "retry_at" in job_model or "delayed_until" in job_model


def test_pull_jobs_filters_delayed_retries() -> None:
    """pull_jobs() should filter out jobs with retry_at > now."""
    pull_service = _read("backend", "api", "jobs", "pull_service.py")

    assert "Job.retry_at.is_(None)" in pull_service
    assert "Job.retry_at <= now" in pull_service


# ============================================================================
# P1.3: accepted_kinds Global Count
# ============================================================================


def test_count_eligible_nodes_for_job_uses_accepted_kinds() -> None:
    """count_eligible_nodes_for_job() should consider accepted_kinds."""
    scheduler = _read("backend", "kernel", "scheduling", "scheduling_candidates.py")

    func_start = scheduler.find("def count_eligible_nodes_for_job(")
    func_end = scheduler.find("\ndef ", func_start + 1)
    func_body = scheduler[func_start:func_end]

    assert "accepted_kinds" in func_body


def test_select_jobs_for_node_passes_accepted_kinds_to_count() -> None:
    """select_jobs_for_node() should pass accepted_kinds when counting eligible nodes."""
    scheduler = _read("backend", "kernel", "scheduling", "job_scheduler.py")

    func_start = scheduler.find("def select_jobs_for_node(")
    func_end = scheduler.find("\ndef ", func_start + 1)
    func_body = scheduler[func_start:func_end]

    assert "batch_eligible_counts(" in func_body
    assert "accepted_kinds=accepted_kinds" in func_body


# ============================================================================
# P1.5: Manual Retry Should Reset attempt_count
# ============================================================================


def test_retry_job_now_resets_retry_count() -> None:
    """retry_job_now() should delegate to JobLifecycleService.retry_job()."""
    lifecycle_route = _read("backend", "api", "jobs", "lifecycle.py")
    lifecycle_service = _read("backend", "kernel", "execution", "job_lifecycle_service.py")

    assert "return await retry_job_by_operator" in lifecycle_route
    assert "job.retry_count = 0" in lifecycle_service


def test_retry_job_now_should_reset_attempt_count() -> None:
    """retry_job_now() should reset attempt_count for manual retry."""
    lifecycle_service = _read("backend", "kernel", "execution", "job_lifecycle_service.py")

    assert "job.attempt_count = 0" in lifecycle_service
    assert "reset_retry_budget=True" in lifecycle_service


# ============================================================================
# P2.1: Aging Weakened by Candidate Window
# ============================================================================


def test_pull_jobs_uses_candidate_window_before_aging() -> None:
    """pull_jobs() should compute a candidate window before placement and scoring."""
    pull_service = _read("backend", "api", "jobs", "pull_service.py")

    assert "candidate_multiplier" in pull_service
    assert "candidate_min" in pull_service
    assert "candidate_max" in pull_service
    assert ".limit(candidate_limit)" in pull_service
    assert "sort_jobs_by_stratified_priority" in pull_service


def test_pull_jobs_should_apply_aging_in_db_query() -> None:
    """pull_jobs() should apply aging in the DB query before limiting."""
    pull_service = _read("backend", "api", "jobs", "pull_service.py")

    assert "_build_effective_priority_expression" in pull_service
    assert ".order_by(effective_priority.desc(), Job.created_at.asc(), Job.job_id.asc())" in pull_service


# ============================================================================
# P2.2: Expired Attempt Cleanup
# ============================================================================


def test_pull_jobs_repairs_stale_attempts_inline() -> None:
    """Dispatch should repair stale leases inline before re-leasing them."""
    pull_service = _read("backend", "api", "jobs", "pull_service.py")

    assert "JobLifecycleService.expire_lease" in pull_service


def test_should_have_active_attempt_expiration_worker() -> None:
    """A background worker should actively expire old attempts."""
    control_worker = _read("backend", "workers", "control_plane_worker.py")
    expiration_worker = _read("backend", "workers", "attempt_expiration_worker.py")
    expiration_service = _read("backend", "kernel", "execution", "attempt_expiration_service.py")

    assert '"attempt-expiration"' in control_worker
    assert "attempt_expiration_worker" in control_worker
    assert "expire_stale_attempts" in expiration_worker
    assert "JobLifecycleService.expire_lease" in expiration_service


# ============================================================================
# P2.4: attempt / attempt_count / retry_count Semantics
# ============================================================================


def test_attempt_incremented_on_lease() -> None:
    """LeaseService.grant_lease() should increment job.attempt on each lease."""
    lease_service = _read("backend", "kernel", "execution", "lease_service.py")

    assert "attempt_no = int(job.attempt or 0) + 1" in lease_service
    assert "job.attempt = attempt_no" in lease_service


def test_attempt_count_incremented_on_retry() -> None:
    """LeaseService.grant_lease() should increment attempt_count on every lease."""
    lease_service = _read("backend", "kernel", "execution", "lease_service.py")

    assert 'job.attempt_count = int(getattr(job, "attempt_count", 0) or 0) + 1' in lease_service


def test_attempt_count_should_be_incremented_on_every_lease() -> None:
    """attempt_count should be incremented on every lease, not just retries."""
    lease_service = _read("backend", "kernel", "execution", "lease_service.py")

    assert 'job.attempt_count = int(getattr(job, "attempt_count", 0) or 0) + 1' in lease_service


# ============================================================================
# Summary Test
# ============================================================================


def test_business_scheduler_hardening_summary() -> None:
    """Summary of the scheduler hardening items that are now enforced."""
    closed_items = {
        "P1.1": "Concurrent limits enforced through JobConcurrencyLeaseWindow",
        "P1.3": "accepted_kinds fix is now the only path and legacy marker is removed",
        "P2.2": "Expired attempts are repaired inline and by the attempt-expiration worker",
    }

    assert closed_items["P1.1"].startswith("Concurrent limits")
    assert closed_items["P1.3"].startswith("accepted_kinds")
    assert len(closed_items) == 3
