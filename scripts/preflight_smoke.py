#!/usr/bin/env python3
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CORE_MODULES: list[Path] = [
    ROOT / "backend" / "api" / "main.py",
    ROOT / "backend" / "api" / "auth.py",
    ROOT / "backend" / "api" / "routes.py",
    ROOT / "backend" / "api" / "settings.py",
    ROOT / "backend" / "middleware.py",
    ROOT / "backend" / "sentinel" / "topology_sentinel.py",
    ROOT / "backend" / "sentinel" / "disk_guardian.py",
    ROOT / "backend" / "core" / "redis_client.py",
    ROOT / "backend" / "core" / "errors.py",
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
            errors.append(f"模块不存在: {module_path.relative_to(ROOT)}")
            continue
        try:
            ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        except SyntaxError as exc:
            errors.append(f"语法错误 ({module_path.relative_to(ROOT)}): L{exc.lineno}: {exc.msg}")
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
        return False, ["test_runtime_contract.py 不存在"]
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
            violations.append(f"禁止运行态明文密钥产物: {path.relative_to(ROOT)}")
            continue
        files = [item.relative_to(ROOT).as_posix() for item in path.rglob("*") if item.is_file()]
        violations.extend(f"禁止运行态明文密钥目录产物: {item}" for item in files)

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
        return True, ["跳过: node_modules 不存在（需要先 npm install）"]

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


GATES: list[tuple[str, object]] = [
    ("G1: 核心模块语法检查", gate_syntax),
    ("G2: pytest 全量单测", gate_pytest),
    ("G3: Runtime Contract", gate_envelope_contract),
    ("G4: 运行态密钥治理", gate_secret_hygiene),
    ("G5: 外部镜像 digest pin", gate_digest_pinning),
    ("G6: 前端类型检查", gate_frontend_build),
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
            passed, details = gate_fn()  # type: ignore[operator]
        except Exception as exc:
            passed = False
            details = [f"异常: {exc}"]

        if passed:
            print("  PASS")
        else:
            print("  FAIL")
            for detail in details:
                print(f"    -> {detail}")
            all_passed = False
        print()

    print("=" * 56)
    print("  PASS: 所有门禁通过，可继续发布" if all_passed else "  FAIL: 存在阻断门禁，停止发布")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
