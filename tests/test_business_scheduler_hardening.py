"""
Business Scheduler Hardening Tests

These tests now act as architecture gates for the scheduler hardening items
that used to live as documented gaps in docs/SCHEDULER_AUDIT_REPORT.md.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


# ============================================================================
# P1.1: Concurrent Limit Enforcement
# ============================================================================


def test_job_type_separation_defines_concurrent_limits() -> None:
    """Verify that job_type_separation.py defines concurrent limits."""
    job_type_sep = (REPO_ROOT / "backend" / "core" / "job_type_separation.py").read_text(encoding="utf-8")

    assert "max_concurrent_global" in job_type_sep
    assert "max_concurrent_per_tenant" in job_type_sep
    assert "max_concurrent_per_connector" in job_type_sep

    assert '"scheduled"' in job_type_sep
    assert '"max_concurrent_global": 10' in job_type_sep
    assert '"max_concurrent_per_tenant": 5' in job_type_sep
    assert '"max_concurrent_per_connector": 3' in job_type_sep

    assert '"background"' in job_type_sep
    assert '"max_concurrent_global": 100' in job_type_sep
    assert '"max_concurrent_per_tenant": 50' in job_type_sep
    assert '"max_concurrent_per_connector": 20' in job_type_sep


def test_create_job_enforces_concurrent_limits() -> None:
    """create_job() should delegate to the shared concurrency gate."""
    routes = (REPO_ROOT / "backend" / "api" / "jobs" / "routes.py").read_text(encoding="utf-8")
    submission = (REPO_ROOT / "backend" / "api" / "jobs" / "submission.py").read_text(encoding="utf-8")
    concurrency_service = (REPO_ROOT / "backend" / "core" / "job_concurrency_service.py").read_text(encoding="utf-8")

    assert "return await submit_job" in routes, "create_job() should stay a thin adapter"
    assert "build_job_concurrency_window" in submission
    assert "assert_capacity(" in submission
    assert "zen70_global_leased_jobs_count" in concurrency_service


def test_pull_jobs_respects_concurrent_limits() -> None:
    """pull_jobs() should enforce the shared concurrency gate before leasing."""
    dispatch = (REPO_ROOT / "backend" / "api" / "jobs" / "dispatch.py").read_text(encoding="utf-8")

    pull_jobs_func = dispatch[dispatch.find("async def pull_jobs("):]
    assert "build_job_concurrency_window" in pull_jobs_func
    assert "check_capacity_for_job" in pull_jobs_func
    assert "note_lease_granted" in pull_jobs_func


# ============================================================================
# P1.2: Retry Delay Mechanism
# ============================================================================


def test_job_type_separation_defines_retry_delays() -> None:
    """Verify that job_type_separation.py defines retry delays."""
    job_type_sep = (REPO_ROOT / "backend" / "core" / "job_type_separation.py").read_text(encoding="utf-8")

    assert "retry_delay_seconds" in job_type_sep
    assert '"retry_delay_seconds": 300' in job_type_sep
    assert '"retry_delay_seconds": 60' in job_type_sep


def test_fail_job_implements_retry_delay() -> None:
    """fail_job() should implement retry delay."""
    lifecycle = (REPO_ROOT / "backend" / "api" / "jobs" / "lifecycle.py").read_text(encoding="utf-8")

    fail_job_func = lifecycle[lifecycle.find("async def fail_job(") : lifecycle.find("async def report_job_progress(")]
    assert "retry_delay_seconds" in fail_job_func, "fail_job() should use retry_delay_seconds"
    assert "retry_at" in fail_job_func or "delayed_until" in fail_job_func, "fail_job() should set retry delay timestamp"


def test_job_model_has_retry_at_field() -> None:
    """Job model should have retry_at field for delayed retry."""
    job_model = (REPO_ROOT / "backend" / "models" / "job.py").read_text(encoding="utf-8")

    assert "retry_at" in job_model or "delayed_until" in job_model, "Job model should have retry delay field"


def test_pull_jobs_filters_delayed_retries() -> None:
    """pull_jobs() should filter out jobs with retry_at > now."""
    dispatch = (REPO_ROOT / "backend" / "api" / "jobs" / "dispatch.py").read_text(encoding="utf-8")

    pull_jobs_func = dispatch[dispatch.find("async def pull_jobs("):]
    assert "retry_at" in pull_jobs_func or "delayed_until" in pull_jobs_func, "pull_jobs() should filter delayed retries"


# ============================================================================
# P1.3: accepted_kinds Global Count
# ============================================================================


def test_count_eligible_nodes_for_job_uses_accepted_kinds() -> None:
    """count_eligible_nodes_for_job() should consider accepted_kinds."""
    scheduler = (REPO_ROOT / "backend" / "core" / "scheduling_candidates.py").read_text(encoding="utf-8")

    func_start = scheduler.find("def count_eligible_nodes_for_job(")
    func_end = scheduler.find("\ndef ", func_start + 1)
    func_body = scheduler[func_start:func_end]

    assert "accepted_kinds" in func_body, "count_eligible_nodes_for_job() should use accepted_kinds"


def test_select_jobs_for_node_passes_accepted_kinds_to_count() -> None:
    """select_jobs_for_node() should pass accepted_kinds when counting eligible nodes."""
    scheduler = (REPO_ROOT / "backend" / "core" / "job_scheduler.py").read_text(encoding="utf-8")

    func_start = scheduler.find("def select_jobs_for_node(")
    func_end = scheduler.find("\ndef ", func_start + 1)
    func_body = scheduler[func_start:func_end]

    assert "batch_eligible_counts(" in func_body
    assert "accepted_kinds=accepted_kinds" in func_body, (
        "select_jobs_for_node() should pass accepted_kinds to eligible-node counting"
    )


# ============================================================================
# P1.5: Manual Retry Should Reset attempt_count
# ============================================================================


def test_retry_job_now_resets_retry_count() -> None:
    """retry_job_now() should delegate to JobLifecycleService.retry_job()."""
    lifecycle_route = (REPO_ROOT / "backend" / "api" / "jobs" / "lifecycle.py").read_text(encoding="utf-8")
    lifecycle_service = (REPO_ROOT / "backend" / "core" / "job_lifecycle_service.py").read_text(encoding="utf-8")

    assert "JobLifecycleService.retry_job" in lifecycle_route
    assert "job.retry_count = 0" in lifecycle_service, "retry service should reset retry_count"


def test_retry_job_now_should_reset_attempt_count() -> None:
    """retry_job_now() should reset attempt_count for manual retry."""
    lifecycle_service = (REPO_ROOT / "backend" / "core" / "job_lifecycle_service.py").read_text(encoding="utf-8")
    assert "job.attempt_count = 0" in lifecycle_service


# ============================================================================
# P2.1: Aging Weakened by Candidate Window
# ============================================================================


def test_pull_jobs_uses_candidate_window_before_aging() -> None:
    """pull_jobs() fetches candidate window before applying aging."""
    dispatch = (REPO_ROOT / "backend" / "api" / "jobs" / "dispatch.py").read_text(encoding="utf-8")

    func_start = dispatch.find("async def pull_jobs(")
    func_body = dispatch[func_start:]

    assert "_dc.candidate_multiplier" in func_body or "_dc.candidate_min" in func_body
    assert ".limit(candidate_limit)" in func_body
    assert "sort_jobs_by_stratified_priority" in func_body

    fetch_pos = func_body.find(".limit(candidate_limit)")
    sort_pos = func_body.find("sort_jobs_by_stratified_priority")
    assert fetch_pos < sort_pos, "Candidate window is fetched before aging sort"


def test_pull_jobs_should_apply_aging_in_db_query() -> None:
    """pull_jobs() should apply aging in DB query before ordering and limiting."""
    dispatch = (REPO_ROOT / "backend" / "api" / "jobs" / "dispatch.py").read_text(encoding="utf-8")
    assert "_effective_priority" in dispatch
    assert ".order_by(_effective_priority.desc(), Job.created_at.asc())" in dispatch


# ============================================================================
# P2.2: Expired Attempt Cleanup
# ============================================================================


def test_pull_jobs_repairs_stale_attempts_inline() -> None:
    """Dispatch should repair stale leases inline before re-leasing them."""
    dispatch = (REPO_ROOT / "backend" / "api" / "jobs" / "dispatch.py").read_text(encoding="utf-8")

    func_start = dispatch.find("async def pull_jobs(")
    func_body = dispatch[func_start:]

    assert "JobLifecycleService.expire_lease" in func_body


def test_should_have_active_attempt_expiration_worker() -> None:
    """A background worker should actively expire old attempts."""
    control_worker = (REPO_ROOT / "backend" / "workers" / "control_plane_worker.py").read_text(encoding="utf-8")
    expiration_worker = (REPO_ROOT / "backend" / "workers" / "attempt_expiration_worker.py").read_text(encoding="utf-8")
    expiration_service = (REPO_ROOT / "backend" / "core" / "attempt_expiration_service.py").read_text(encoding="utf-8")

    assert '"attempt-expiration"' in control_worker
    assert "attempt_expiration_worker" in control_worker
    assert "expire_stale_attempts" in expiration_worker
    assert "JobLifecycleService.expire_lease" in expiration_service


# ============================================================================
# P2.4: attempt / attempt_count / retry_count Semantics
# ============================================================================


def test_attempt_incremented_on_lease() -> None:
    """LeaseService.grant_lease() should increment job.attempt on each lease."""
    lease_service = (REPO_ROOT / "backend" / "core" / "lease_service.py").read_text(encoding="utf-8")
    assert "attempt_no = int(job.attempt or 0) + 1" in lease_service
    assert "job.attempt = attempt_no" in lease_service


def test_attempt_count_incremented_on_retry() -> None:
    """LeaseService.grant_lease() should increment attempt_count on every lease."""
    lease_service = (REPO_ROOT / "backend" / "core" / "lease_service.py").read_text(encoding="utf-8")
    assert "job.attempt_count = int(getattr(job, \"attempt_count\", 0) or 0) + 1" in lease_service


def test_attempt_count_should_be_incremented_on_every_lease() -> None:
    """attempt_count should be incremented on every lease, not just retries."""
    lease_service = (REPO_ROOT / "backend" / "core" / "lease_service.py").read_text(encoding="utf-8")
    assert "job.attempt_count = int(getattr(job, \"attempt_count\", 0) or 0) + 1" in lease_service


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

    print("\n" + "=" * 80)
    print("Business Scheduler Hardening Closure")
    print("=" * 80)
    for gap_id, description in closed_items.items():
        print(f"{gap_id}: {description}")
    print("=" * 80)
    print(f"\nClosed items: {len(closed_items)}")
    print("See: docs/SCHEDULER_AUDIT_REPORT.md for historical context")
    print("=" * 80 + "\n")
