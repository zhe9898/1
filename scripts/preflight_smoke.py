#!/usr/bin/env python3
from __future__ import annotations

import ast
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CORE_MODULES: list[Path] = [
    ROOT / "backend" / "control_plane" / "app" / "entrypoint.py",
    ROOT / "backend" / "control_plane" / "app" / "factory.py",
    ROOT / "backend" / "control_plane" / "app" / "router_admission.py",
    ROOT / "backend" / "control_plane" / "app" / "response_envelope.py",
    ROOT / "backend" / "api" / "auth.py",
    ROOT / "backend" / "api" / "routes.py",
    ROOT / "backend" / "api" / "settings.py",
    ROOT / "backend" / "middleware.py",
    ROOT / "backend" / "sentinel" / "topology_sentinel.py",
    ROOT / "backend" / "sentinel" / "disk_guardian.py",
    ROOT / "backend" / "platform" / "redis" / "client.py",
    ROOT / "backend" / "kernel" / "contracts" / "errors.py",
]

SECRET_FILE_PATTERNS = (
    ROOT / "runtime" / "secrets",
    ROOT / "runtime" / "tmp-compile",
    ROOT / "config" / "users.acl",
)
SECRET_LINE_PATTERN = re.compile(
    r"""(?ix)
    (?:password|secret|token|api[_-]?key)\s*[:=]\s*
    (?!.*\$\{)
    (?!.*\bnull\b)
    (?!.*\bnone\b)
    (?!.*\bchangeme\b)
    ["']?[a-z0-9!@#$%^&*._:/+-]{6,}
    """
)
IMAGE_LINE_PATTERN = re.compile(r"^\s*image:\s*(?P<ref>\S+)\s*$", re.MULTILINE)
LOCAL_BUILD_IMAGES = ("zen70-gateway", "zen70-runner-agent")


def gate_syntax() -> tuple[bool, list[str]]:
    errors: list[str] = []
    for module_path in CORE_MODULES:
        if not module_path.exists():
            errors.append(f"Missing module: {module_path.relative_to(ROOT)}")
            continue
        try:
            ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        except SyntaxError as exc:
            errors.append(f"Syntax error in {module_path.relative_to(ROOT)} at L{exc.lineno}: {exc.msg}")
    return len(errors) == 0, errors


def gate_pytest() -> tuple[bool, list[str]]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "backend/tests/unit/", "-q", "--tb=line"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode == 0:
        return True, []
    lines = (result.stdout + result.stderr).strip().splitlines()
    return False, [line for line in lines[-10:] if line.strip()]


def gate_envelope_contract() -> tuple[bool, list[str]]:
    test_file = ROOT / "backend" / "tests" / "unit" / "test_runtime_contract.py"
    if not test_file.exists():
        return False, ["test_runtime_contract.py does not exist"]
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_file), "-v", "--tb=short"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode == 0:
        return True, []
    lines = (result.stdout + result.stderr).strip().splitlines()
    failures = [line for line in lines if "FAILED" in line or "ERROR" in line]
    return False, failures[:5]


def gate_secret_hygiene() -> tuple[bool, list[str]]:
    violations: list[str] = []

    system_yaml = ROOT / "system.yaml"
    if system_yaml.exists():
        for line_number, line in enumerate(system_yaml.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if SECRET_LINE_PATTERN.search(stripped):
                violations.append(f"system.yaml:L{line_number}:{stripped[:120]}")

    for path in SECRET_FILE_PATTERNS:
        if not path.exists():
            continue
        if path.is_file():
            violations.append(f"Runtime secret artifact is committed: {path.relative_to(ROOT)}")
            continue
        files = [item.relative_to(ROOT).as_posix() for item in path.rglob("*") if item.is_file()]
        violations.extend(f"Runtime secret directory contains file: {item}" for item in files)

    return len(violations) == 0, violations


def gate_digest_pinning() -> tuple[bool, list[str]]:
    violations: list[str] = []
    for yaml_path in (ROOT / "system.yaml", ROOT / "tests" / "docker-compose.yml"):
        if not yaml_path.exists():
            continue
        text = yaml_path.read_text(encoding="utf-8")
        for match in IMAGE_LINE_PATTERN.finditer(text):
            image_ref = match.group("ref")
            if yaml_path.name == "system.yaml" and any(local in image_ref for local in LOCAL_BUILD_IMAGES):
                continue
            if "@sha256:" not in image_ref:
                violations.append(f"{yaml_path.relative_to(ROOT)}: {image_ref}")
    return len(violations) == 0, violations


def gate_frontend_build() -> tuple[bool, list[str]]:
    frontend_dir = ROOT / "frontend"
    if not (frontend_dir / "package.json").exists():
        return True, []
    if not (frontend_dir / "node_modules").exists():
        return True, ["Skipped frontend typecheck because node_modules is missing"]

    result = subprocess.run(
        ["npx", "vue-tsc", "--noEmit"],
        cwd=str(frontend_dir),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode == 0:
        return True, []
    lines = (result.stdout + result.stderr).strip().splitlines()
    return False, [line for line in lines if "error TS" in line][:10]


def gate_governance_consolidation() -> tuple[bool, list[str]]:
    """Ensure dispatch.py reaches governance through GovernanceFacade only."""

    dispatch_path = ROOT / "backend" / "api" / "jobs" / "dispatch.py"
    if not dispatch_path.exists():
        return False, ["dispatch.py does not exist"]

    violations: list[str] = []
    banned_symbols = (
        "SchedulingBackoff",
        "SchedulingMetrics",
        "TopologySpreadPolicy",
        "AdmissionController",
        "PreemptionBudgetPolicy",
        "SchedulingDecisionLogger",
    )

    text = dispatch_path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for symbol in banned_symbols:
            if symbol in stripped:
                violations.append(f"dispatch.py:L{lineno}: direct import of {symbol}; use GovernanceFacade instead")

    return len(violations) == 0, violations


GateFn = Callable[[], tuple[bool, list[str]]]
GATES: list[tuple[str, GateFn]] = [
    ("G1: core module syntax", gate_syntax),
    ("G2: unit test suite", gate_pytest),
    ("G3: runtime contract", gate_envelope_contract),
    ("G4: secret hygiene", gate_secret_hygiene),
    ("G5: image digest pinning", gate_digest_pinning),
    ("G6: frontend typecheck", gate_frontend_build),
    ("G7: governance facade consolidation", gate_governance_consolidation),
]


def main() -> int:
    print("=" * 56)
    print("  ZEN70 Preflight Smoke Gate")
    print("=" * 56)
    print()

    all_passed = True
    for gate_name, gate_fn in GATES:
        print(f"[Gate] {gate_name}...")
        try:
            passed, details = gate_fn()
        except Exception as exc:  # pragma: no cover - guardrail script
            passed = False
            details = [f"Unexpected error: {exc}"]

        if passed:
            print("  PASS")
        else:
            print("  FAIL")
            for detail in details:
                print(f"    -> {detail}")
            all_passed = False
        print()

    print("=" * 56)
    if all_passed:
        print("  PASS: all preflight gates succeeded")
    else:
        print("  FAIL: one or more preflight gates failed")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
