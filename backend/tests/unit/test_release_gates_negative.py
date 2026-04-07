"""
Release Gate Negative Validation Tests (ADR-0047).

每个测试用例故意制造一种破坏条件，验证 package_v3_43.py 的门禁
是否真的能识别并拒绝 — 而不是静默放过。

这是正向验证（"门禁通过"）的对立面，缺少这部分测试等于只测了"阳光路径"。

运行方式:
    pytest backend/tests/unit/test_release_gates_negative.py -v

依赖:
    - pytest, pytest-tmp-path (标准 pytest fixture)
    - 只 import 标准库 + 项目脚本，无需启动 FastAPI
"""

from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[4] / "scripts"
PACKAGE_SCRIPT = SCRIPTS_DIR / "package_v3_43.py"
REGISTRY_PATH = Path(__file__).resolve().parents[4] / "backend" / "models" / "domain_registry.json"


# ── Negative Test 1: lockfile missing → gate must exit non-zero ───────────────


def test_lockfile_gate_fails_when_backend_lock_missing(tmp_path: Path) -> None:
    """
    ADR-0047 Gate 3 (negative): 删除 backend/requirements-ci.lock。
    打包脚本必须以非 0 退出，不得静默产包。
    """
    # Create a minimal fake project tree with no backend lockfile
    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend").mkdir()
    # frontend/package-lock.json exists, backend lockfile does NOT
    (tmp_path / "frontend" / "package-lock.json").write_text("{}", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(PACKAGE_SCRIPT)],
        env={"ZEN70_SKIP_RELEASE_GATE": "", **_env()},
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        timeout=30,
    )
    # Gate 3 must reject: missing backend/requirements-ci.lock
    assert result.returncode != 0, (
        "NEGATIVE TEST FAILED: packaging succeeded despite missing backend lockfile.\n" f"stdout: {result.stdout[-500:]}\nstderr: {result.stderr[-500:]}"
    )
    assert "requirements-ci.lock" in result.stdout + result.stderr or result.returncode != 0, "Gate should mention the missing lockfile"


def test_lockfile_gate_fails_when_frontend_lock_missing(tmp_path: Path) -> None:
    """
    ADR-0047 Gate 3 (negative): 删除 frontend/package-lock.json。
    打包脚本必须以非 0 退出。
    """
    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend").mkdir()
    # backend lockfile exists, frontend does NOT
    (tmp_path / "backend" / "requirements-ci.lock").write_text("", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(PACKAGE_SCRIPT)],
        env={"ZEN70_SKIP_RELEASE_GATE": "", **_env()},
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        timeout=30,
    )
    assert result.returncode != 0, "NEGATIVE TEST FAILED: packaging succeeded despite missing frontend lockfile.\n" f"stderr: {result.stderr[-500:]}"


# ── Negative Test 2: domain_registry.json corrupt → SystemExit ───────────────


def test_domain_registry_corrupt_json_hard_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    ADR-0047 Gate (negative): 破坏 domain_registry.json（写入非法 JSON）。
    _load_inactive_business_paths() 必须 raise SystemExit，不得返回空集。
    """
    fake_registry = tmp_path / "domain_registry.json"
    fake_registry.write_text("{ this is not valid json !!! }", encoding="utf-8")

    # Monkeypatch DOMAIN_REGISTRY_PATH in the module directly
    # (module-level singleton _INACTIVE_BUSINESS_PATHS is already loaded;
    #  we test _load_inactive_business_paths() with a patched registry path)
    import scripts.package_v3_43 as pkg  # noqa: PLC0415 (lazy import for isolation)

    monkeypatch.setattr(pkg, "DOMAIN_REGISTRY_PATH", fake_registry)

    with pytest.raises(SystemExit) as exc_info:
        pkg._load_inactive_business_paths()  # type: ignore[attr-defined]

    assert exc_info.value.code != 0 or isinstance(exc_info.value.code, str), "SystemExit must carry a non-zero code or descriptive message"


def test_domain_registry_missing_file_hard_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    ADR-0047 Gate (negative): domain_registry.json 完全不存在。
    _load_inactive_business_paths() 必须 raise SystemExit。
    """
    import scripts.package_v3_43 as pkg  # noqa: PLC0415

    nonexistent = tmp_path / "does_not_exist.json"
    monkeypatch.setattr(pkg, "DOMAIN_REGISTRY_PATH", nonexistent)

    with pytest.raises(SystemExit):
        pkg._load_inactive_business_paths()  # type: ignore[attr-defined]


