from __future__ import annotations

from backend.kernel.contracts.safe_error_projection import project_safe_error


def test_project_safe_error_uses_failure_category_mapping() -> None:
    projection = project_safe_error(
        failure_category="resource_exhausted",
        status="failed",
        error_message="oom",
    )

    assert projection is not None
    assert projection.code == "ZEN-JOB-RESOURCE-EXHAUSTED"
    assert projection.hint == "The executor ran out of required resources. Adjust placement or capacity and retry."


def test_project_safe_error_hides_raw_terminal_message_without_category() -> None:
    projection = project_safe_error(
        failure_category=None,
        status="failed",
        error_message="panic: database password=secret",
    )

    assert projection is not None
    assert projection.code == "ZEN-JOB-UNKNOWN"
    assert projection.hint == "The job failed with an internal runtime error. Review audit or runner logs for details."


def test_project_safe_error_returns_none_for_success_without_error() -> None:
    assert project_safe_error(failure_category=None, status="completed", error_message=None) is None
