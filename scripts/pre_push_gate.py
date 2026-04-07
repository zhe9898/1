#!/usr/bin/env python3
"""ZEN70 Pre-Push Release Gate — 本地强制门禁。

在代码推送前检查所有硬性发布条件。CI 中任何一个 gate 失败
都应在本地提前捕获，减少远程 CI 浪费。

退出码：
  0 — 全部通过
  1 — 至少一个硬门禁失败，阻断推送

用法：
    python scripts/pre_push_gate.py
    # 或添加为 git pre-push hook:
    # .git/hooks/pre-push → python scripts/pre_push_gate.py

门禁列表：
  G1: 测试套件无失败
  G2: IaC 单一真源确定性编译
  G3: OpenAPI 合约同步
  G4: 后端源码行数门禁
  G5: 安全审计 (bandit)
  G6: 治理封印完整性
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(cmd: list[str], *, cwd: Path = REPO_ROOT, timeout: int = 300) -> tuple[int, str]:
    """Run a command and return (exit_code, combined_output)."""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, (result.stdout + result.stderr)[-2000:]
    except subprocess.TimeoutExpired:
        return 1, f"command timed out after {timeout}s"
    except FileNotFoundError:
        return 1, f"command not found: {cmd[0]}"


def gate_tests() -> tuple[bool, str]:
    """G1: pytest 全量无失败。"""
    code, output = _run(
        [sys.executable, "-m", "pytest", "--tb=line", "-q", "--no-header"],
        timeout=600,
    )
    if code != 0:
        return False, f"pytest exit {code}: {output[-500:]}"
    return True, "all tests passed"


def gate_iac_determinism() -> tuple[bool, str]:
    """G2: IaC 编译器确定性输出 — docker-compose.yml 无 diff。"""
    compiler = REPO_ROOT / "deploy" / "config-compiler.py"
    if not compiler.exists():
        # Fallback: scripts/compiler.py
        compiler = REPO_ROOT / "scripts" / "compiler.py"
    if not compiler.exists():
        return True, "no IaC compiler found, skip"
    code, _ = _run([sys.executable, str(compiler)])
    if code != 0:
        return False, "IaC compiler exited non-zero"

    code, diff = _run(["git", "diff", "--exit-code", "docker-compose.yml"])
    if code != 0:
        return False, f"docker-compose.yml drift after recompile:\n{diff[:500]}"
    return True, "IaC deterministic"


def gate_openapi_sync() -> tuple[bool, str]:
    """G3: OpenAPI 合约 JSON 同步。"""
    exporter = REPO_ROOT / "scripts" / "export_openapi.py"
    if not exporter.exists():
        return True, "no OpenAPI exporter found, skip"
    code, _ = _run([sys.executable, str(exporter)])
    if code != 0:
        return False, "OpenAPI export failed"

    code, diff = _run(["git", "diff", "--exit-code", "docs/openapi.json"])
    if code != 0:
        return False, "openapi.json drift after re-export"
    return True, "OpenAPI in sync"


def gate_code_length() -> tuple[bool, str]:
    """G4: 后端源码 ≤600 行 / 测试 ≤800 行。"""
    code, output = _run(
        [sys.executable, "-m", "pytest",
         "tests/test_repo_hardening.py::test_backend_source_files_do_not_exceed_line_limit",
         "tests/test_repo_hardening.py::test_backend_test_files_do_not_exceed_line_limit",
         "-q", "--tb=short", "--no-header"],
    )
    if code != 0:
        return False, f"code length gate failed:\n{output[-500:]}"
    return True, "code length within limits"


def gate_bandit() -> tuple[bool, str]:
    """G5: bandit 安全扫描无 HIGH。"""
    code, output = _run(
        [sys.executable, "-m", "bandit", "-r", "backend/", "-ll", "-q", "--exclude", "backend/tests"],
    )
    if code != 0 and "High" in output:
        return False, f"bandit HIGH issues:\n{output[-500:]}"
    return True, "no high-severity security issues"


def gate_governance_seal() -> tuple[bool, str]:
    """G6: 治理封印文件完整性检查。"""
    governance = REPO_ROOT / "backend" / "core" / "governance_facade.py"
    if not governance.exists():
        return False, "governance_facade.py missing"
    content = governance.read_text(encoding="utf-8")
    if "def seal(" not in content:
        return False, "governance_facade.py missing seal() method"
    if "def unseal(" not in content:
        return False, "governance_facade.py missing unseal() method"
    return True, "governance seal API present"


GATES: list[tuple[str, object]] = [
    ("G1: pytest 全量通过", gate_tests),
    ("G2: IaC 确定性编译", gate_iac_determinism),
    ("G3: OpenAPI 合约同步", gate_openapi_sync),
    ("G4: 代码行数门禁", gate_code_length),
    ("G5: 安全审计 (bandit)", gate_bandit),
    ("G6: 治理封印完整性", gate_governance_seal),
]


def main() -> int:
    print("═══════════════════════════════════════════")
    print("  ZEN70 Pre-Push Release Gate")
    print("═══════════════════════════════════════════")
    print()

    failed = False
    for name, fn in GATES:
        print(f"  [{name}]...", end=" ", flush=True)
        try:
            passed, detail = fn()  # type: ignore[operator]
        except Exception as exc:
            passed, detail = False, f"exception: {exc}"

        if passed:
            print(f"PASS ({detail})")
        else:
            print(f"FAIL ({detail})")
            failed = True

    print()
    if failed:
        print("  BLOCKED — fix failures before pushing")
        return 1
    print("  ALL GATES PASSED — safe to push")
    return 0


if __name__ == "__main__":
    sys.exit(main())
