from __future__ import annotations

from pathlib import Path

from scripts import quality_gate
from scripts.quality_gate import IAC_DRIFT_TARGETS


def test_iac_drift_targets_cover_rendered_contracts_but_not_machine_local_env() -> None:
    assert "docker-compose.yml" in IAC_DRIFT_TARGETS
    assert "config/Caddyfile" in IAC_DRIFT_TARGETS
    assert ".env" not in IAC_DRIFT_TARGETS


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
