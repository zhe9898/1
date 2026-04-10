from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
QUALITY_GATE_PATH = REPO_ROOT / "scripts" / "quality_gate.py"


def _load_quality_gate_module():
    spec = importlib.util.spec_from_file_location("zen70_quality_gate", QUALITY_GATE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_parse_cobertura_coverage_summary_reads_lines_and_branches(tmp_path: Path) -> None:
    quality_gate = _load_quality_gate_module()
    report_path = tmp_path / "cobertura-coverage.xml"
    report_path.write_text(
        """<?xml version="1.0" ?>
<coverage lines-covered="29" lines-valid="50" branches-covered="16" branches-valid="25" />
""",
        encoding="utf-8",
    )

    summary = quality_gate._parse_cobertura_coverage_summary(report_path)

    assert summary is not None
    assert summary.lines.covered == 29
    assert summary.lines.total == 50
    assert summary.lines.percent == pytest.approx(58.0)
    assert summary.branches is not None
    assert summary.branches.covered == 16
    assert summary.branches.total == 25
    assert summary.branches.percent == pytest.approx(64.0)


def test_emit_frontend_coverage_summary_prints_compact_summary(tmp_path: Path, capsys) -> None:
    quality_gate = _load_quality_gate_module()
    report_path = tmp_path / "cobertura-coverage.xml"
    report_path.write_text(
        """<?xml version="1.0" ?>
<coverage lines-covered="29" lines-valid="50" branches-covered="16" branches-valid="25" />
""",
        encoding="utf-8",
    )

    quality_gate._emit_frontend_coverage_summary(report_path)

    output = capsys.readouterr().out.strip()
    assert output.startswith("[quality] frontend:coverage-summary lines 58.0% (29/50) | branches 64.0% (16/25)")
    assert str(report_path) in output


def test_run_step_emits_frontend_coverage_summary_after_success(monkeypatch) -> None:
    quality_gate = _load_quality_gate_module()
    step = quality_gate.CommandStep("frontend:test-coverage", ("npm", "run", "test:coverage"))
    captured_paths: list[Path] = []

    monkeypatch.setattr(quality_gate.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(quality_gate, "_emit_frontend_coverage_summary", lambda report_path=quality_gate.FRONTEND_COVERAGE_COBERTURA_PATH: captured_paths.append(report_path))

    assert quality_gate._run_step(step) == 0
    assert captured_paths == [quality_gate.FRONTEND_COVERAGE_COBERTURA_PATH]
