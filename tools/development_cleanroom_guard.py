from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.kernel.governance.development_cleanroom import (  # noqa: E402
    CLEANROOM_EXCLUDED_FILES,
    CLEANROOM_FILE_EXTENSIONS,
    CLEANROOM_IGNORED_PATH_PREFIXES,
    FORBIDDEN_TRANSITIONAL_MARKERS,
    GOVERNED_CLEANROOM_ROOTS,
    export_development_cleanroom_contract,
)


def _rel(path: Path, *, repo_root: Path) -> str:
    return path.relative_to(repo_root).as_posix()


def _is_governed_source(path: Path, *, repo_root: Path) -> bool:
    rel = _rel(path, repo_root=repo_root)
    if "__pycache__" in path.parts:
        return False
    if rel in CLEANROOM_EXCLUDED_FILES:
        return False
    if any(rel.startswith(prefix) for prefix in CLEANROOM_IGNORED_PATH_PREFIXES):
        return False
    return path.suffix in CLEANROOM_FILE_EXTENSIONS


def development_cleanroom_violations(*, repo_root: Path | None = None) -> list[str]:
    resolved_root = (repo_root or REPO_ROOT).resolve()
    violations: list[str] = []

    for root_name in GOVERNED_CLEANROOM_ROOTS:
        root_path = resolved_root / root_name
        if not root_path.exists():
            continue
        for path in sorted(root_path.rglob("*")):
            if not path.is_file() or not _is_governed_source(path, repo_root=resolved_root):
                continue
            rel = _rel(path, repo_root=resolved_root)
            text = path.read_text(encoding="utf-8-sig", errors="ignore")
            lowered_lines = [line.lower() for line in text.splitlines()]
            for marker_key, raw_markers in FORBIDDEN_TRANSITIONAL_MARKERS.items():
                for marker in raw_markers:
                    lowered_marker = marker.lower()
                    for line_number, lowered_line in enumerate(lowered_lines, 1):
                        if lowered_marker in lowered_line:
                            violations.append(f"{rel}:{line_number}:{marker_key}:{marker}")
    return violations


def main() -> int:
    violations = development_cleanroom_violations()
    if not violations:
        return 0
    print("development clean-room violations detected:")
    print(export_development_cleanroom_contract())
    for violation in violations:
        print(violation)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
