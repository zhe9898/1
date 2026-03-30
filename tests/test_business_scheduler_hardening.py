"""
Business Scheduler Hardening Tests

Tests for business scheduling constraints that are defined but not enforced.
These tests document the gap between policy definition and execution layer.

Based on: docs/SCHEDULER_AUDIT_REPORT.md
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


# ============================================================================
# P1.1: Concurrent Limit Enforcement
# ============================================================================


def test_job_type_separation_defines_concurrent_limits() -> None:
    """Verify that job_type_separation.py defines concurrent limits."""
    job_type_sep = (REPO_ROOT / "backend" / "core" / "job_type_separation.py").read_text(encoding="utf-8")

    # Verify config exists
    assert "max_concurrent_global" in job_type_sep
    assert "max_concurrent_per_tenant" in job_type_sep
    assert "max_concurrent_per_connector" in job_type_sep

    # Verify scheduled limits
    assert '"scheduled"' in job_type_sep
    assert '"max_concurrent_global": 10' in job_type_sep
    assert '"max_concurrent_per_tenant": 5' in job_type_sep
    assert '"max_concurrent_per_connector": 3' in job_type_sep

    # Verify background limits
    assert '"background"' in job_type_sep
    assert '"max_concurrent_global": 100' in job_type_sep
    assert '"max_concurrent_per_tenant": 50' in job_type_sep
    assert '"max_concurrent_per_connector": 20' in job_type_sep


@pytest.mark.xfail(reason="P1.1: Concurrent limits are defined but not enforced in create_job()")
def test_create_job_enforces_concurrent_limits() -> None:
    """EXPECTED TO FAIL: create_job() should enforce concurrent limits but doesn't."""
    routes = (REPO_ROOT / "backend" / "api" / "jobs" / "routes.py").read_text(encoding="utf-8")

    # Should check concurrent limits before creating job
    assert "max_concurrent_global" in routes, "create_job() should check max_concurrent_global"
    assert "max_concurrent_per_tenant" in routes, "create_job() should check max_concurrent_per_tenant"
    assert "max_concurrent_per_connector" in routes, "create_job() should check max_concurrent_per_connector"


@pytest.mark.xfail(reason="P1.1: Concurrent limits are defined but not enforced in pull_jobs()")
def test_pull_jobs_respects_concurrent_limits() -> None:
    """EXPECTED TO FAIL: pull_jobs() should respect concurrent limits but doesn't."""
    routes = (REPO_ROOT / "backend" / "api" / "jobs" / "routes.py").read_text(encoding="utf-8")

    # pull_jobs() should filter candidates by concurrent limits
    pull_jobs_func = routes[routes.find("async def pull_jobs(") : routes.find("async def complete_job(")]
    assert "max_concurrent" in pull_jobs_func, "pull_jobs() should check concurrent limits"


# ============================================================================
# P1.2: Retry Delay Mechanism
# ============================================================================


def test_job_type_separation_defines_retry_delays() -> None:
    """Verify that job_type_separation.py defines retry delays."""
    job_type_sep = (REPO_ROOT / "backend" / "core" / "job_type_separation.py").read_text(encoding="utf-8")

    # Verify retry delay config exists
    assert "retry_delay_seconds" in job_type_sep

    # Verify scheduled retry delay (5 minutes)
    assert '"retry_delay_seconds": 300' in job_type_sep

    # Verify background retry delay (1 minute)
    assert '"retry_delay_seconds": 60' in job_type_sep


@pytest.mark.xfail(reason="P1.2: Retry delays are defined but not implemented in fail_job()")
def test_fail_job_implements_retry_delay() -> None:
    """EXPECTED TO FAIL: fail_job() should implement retry delay but doesn't."""
    routes = (REPO_ROOT / "backend" / "api" / "jobs" / "routes.py").read_text(encoding="utf-8")

    # Should have retry_at field or similar delay mechanism
    fail_job_func = routes[routes.find("async def fail_job(") : routes.find("async def report_job_progress(")]
    assert "retry_delay_seconds" in fail_job_func, "fail_job() should use retry_delay_seconds"
    assert "retry_at" in fail_job_func or "delayed_until" in fail_job_func, "fail_job() should set retry delay timestamp"


@pytest.mark.xfail(reason="P1.2: Job model should have retry_at field but doesn't")
def test_job_model_has_retry_at_field() -> None:
    """EXPECTED TO FAIL: Job model should have retry_at field for delayed retry."""
    job_model = (REPO_ROOT / "backend" / "models" / "job.py").read_text(encoding="utf-8")

    assert "retry_at" in job_model or "delayed_until" in job_model, "Job model should have retry delay field"


@pytest.mark.xfail(reason="P1.2: pull_jobs() should filter out delayed retries but doesn't")
def test_pull_jobs_filters_delayed_retries() -> None:
    """EXPECTED TO FAIL: pull_jobs() should filter out jobs with retry_at > now."""
    routes = (REPO_ROOT / "backend" / "api" / "jobs" / "routes.py").read_text(encoding="utf-8")

    pull_jobs_func = routes[routes.find("async def pull_jobs(") : routes.find("async def complete_job(")]
    assert "retry_at" in pull_jobs_func or "delayed_until" in pull_jobs_func, "pull_jobs() should filter delayed retries"


