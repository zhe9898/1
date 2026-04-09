#!/usr/bin/env python3
"""Shared repository quality gate entrypoint.

This script is the canonical source for CI/local quality suites so
workflow files and local hooks do not drift on command details.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
FRONTEND_DIR = REPO_ROOT / "frontend"

BACKEND_TYPED_PATHS = [
    "backend/api",
    "backend/control_plane",
    "backend/kernel",
    "backend/models",
    "backend/platform",
    "backend/workers",
    "backend/capabilities.py",
    "backend/background_tasks.py",
]
# `.env` is intentionally excluded from git-diff drift checks because it carries
# machine-local secret material and external runtime paths (for example
# `REDIS_ACL_FILE`). Those values are validated by compiler contract tests, but
# they are not expected to be byte-for-byte identical across Windows/Linux CI.
IAC_DRIFT_TARGETS = [
    "docker-compose.yml",
    "config/Caddyfile",
]
OPENAPI_DRIFT_TARGETS = [
    "contracts/metadata.json",
    "contracts/openapi",
    "docs/openapi-kernel.json",
    "docs/openapi-iot.json",
    "docs/openapi-ops.json",
    "docs/openapi-full.json",
    "docs/openapi.json",
]


def _node_binary(name: str) -> str:
    if os.name == "nt":
        return f"{name}.cmd"
    return name


@dataclass(frozen=True, slots=True)
class CommandStep:
    name: str
    command: tuple[str, ...]
    cwd: Path = REPO_ROOT
    extra_env: dict[str, str] | None = None
    retries: int = 0
    retry_delay_seconds: float = 0.0


def _python_test_env() -> dict[str, str]:
    return {
        "PYTHONPATH": str(REPO_ROOT),
        "JWT_SECRET_CURRENT": "ci-test-secret-32bytes!!!!!!!!!",
        "JWT_SECRET_PREVIOUS": "ci-test-prev-secret-32bytes!!!!",
        "POSTGRES_DSN": "postgresql://zen70:zen70@localhost:5432/zen70",
        "ZEN70_ENV": "",
        "DOMAIN": "localhost",
    }


def _run_step(step: CommandStep) -> int:
    env = os.environ.copy()
    if step.extra_env:
        env.update(step.extra_env)

    print(f"[quality] {step.name}")
    attempts = step.retries + 1
    for attempt in range(1, attempts + 1):
        result = subprocess.run(
            list(step.command),
            cwd=step.cwd,
            env=env,
            check=False,
        )
        if result.returncode == 0:
            return 0
        if attempt == attempts:
            return int(result.returncode)
        if step.retry_delay_seconds > 0:
            print(
                f"[quality] retrying {step.name} after attempt {attempt}/{attempts} failed",
            )
            time.sleep(step.retry_delay_seconds)
    return 1


def _git_diff_step(name: str, targets: list[str]) -> CommandStep:
    return CommandStep(
        name=name,
        command=("git", "diff", "--exit-code", "--", *targets),
        cwd=REPO_ROOT,
    )


def _backend_ci_steps() -> list[CommandStep]:
    test_env = _python_test_env()
    return [
        CommandStep("backend:black", ("black", "--check", "--diff", "backend"), cwd=REPO_ROOT),
        CommandStep("backend:isort", ("isort", "--check-only", "--diff", "backend"), cwd=REPO_ROOT),
        CommandStep("backend:flake8", ("flake8", "backend"), cwd=REPO_ROOT),
        CommandStep(
            "backend:mypy",
            (sys.executable, "-m", "mypy", "--config-file", "backend/mypy.ini", *BACKEND_TYPED_PATHS),
            cwd=REPO_ROOT,
        ),
        CommandStep(
            "backend:iac-compile",
            (sys.executable, "scripts/compiler.py", "system.yaml", "-o", "."),
            cwd=REPO_ROOT,
        ),
        _git_diff_step("backend:iac-drift", IAC_DRIFT_TARGETS),
        CommandStep(
            "backend:pip-audit",
            ("pip-audit", "-r", "requirements-core.txt", "--strict", "--desc", "on"),
            cwd=BACKEND_DIR,
        ),
        CommandStep(
            "backend:bandit-json",
            (
                "bandit",
                "-r",
                ".",
                "-x",
                "./tests",
                "-f",
                "json",
                "-o",
                "bandit-report.json",
                "--severity-level",
                "medium",
                "--confidence-level",
                "medium",
            ),
            cwd=BACKEND_DIR,
        ),
        CommandStep(
            "backend:bandit",
            (
                "bandit",
                "-r",
                ".",
                "-x",
                "./tests",
                "--severity-level",
                "medium",
                "--confidence-level",
                "medium",
            ),
            cwd=BACKEND_DIR,
        ),
        CommandStep(
            "backend:pytest",
            (
                sys.executable,
                "-m",
                "pytest",
                "backend/tests/",
                "-v",
                "--tb=short",
                "--strict-markers",
                "--cov=backend",
                "--cov-report=term-missing",
                "--cov-report=xml:backend/coverage-backend.xml",
                "--cov-fail-under=70",
            ),
            cwd=REPO_ROOT,
            extra_env=test_env,
        ),
        CommandStep(
            "backend:sre-compliance",
            (
                sys.executable,
                "-m",
                "pytest",
                "tests/test_compliance_sre.py",
                "tests/test_repo_hardening.py",
                "-v",
                "--tb=short",
                "-q",
            ),
            cwd=REPO_ROOT,
            extra_env=test_env,
        ),
        CommandStep(
            "backend:contract-capabilities",
            (
                sys.executable,
                "-m",
                "pytest",
                "backend/tests/unit/test_contract_capabilities.py",
                "-v",
                "--tb=short",
                "-q",
            ),
            cwd=REPO_ROOT,
            extra_env=test_env,
        ),
        CommandStep(
            "backend:architecture-governance",
            (
                sys.executable,
                "-m",
                "pytest",
                "backend/tests/unit/test_architecture_governance_gates.py",
                "-v",
                "--tb=short",
                "-q",
            ),
            cwd=REPO_ROOT,
            extra_env=test_env,
        ),
        CommandStep(
            "backend:generate-contracts",
            (sys.executable, "scripts/generate_contracts.py"),
            cwd=REPO_ROOT,
            extra_env=test_env,
        ),
        _git_diff_step("backend:contract-drift", OPENAPI_DRIFT_TARGETS),
    ]


def _frontend_ci_steps() -> list[CommandStep]:
    return [
        CommandStep(
            "frontend:npm-audit",
            (_node_binary("npm"), "audit", "--audit-level=high"),
            cwd=FRONTEND_DIR,
            retries=2,
            retry_delay_seconds=2.0,
        ),
        CommandStep(
            "frontend:lint",
            (_node_binary("npm"), "run", "lint"),
            cwd=FRONTEND_DIR,
        ),
        CommandStep(
            "frontend:test-coverage",
            (_node_binary("npm"), "run", "test:coverage"),
            cwd=FRONTEND_DIR,
        ),
        CommandStep(
            "frontend:build",
            (_node_binary("npm"), "run", "build"),
            cwd=FRONTEND_DIR,
        ),
    ]


SUITES: dict[str, Callable[[], list[CommandStep]]] = {
    "backend-ci": _backend_ci_steps,
    "frontend-ci": _frontend_ci_steps,
}


def main(argv: list[str]) -> int:
    suite_names = argv[1:] or ["backend-ci"]
    for suite_name in suite_names:
        if suite_name not in SUITES:
            available = ", ".join(sorted(SUITES))
            print(f"unknown suite: {suite_name}. available: {available}", file=sys.stderr)
            return 2

    for suite_name in suite_names:
        print(f"[quality] suite={suite_name}")
        for step in SUITES[suite_name]():
            if _run_step(step) != 0:
                print(f"[quality] failed at {step.name}", file=sys.stderr)
                return 1
    print("[quality] all suites passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
