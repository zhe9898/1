from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.control_plane.auth.authority_boundary import (  # noqa: E402
    DIRECT_TENANT_CLAIM_ALLOWLIST,
    export_auth_boundary_contract,
)


def _rel(path: Path, *, repo_root: Path) -> str:
    return path.relative_to(repo_root).as_posix()


def _is_current_user_tenant_get(node: ast.Call) -> bool:
    if not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr != "get":
        return False
    if not _is_current_user_expr(node.func.value):
        return False
    if not node.args:
        return False
    first_arg = node.args[0]
    return isinstance(first_arg, ast.Constant) and first_arg.value == "tenant_id"


def _is_current_user_tenant_subscript(node: ast.Subscript) -> bool:
    if not _is_current_user_expr(node.value):
        return False
    slice_node = node.slice
    return isinstance(slice_node, ast.Constant) and slice_node.value == "tenant_id"


def _is_current_user_expr(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "current_user"
    if not isinstance(node, ast.BoolOp) or not isinstance(node.op, ast.Or) or len(node.values) != 2:
        return False
    left, right = node.values
    return isinstance(left, ast.Name) and left.id == "current_user" and isinstance(right, ast.Dict) and not right.keys and not right.values


def tenant_claim_violations(*, repo_root: Path | None = None) -> list[str]:
    resolved_root = (repo_root or REPO_ROOT).resolve()
    backend_root = resolved_root / "backend"
    violations: list[str] = []
    for path in sorted(backend_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = _rel(path, repo_root=resolved_root)
        if rel.startswith("backend/tests/"):
            continue
        if rel in DIRECT_TENANT_CLAIM_ALLOWLIST:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_current_user_tenant_get(node):
                violations.append(f"{rel}:{getattr(node, 'lineno', 0)}:direct current_user tenant claim access")
            elif isinstance(node, ast.Subscript) and _is_current_user_tenant_subscript(node):
                violations.append(f"{rel}:{getattr(node, 'lineno', 0)}:direct current_user tenant claim access")
    return violations


def main() -> int:
    violations = tenant_claim_violations()
    if not violations:
        return 0
    print("tenant claim boundary violations detected:")
    print(export_auth_boundary_contract())
    for violation in violations:
        print(violation)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
