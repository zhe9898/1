from __future__ import annotations

import ast
from pathlib import Path

from backend.kernel.governance.domain_import_fence import (
    EXTENSIONS_CONTROL_PLANE_ALLOWLIST,
    GOVERNED_BACKEND_DOMAINS,
    KERNEL_CONTROL_PLANE_ALLOWLIST,
    KERNEL_EXTENSIONS_ALLOWLIST,
    KERNEL_PLATFORM_ALLOWLIST,
    KERNEL_RUNTIME_ALLOWLIST,
    PLATFORM_KERNEL_CONTRACT_PREFIX,
    RUNTIME_CONTROL_PLANE_ALLOWLIST,
    export_backend_domain_import_fence as export_backend_domain_import_fence_contract,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _rel(path: Path, *, repo_root: Path) -> str:
    return path.relative_to(repo_root).as_posix()


def _backend_domain_imports(path: Path) -> list[tuple[str, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    imports: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or ""]
        for module in modules:
            parts = module.split(".")
            if len(parts) < 3 or parts[0] != "backend":
                continue
            if parts[1] not in GOVERNED_BACKEND_DOMAINS:
                continue
            imports.append((parts[1], module))
    return imports


def backend_domain_import_fence_violations(*, repo_root: Path | None = None) -> list[str]:
    resolved_root = (repo_root or REPO_ROOT).resolve()
    backend_root = resolved_root / "backend"
    violations: list[str] = []
    for path in sorted(backend_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = _rel(path, repo_root=resolved_root)
        parts = rel.split("/")
        if len(parts) < 3:
            continue
        src_domain = parts[1]
        if src_domain not in GOVERNED_BACKEND_DOMAINS:
            continue
        for imported_domain, imported_module in _backend_domain_imports(path):
            if imported_domain == src_domain:
                continue
            if src_domain == "kernel" and imported_domain == "control_plane" and rel in KERNEL_CONTROL_PLANE_ALLOWLIST:
                continue
            if src_domain == "kernel" and imported_domain == "runtime" and rel in KERNEL_RUNTIME_ALLOWLIST:
                continue
            if src_domain == "kernel" and imported_domain == "extensions" and rel in KERNEL_EXTENSIONS_ALLOWLIST:
                continue
            if src_domain == "kernel" and imported_domain == "platform" and rel in KERNEL_PLATFORM_ALLOWLIST:
                continue
            if src_domain == "runtime" and imported_domain == "control_plane" and rel in RUNTIME_CONTROL_PLANE_ALLOWLIST:
                continue
            if src_domain == "extensions" and imported_domain == "control_plane" and rel in EXTENSIONS_CONTROL_PLANE_ALLOWLIST:
                continue
            if src_domain == "platform" and imported_domain == "kernel" and imported_module.startswith(PLATFORM_KERNEL_CONTRACT_PREFIX):
                continue
            if src_domain == "control_plane":
                continue
            if src_domain == "runtime" and imported_domain in {"kernel", "extensions", "platform"}:
                continue
            if src_domain == "extensions" and imported_domain in {"kernel", "platform", "runtime"}:
                continue
            if src_domain == "platform" and imported_domain == "platform":
                continue
            violations.append(f"{rel}:{imported_module}")
    return violations


def export_backend_domain_import_fence() -> dict[str, object]:
    return export_backend_domain_import_fence_contract()


def main() -> int:
    violations = backend_domain_import_fence_violations()
    if not violations:
        return 0
    print("backend domain import fence violations detected:")
    for violation in violations:
        print(violation)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