# ============================================================================
# P1.3: accepted_kinds Global Count
# ============================================================================


def test_count_eligible_nodes_for_job_uses_accepted_kinds() -> None:
    """FIXED: count_eligible_nodes_for_job() now considers accepted_kinds (P1.3)."""
    scheduler = (REPO_ROOT / "backend" / "core" / "job_scheduler.py").read_text(encoding="utf-8")

    # Find count_eligible_nodes_for_job function
    func_start = scheduler.find("def count_eligible_nodes_for_job(")
    func_end = scheduler.find("\ndef ", func_start + 1)
    func_body = scheduler[func_start:func_end]

    # Verify it DOES use accepted_kinds
    assert "accepted_kinds" in func_body, "count_eligible_nodes_for_job() should use accepted_kinds (P1.3 fix)"


@pytest.mark.xfail(reason="P1.3: already fixed — accepted_kinds now used")
def test_count_eligible_nodes_for_job_should_use_accepted_kinds() -> None:
    """EXPECTED TO FAIL: count_eligible_nodes_for_job() should accept accepted_kinds parameter."""
    scheduler = (REPO_ROOT / "backend" / "core" / "job_scheduler.py").read_text(encoding="utf-8")

    # Find count_eligible_nodes_for_job function signature
    func_start = scheduler.find("def count_eligible_nodes_for_job(")
    func_end = scheduler.find(") -> int:", func_start)
    func_signature = scheduler[func_start:func_end]

    assert "accepted_kinds" in func_signature, "count_eligible_nodes_for_job() should accept accepted_kinds parameter"


@pytest.mark.xfail(reason="P1.3: select_jobs_for_node() should pass accepted_kinds to count_eligible_nodes_for_job()")
def test_select_jobs_for_node_passes_accepted_kinds_to_count() -> None:
    """EXPECTED TO FAIL: select_jobs_for_node() should pass accepted_kinds when counting eligible nodes."""
    scheduler = (REPO_ROOT / "backend" / "core" / "job_scheduler.py").read_text(encoding="utf-8")

    # Find select_jobs_for_node function
    func_start = scheduler.find("def select_jobs_for_node(")
    func_end = scheduler.find("\ndef ", func_start + 1)
    func_body = scheduler[func_start:func_end]

    # Should pass accepted_kinds to count_eligible_nodes_for_job
    assert "count_eligible_nodes_for_job(job, active_nodes, now=now, accepted_kinds=accepted_kinds)" in func_body, (
        "select_jobs_for_node() should pass accepted_kinds to count_eligible_nodes_for_job()"
    )


# ============================================================================
# P1.5: Manual Retry Should Reset attempt_count
# ============================================================================


def test_retry_job_now_resets_retry_count() -> None:
    """CURRENT BEHAVIOR: retry_job_now() resets retry_count."""
    routes = (REPO_ROOT / "backend" / "api" / "jobs" / "routes.py").read_text(encoding="utf-8")

    # Find retry_job_now function
    func_start = routes.find("async def retry_job_now(")
    func_end = routes.find("\n\n@router.", func_start + 1)
    func_body = routes[func_start:func_end]

    assert "retry_count = 0" in func_body, "retry_job_now() should reset retry_count"


@pytest.mark.xfail(reason="P1.5: retry_job_now() should reset attempt_count but doesn't")
def test_retry_job_now_should_reset_attempt_count() -> None:
    """EXPECTED TO FAIL: retry_job_now() should reset attempt_count for manual retry."""
    routes = (REPO_ROOT / "backend" / "api" / "jobs" / "routes.py").read_text(encoding="utf-8")

    # Find retry_job_now function
    func_start = routes.find("async def retry_job_now(")
    func_end = routes.find("\n\n@router.", func_start + 1)
    func_body = routes[func_start:func_end]

    assert "attempt_count = 0" in func_body, "retry_job_now() should reset attempt_count for manual retry"


# ============================================================================
# P2.1: Aging Weakened by Candidate Window
# ============================================================================


def test_pull_jobs_uses_candidate_window_before_aging() -> None:
    """CURRENT BEHAVIOR: pull_jobs() fetches candidate window before applying aging."""
    routes = (REPO_ROOT / "backend" / "api" / "jobs" / "routes.py").read_text(encoding="utf-8")

    # Find pull_jobs function
    func_start = routes.find("async def pull_jobs(")
    func_end = routes.find("async def complete_job(", func_start)
    func_body = routes[func_start:func_end]

    # Verify candidate window logic
    assert "candidate_limit = min(max(payload.limit * 40, 40), 200)" in func_body
    assert ".limit(candidate_limit)" in func_body
    assert "sort_jobs_by_stratified_priority" in func_body

    # Verify order: fetch candidates first, then sort with aging
    fetch_pos = func_body.find(".limit(candidate_limit)")
    sort_pos = func_body.find("sort_jobs_by_stratified_priority")
    assert fetch_pos < sort_pos, "Candidate window is fetched before aging sort (causes aging weakness)"


