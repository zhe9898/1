from __future__ import annotations

from pathlib import Path

from scripts import quality_gate
from scripts.quality_gate import BACKEND_TYPED_PATHS, IAC_DRIFT_TARGETS, OPENAPI_DRIFT_TARGETS


def test_iac_drift_targets_cover_rendered_contracts_but_not_machine_local_env() -> None:
    assert "docker-compose.yml" in IAC_DRIFT_TARGETS
    assert "config/Caddyfile" in IAC_DRIFT_TARGETS
    assert ".env" not in IAC_DRIFT_TARGETS


def test_openapi_drift_targets_include_locked_surface_snapshot() -> None:
    assert "contracts/metadata.json" in OPENAPI_DRIFT_TARGETS
    assert "docs/api/openapi_locked.json" in OPENAPI_DRIFT_TARGETS
    assert "docs/openapi.json" in OPENAPI_DRIFT_TARGETS
    assert "docs/openapi-iot.json" not in OPENAPI_DRIFT_TARGETS
    assert "docs/openapi-ops.json" not in OPENAPI_DRIFT_TARGETS
    assert "docs/openapi-full.json" not in OPENAPI_DRIFT_TARGETS


def test_backend_ci_suite_contains_explicit_audit_drift_gate() -> None:
    step_names = [step.name for step in quality_gate._backend_ci_steps()]  # noqa: SLF001

    assert "backend:architecture-governance" in step_names
    assert "backend:audit-drift" in step_names
    assert "backend:auth-tenant-boundary" in step_names
    assert "backend:cookie-boundary" in step_names
    assert "backend:development-cleanroom" in step_names
    assert "backend:tenant-claim" in step_names


def test_backend_ci_suite_contains_explicit_auth_tenant_boundary_guard() -> None:
    auth_tenant_step = next(step for step in quality_gate._backend_ci_steps() if step.name == "backend:auth-tenant-boundary")  # noqa: SLF001

    assert auth_tenant_step.command == (quality_gate.sys.executable, "tools/auth_tenant_boundary_guard.py")  # noqa: SLF001
    assert auth_tenant_step.cwd == quality_gate.REPO_ROOT  # noqa: SLF001


def test_backend_ci_suite_contains_explicit_cookie_boundary_guard() -> None:
    cookie_step = next(step for step in quality_gate._backend_ci_steps() if step.name == "backend:cookie-boundary")  # noqa: SLF001

    assert cookie_step.command == (quality_gate.sys.executable, "tools/cookie_boundary_guard.py")  # noqa: SLF001
    assert cookie_step.cwd == quality_gate.REPO_ROOT  # noqa: SLF001


def test_backend_typed_paths_track_current_five_domain_layout() -> None:
    expected_roots = {
        "backend/control_plane",
        "backend/extensions",
        "backend/kernel",
        "backend/platform",
        "backend/runtime",
    }

    assert expected_roots.issubset(BACKEND_TYPED_PATHS)
    assert "backend/api" not in BACKEND_TYPED_PATHS
    assert all(Path(path).exists() for path in BACKEND_TYPED_PATHS)


def test_backend_pip_audit_runs_without_bootstrapping_temp_pip_environment() -> None:
    pip_audit_step = next(step for step in quality_gate._backend_ci_steps() if step.name == "backend:pip-audit")  # noqa: SLF001

    assert pip_audit_step.cwd == quality_gate.BACKEND_DIR  # noqa: SLF001
    assert pip_audit_step.command == (
        "pip-audit",
        "-r",
        "requirements-core.txt",
        "--strict",
        "--desc",
        "on",
        "--no-deps",
        "--disable-pip",
    )


def test_github_actions_error_annotation_is_emitted_only_in_ci(monkeypatch, capsys) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    quality_gate._emit_github_actions_error(  # noqa: SLF001
        title="quality,gate:failure",
        message="backend:contract-drift failed\nSee git diff output.",
    )

    captured = capsys.readouterr()
    assert captured.out.strip() == "::error title=quality%2Cgate%3Afailure::backend:contract-drift failed%0ASee git diff output."


def test_github_actions_error_annotation_is_silent_outside_ci(monkeypatch, capsys) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)

    quality_gate._emit_github_actions_error("quality-gate", "backend:pytest failed")  # noqa: SLF001

    captured = capsys.readouterr()
    assert captured.out == ""


def test_pytest_failure_annotations_emit_failed_case_names(monkeypatch, capsys, tmp_path: Path) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    junit_path = tmp_path / "pytest-results.xml"
    junit_path.write_text(
        """
<testsuite tests="1" failures="1" errors="0">
  <testcase classname="backend.tests.unit.test_linux_only" name="test_runtime_contract">
    <failure message="AssertionError: linux mismatch">traceback</failure>
  </testcase>
</testsuite>
""".strip(),
        encoding="utf-8",
    )

    quality_gate._emit_pytest_failure_annotations(junit_path)  # noqa: SLF001

    captured = capsys.readouterr()
    assert "backend.tests.unit.test_linux_only::test_runtime_contract" in captured.out
    assert "AssertionError: linux mismatch" in captured.out