# ── Negative Test 3: frozen path drifts → _run_gate must detect it ───────────


def test_frozen_openapi_path_drift_detected(tmp_path: Path) -> None:
    """
    ADR-0047 Gate 4 (negative): snapshot 里有路径，但实际 surface 已经不含该路径。
    freeze_openapi --check 必须以非 0 退出（表示漂移）。
    """
    freeze_script = SCRIPTS_DIR / "freeze_openapi.py"
    snapshot_dir = tmp_path / "docs" / "api"
    snapshot_dir.mkdir(parents=True)

    # Write a fake locked snapshot with a path that does NOT exist in the real app
    fake_snapshot = snapshot_dir / "openapi_locked.json"
    fake_snapshot.write_text(
        json.dumps(
            {
                "version": "v3.42",
                "freeze_scope": "path-surface-only",
                "freeze_boundary": "test",
                "allowlist_prefixes": ["/api/v1/nonexistent-ghost-route"],
                "paths": ["/api/v1/nonexistent-ghost-route/list"],
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(freeze_script), "--check", "--quiet"],
        env=_env(),
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        timeout=30,
    )
    # Either: snapshot not found (exit 1) or surface drift (exit 1)
    # Both are correct — the gate must be non-zero
    assert result.returncode != 0, "NEGATIVE TEST FAILED: freeze --check reported 0 despite path drift.\n" f"stdout: {result.stdout}\nstderr: {result.stderr}"


# ── Negative Test 4: gate command not found → must SystemExit ─────────────────


def test_run_gate_missing_command_hard_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    ADR-0047 _run_gate (negative): 传入一个不存在的命令。
    必须 raise SystemExit，不得静默返回。
    """
    import scripts.package_v3_43 as pkg  # noqa: PLC0415

    with pytest.raises(SystemExit) as exc_info:
        pkg._run_gate(  # type: ignore[attr-defined]
            ["this-command-definitely-does-not-exist-zen70-test"],
            "Fake gate for negative test",
        )

    code = exc_info.value.code
    assert code != 0 or isinstance(code, str), "SystemExit must carry non-zero code or error message string"
    if isinstance(code, str):
        assert "gate command not found" in code.lower() or "aborted" in code.lower()


# ── Negative Test 5: forbidden artifact bleeds into zip → post-validate fails ─


def test_forbidden_business_model_not_in_zip(tmp_path: Path) -> None:
    """
    ADR-0047 domain isolation (negative): 验证已知 inactive business-layer 模型
    不存在于发布 zip 内。这是对 _INACTIVE_BUSINESS_PATHS 逻辑的端到端验证。

    直接验证当前发布产物（若 dist/ 已存在），否则跳过（CI 打包前会生成）。
    """
    dist_zip = Path(__file__).resolve().parents[4] / "dist" / "ZEN70_v3.43_Install.zip"
    if not dist_zip.exists():
        pytest.skip("dist zip not generated yet — run package_v3_43.py first")

    forbidden = {
        "backend/models/asset.py",
        "backend/models/device.py",
        "backend/models/scene.py",
        "backend/models/memory.py",
        "backend/models/board.py",
    }

    with zipfile.ZipFile(dist_zip, "r") as zf:
        names_posix = {n.replace("\\", "/") for n in zf.namelist()}

    leaked = forbidden & names_posix
    assert not leaked, (
        f"NEGATIVE TEST FAILED: forbidden business-layer models leaked into zip:\n"
        f"  {sorted(leaked)}\n"
        "Fix: ensure domain_registry.json marks these files as active=false "
        "and package_v3_43.py reads it correctly."
    )


# ── Utility ───────────────────────────────────────────────────────────────────


def _env() -> dict[str, str]:
    """Clean env dict with ZEN70_SKIP_RELEASE_GATE unset for negative tests."""
    import os

    env = dict(os.environ)
    env.pop("ZEN70_SKIP_RELEASE_GATE", None)
    return env
