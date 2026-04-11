from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.control_plane.auth.authority_boundary import (  # noqa: E402
    DIRECT_COOKIE_POLICY_ALLOWLIST,
    export_auth_boundary_contract,
)


def _rel(path: Path, *, repo_root: Path) -> str:
    return path.relative_to(repo_root).as_posix()


def _is_request_cookies_access(node: ast.Attribute) -> bool:
    return isinstance(node.value, ast.Name) and node.value.id == "request" and node.attr == "cookies"


def _is_raw_cookie_write(node: ast.Call) -> bool:
    if not isinstance(node.func, ast.Attribute):
        return False
    return node.func.attr in {"set_cookie", "delete_cookie"}


def cookie_boundary_violations(*, repo_root: Path | None = None) -> list[str]:
    resolved_root = (repo_root or REPO_ROOT).resolve()
    backend_root = resolved_root / "backend"
    violations: list[str] = []
    for path in sorted(backend_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = _rel(path, repo_root=resolved_root)
        if rel.startswith("backend/tests/"):
            continue
        if rel in DIRECT_COOKIE_POLICY_ALLOWLIST:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and _is_request_cookies_access(node):
                violations.append(f"{rel}:{getattr(node, 'lineno', 0)}:raw request cookies access bypasses cookie policy")
            elif isinstance(node, ast.Call) and _is_raw_cookie_write(node):
                violations.append(f"{rel}:{getattr(node, 'lineno', 0)}:raw response cookie mutation bypasses cookie policy")
    return violations


def main() -> int:
    violations = cookie_boundary_violations()
    if not violations:
        return 0
    print("cookie boundary violations detected:")
    print(export_auth_boundary_contract())
    for violation in violations:
        print(violation)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