@pytest.mark.xfail(reason="P2.1: Aging should be applied in DB query, not after candidate window")
def test_pull_jobs_should_apply_aging_in_db_query() -> None:
    """EXPECTED TO FAIL: Ideal solution would apply aging in DB query, not after fetching."""
    routes = (REPO_ROOT / "backend" / "api" / "jobs" / "routes.py").read_text(encoding="utf-8")

    # This is a design limitation - aging is applied after fetching candidate window
    # Ideal solution would use DB-level effective priority calculation
    # For now, this test documents the limitation
    assert False, "Aging should ideally be applied in DB query to prevent starvation of low-priority old jobs"


# ============================================================================
# P2.2: Expired Attempt Cleanup
# ============================================================================


def test_expire_previous_attempt_is_passive() -> None:
    """CURRENT BEHAVIOR: _expire_previous_attempt_if_needed() is called only when job is leased again."""
    routes = (REPO_ROOT / "backend" / "api" / "jobs" / "routes.py").read_text(encoding="utf-8")

    # Find pull_jobs function
    func_start = routes.find("async def pull_jobs(")
    func_end = routes.find("async def complete_job(", func_start)
    func_body = routes[func_start:func_end]

    # Verify passive cleanup
    assert "_expire_previous_attempt_if_needed" in func_body, "Expired attempts are cleaned up passively during next lease"


@pytest.mark.xfail(reason="P2.2: Should have active background job to expire old attempts")
def test_should_have_active_attempt_expiration_worker() -> None:
    """EXPECTED TO FAIL: Should have background worker to actively expire old attempts."""
    # Search for background worker or scheduled task
    workers_dir = REPO_ROOT / "backend" / "workers"
    if workers_dir.exists():
        worker_files = list(workers_dir.glob("*.py"))
        worker_contents = [f.read_text(encoding="utf-8") for f in worker_files]
        has_expiration_worker = any("expire" in content and "attempt" in content for content in worker_contents)
        assert has_expiration_worker, "Should have background worker to expire old attempts"
    else:
        assert False, "Should have workers directory with attempt expiration worker"


# ============================================================================
# P2.4: attempt / attempt_count / retry_count Semantics
# ============================================================================


def test_attempt_incremented_on_lease() -> None:
    """CURRENT BEHAVIOR: job.attempt is incremented on each lease."""
    routes = (REPO_ROOT / "backend" / "api" / "jobs" / "routes.py").read_text(encoding="utf-8")

    func_start = routes.find("async def pull_jobs(")
    func_end = routes.find("async def complete_job(", func_start)
    func_body = routes[func_start:func_end]

    assert "job.attempt = int(job.attempt or 0) + 1" in func_body


def test_attempt_count_incremented_on_retry() -> None:
    """FIXED: job.attempt_count is now incremented on every lease in pull_jobs(), not just retry in fail_job."""
    routes = (REPO_ROOT / "backend" / "api" / "jobs" / "routes.py").read_text(encoding="utf-8")

    func_start = routes.find("async def pull_jobs(")
    func_end = routes.find("async def complete_job(", func_start)
    func_body = routes[func_start:func_end]

    assert "attempt_count" in func_body, "attempt_count should be incremented in pull_jobs (on every lease)"


@pytest.mark.xfail(reason="P2.4: attempt_count should track total attempts, not just retries")
def test_attempt_count_should_be_incremented_on_every_lease() -> None:
    """EXPECTED TO FAIL: attempt_count should be incremented on every lease, not just retries."""
    routes = (REPO_ROOT / "backend" / "api" / "jobs" / "routes.py").read_text(encoding="utf-8")

    func_start = routes.find("async def pull_jobs(")
    func_end = routes.find("async def complete_job(", func_start)
    func_body = routes[func_start:func_end]

    # Should increment attempt_count in pull_jobs, not just in fail_job
    assert "job.attempt_count" in func_body, "attempt_count should be incremented in pull_jobs()"


# ============================================================================
# Summary Test
# ============================================================================


def test_business_scheduler_hardening_summary() -> None:
    """Summary of business scheduler hardening gaps."""
    gaps = {
        "P1.1": "Concurrent limits defined but not enforced",
        "P1.2": "Retry delays defined but not implemented",
        "P1.3": "accepted_kinds not used in global eligible node count",
        "P1.5": "Manual retry doesn't reset attempt_count",
        "P2.1": "Aging weakened by candidate window",
        "P2.2": "Expired attempts cleaned up passively, not actively",
        "P2.4": "attempt/attempt_count/retry_count semantics unclear",
    }

    print("\n" + "=" * 80)
    print("Business Scheduler Hardening Gaps")
    print("=" * 80)
    for gap_id, description in gaps.items():
        print(f"{gap_id}: {description}")
    print("=" * 80)
    print(f"\nTotal gaps: {len(gaps)}")
    print("See: docs/SCHEDULER_AUDIT_REPORT.md for details")
    print("=" * 80 + "\n")
