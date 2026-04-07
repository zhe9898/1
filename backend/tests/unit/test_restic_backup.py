from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.sentinel import restic_backup as rb


def test_extract_max_gpu_utilization_parses_labeled_metrics() -> None:
    metrics = """
# HELP nvidia_gpu_utilization GPU utilization
nvidia_gpu_utilization{gpu="0"} 12.5
nvidia_gpu_utilization{gpu="1"} 85.0
"""
    assert rb._extract_max_gpu_utilization(metrics) == 85.0


def test_check_system_load_for_backup_blocks_on_high_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rb.psutil, "cpu_percent", lambda interval=1: 10.0)
    monkeypatch.setenv("CATEGRAF_GPU_METRICS_URL", "http://metrics.internal")
    monkeypatch.setitem(
        sys.modules,
        "httpx",
        SimpleNamespace(get=lambda *_args, **_kwargs: SimpleNamespace(status_code=200, text='nvidia_gpu_utilization{gpu="0"} 90.0')),
    )

    assert rb.check_system_load_for_backup() is False


def test_load_restic_target_paths_requires_allowed_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target_dir = tmp_path / "safe"
    target_dir.mkdir()
    target_file = target_dir / "artifact.bin"
    target_file.write_text("payload", encoding="utf-8")

    monkeypatch.setenv("RESTIC_ALLOWED_ROOTS", str(target_dir))

    targets = rb.load_restic_target_paths(str(target_file))

    assert targets == [str(target_file.resolve())]


def test_load_restic_target_paths_rejects_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    allowed_root = tmp_path / "safe"
    allowed_root.mkdir()
    escaped_file = tmp_path / "secret.txt"
    escaped_file.write_text("nope", encoding="utf-8")

    monkeypatch.setenv("RESTIC_ALLOWED_ROOTS", str(allowed_root))

    with pytest.raises(ValueError):
        rb.load_restic_target_paths(str(escaped_file))
