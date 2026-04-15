from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.control_plane.auth.authority_boundary import (  # noqa: E402
    DIRECT_AUDIT_HELPER_ALLOWLIST,
    DIRECT_ROLE_CLAIM_ALLOWLIST,
    FORBIDDEN_DIRECT_AUDIT_HELPERS,
    export_auth_boundary_contract,
)


def _rel(path: Path, *, repo_root: Path) -> str:
    return path.relative_to(repo_root).as_posix()


def _is_current_user_role_get(node: ast.Call) -> bool:
    if not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr != "get":
        return False
    if not isinstance(node.func.value, ast.Name) or node.func.value.id != "current_user":
        return False
    if not node.args:
        return False
    first_arg = node.args[0]
    return isinstance(first_arg, ast.Constant) and first_arg.value == "role"


def _is_current_user_role_subscript(node: ast.Subscript) -> bool:
    if not isinstance(node.value, ast.Name) or node.value.id != "current_user":
        return False
    slice_node = node.slice
    return isinstance(slice_node, ast.Constant) and slice_node.value == "role"


def _expr_chain(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):
        return (*_expr_chain(node.value), node.attr)
    if isinstance(node, ast.Call):
        return _expr_chain(node.func)
    return ()


def _imported_audit_module_prefixes(tree: ast.AST) -> set[tuple[str, ...]]:
    prefixes: set[tuple[str, ...]] = set()
    target_modules = {
        "backend.platform.logging.audit",
        "backend.control_plane.admin",
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Import):
            continue
        for alias in node.names:
            if alias.name not in target_modules:
                continue
            if alias.asname:
                prefixes.add((alias.asname,))
            else:
                prefixes.add(tuple(alias.name.split(".")))
    return prefixes


def _is_forbidden_audit_helper_import(node: ast.ImportFrom) -> bool:
    return node.module in {"backend.platform.logging.audit", "backend.control_plane.admin"} and any(
        alias.name in FORBIDDEN_DIRECT_AUDIT_HELPERS for alias in node.names
    )


def _is_forbidden_audit_helper_access(node: ast.AST, *, module_prefixes: set[tuple[str, ...]]) -> bool:
    chain = _expr_chain(node)
    if len(chain) < 2:
        return False
    helper_name = chain[-1]
    if helper_name not in FORBIDDEN_DIRECT_AUDIT_HELPERS:
        return False
    return chain[:-1] in module_prefixes


def auth_boundary_violations(*, repo_root: Path | None = None) -> list[str]:
    resolved_root = (repo_root or REPO_ROOT).resolve()
    backend_root = resolved_root / "backend"
    violations: list[str] = []
    for path in sorted(backend_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = _rel(path, repo_root=resolved_root)
        if rel.startswith("backend/tests/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        module_prefixes = _imported_audit_module_prefixes(tree)
        for node in ast.walk(tree):
            if rel not in DIRECT_ROLE_CLAIM_ALLOWLIST and isinstance(node, ast.Call) and _is_current_user_role_get(node):
                violations.append(f"{rel}:{getattr(node, 'lineno', 0)}:direct current_user role claim access")
            elif rel not in DIRECT_ROLE_CLAIM_ALLOWLIST and isinstance(node, ast.Subscript) and _is_current_user_role_subscript(node):
                violations.append(f"{rel}:{getattr(node, 'lineno', 0)}:direct current_user role claim access")
            elif rel not in DIRECT_AUDIT_HELPER_ALLOWLIST and isinstance(node, ast.ImportFrom) and _is_forbidden_audit_helper_import(node):
                violations.append(f"{rel}:{getattr(node, 'lineno', 0)}:direct audit helper import bypasses log_audit")
            elif rel not in DIRECT_AUDIT_HELPER_ALLOWLIST and _is_forbidden_audit_helper_access(node, module_prefixes=module_prefixes):
                violations.append(f"{rel}:{getattr(node, 'lineno', 0)}:direct audit helper access bypasses log_audit")
    return violations


def main() -> int:
    violations = auth_boundary_violations()
    if not violations:
        return 0
    print("auth boundary violations detected:")
    print(export_auth_boundary_contract())
    for violation in violations:
        print(violation)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
