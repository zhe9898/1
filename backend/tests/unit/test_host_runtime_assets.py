from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.host_runtime_assets import _materialize_host_build_plan


def test_materialize_host_build_plan_invokes_go_build(monkeypatch, tmp_path: Path) -> None:
    source_dir = tmp_path / "runner-agent"
    source_dir.mkdir()
    (source_dir / "main.go").write_text("package main\nfunc main() {}\n", encoding="utf-8")
    output_path = tmp_path / "runtime" / "host" / "bin" / "runner-agent"
    calls: list[tuple[list[str], str]] = []

    def fake_which(name: str) -> str | None:
        assert name == "go"
        return "/usr/bin/go"

    def fake_run(
        command: list[str],
        *,
        cwd: str,
        env: dict[str, str],
        check: bool,
        timeout: int,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        del env, check, timeout, capture_output, text
        calls.append((command, cwd))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("binary", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("scripts.host_runtime_assets.shutil.which", fake_which)
    monkeypatch.setattr("scripts.host_runtime_assets.subprocess.run", fake_run)

    _materialize_host_build_plan(
        "runner-agent",
        {
            "kind": "go_binary",
            "source_dir": str(source_dir),
            "output_path": str(output_path),
            "package": "./cmd/runner-agent",
            "env": {"CGO_ENABLED": "0"},
            "trimpath": True,
            "ldflags": "-s -w",
        },
    )

    assert calls == [
        (
            [
                "/usr/bin/go",
                "build",
                "-trimpath",
                "-ldflags",
                "-s -w",
                "-o",
                str(output_path),
                "./cmd/runner-agent",
            ],
            str(source_dir),
        )
    ]
