"""
ZEN70 OpenAPI surface snapshot tool (ADR-0047 WP-P2a).

Freeze scope
------------
Path presence and HTTP methods are frozen for all public ``/api/`` routes.
Out of scope: schemas, response bodies, status codes, and auth semantics.

Surface collection
------------------
All routes whose path starts with ``/api/`` are discovered automatically. No
manual router allowlist is required for collection.

Canonical workflow
------------------
After a deliberate API surface change:
  1. Run:    python scripts/generate_contracts.py
  2. Commit: docs/api/openapi_locked.json together with the router change

Usage
-----
  # Generate / refresh the committed snapshot directly:
  python scripts/freeze_openapi.py --sync

  # Also write dist/openapi_v3.43.json for tooling pipelines:
  python scripts/freeze_openapi.py --sync --export-dist

  # Verify surface matches the committed snapshot:
  python scripts/freeze_openapi.py --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCKED_SNAPSHOT = PROJECT_ROOT / "docs" / "api" / "openapi_locked.json"
DIST_SNAPSHOT = PROJECT_ROOT / "dist" / "openapi_v3.43.json"
_API_PATH_PREFIX = "/api/"

_EXCLUDE_EXACT: frozenset[str] = frozenset(
    {
        "/",
        "/health",
        "/docs",
        "/redoc",
        "/api/docs",
        "/api/redoc",
        "/openapi.json",
    }
)

_SurfaceMap = dict[str, list[str]]
_LockedMap = dict[str, list[str]]


def _collect_surface() -> _SurfaceMap:
    sys.path.insert(0, str(PROJECT_ROOT))
    try:
        from backend.control_plane.app.entrypoint import app  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            f"[freeze_openapi] Cannot import FastAPI app: {exc}. "
            "Install backend deps (pip install -r backend/requirements.txt)."
        ) from exc

    surface: _SurfaceMap = {}
    for route in app.routes:
        path = getattr(route, "path", None)
        if not isinstance(path, str):
            continue
        if path in _EXCLUDE_EXACT:
            continue
        if not path.startswith(_API_PATH_PREFIX):
            continue
        methods = sorted(getattr(route, "methods", None) or [])
        existing = surface.get(path)
        if existing is None:
            surface[path] = methods
        else:
            surface[path] = sorted(set(existing) | set(methods))
    return surface


def _load_locked() -> _LockedMap | None:
    if not LOCKED_SNAPSHOT.exists():
        return None
    data = json.loads(LOCKED_SNAPSHOT.read_text(encoding="utf-8"))
    path_methods = data.get("path_methods")
    if not isinstance(path_methods, dict):
        raise ValueError(
            "Locked snapshot is missing the required 'path_methods' mapping. "
            "Re-run: python scripts/generate_contracts.py"
        )

    locked: _LockedMap = {}
    for path, methods in path_methods.items():
        if not isinstance(path, str) or not isinstance(methods, list) or not all(isinstance(method, str) for method in methods):
            raise ValueError(
                "Locked snapshot contains invalid path_methods entries. "
                "Re-run: python scripts/generate_contracts.py"
            )
        locked[path] = sorted(method.upper() for method in methods)
    return locked


def _build_snapshot_dict(surface: _SurfaceMap) -> dict[str, object]:
    sorted_surface = {path: methods for path, methods in sorted(surface.items())}
    return {
        "version": "v3.43",
        "freeze_scope": "path-and-method-surface",
        "freeze_boundary": "Path presence and HTTP methods are frozen. Schemas, responses, and auth semantics are out of scope.",
        "description": "ZEN70 v3.43 API path+method surface locked by ADR-0047. Do not edit manually. Re-run generate_contracts.py after deliberate surface changes.",
        "path_methods": sorted_surface,
        "paths": sorted(sorted_surface.keys()),
    }


def _save_snapshot(surface: _SurfaceMap, *, export_dist: bool = False) -> None:
    payload = _build_snapshot_dict(surface)
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    LOCKED_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    LOCKED_SNAPSHOT.write_text(text, encoding="utf-8")
    if export_dist:
        DIST_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        DIST_SNAPSHOT.write_text(text, encoding="utf-8")


def sync_locked_snapshot(*, quiet: bool = False, export_dist: bool = False) -> _SurfaceMap:
    surface = _collect_surface()
    _save_snapshot(surface, export_dist=export_dist)
    if not quiet:
        print(f"[freeze_openapi] Synchronized snapshot: {LOCKED_SNAPSHOT}")
        print(f"  Captured {len(surface)} paths with HTTP methods.")
        if export_dist:
            print(f"  Exported (overwritten): {DIST_SNAPSHOT}")
    return surface


def cmd_sync(*, quiet: bool = False, export_dist: bool = False) -> int:
    try:
        sync_locked_snapshot(quiet=quiet, export_dist=export_dist)
    except ImportError as exc:
        print(f"[freeze_openapi] SKIP --sync: {exc}", file=sys.stderr)
        print("  Surface snapshot NOT updated.", file=sys.stderr)
        return 2
    return 0


def cmd_check(*, quiet: bool = False, export_dist: bool = False) -> int:
    try:
        locked = _load_locked()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[freeze_openapi] ERROR: Locked snapshot is invalid: {exc}", file=sys.stderr)
        return 1
    if locked is None:
        print(
            "[freeze_openapi] ERROR: No locked snapshot found at:\n"
            f"  {LOCKED_SNAPSHOT}\n"
            "  Run: python scripts/generate_contracts.py",
            file=sys.stderr,
        )
        return 1

    try:
        actual = _collect_surface()
    except ImportError as exc:
        print(f"[freeze_openapi] ERROR: --check cannot run because deps are unavailable: {exc}", file=sys.stderr)
        return 2

    if export_dist:
        _save_snapshot(actual, export_dist=True)
        if not quiet:
            print(f"[freeze_openapi] Exported current surface (overwritten): {DIST_SNAPSHOT}")

    locked_paths = set(locked.keys())
    actual_paths = set(actual.keys())
    added_paths = actual_paths - locked_paths
    removed_paths = locked_paths - actual_paths

    method_drift: dict[str, tuple[list[str], list[str]]] = {}
    for path in locked_paths & actual_paths:
        locked_methods = locked[path]
        actual_methods = actual.get(path, [])
        if sorted(locked_methods) != sorted(actual_methods):
            method_drift[path] = (sorted(locked_methods), sorted(actual_methods))

    if not added_paths and not removed_paths and not method_drift:
        if not quiet:
            print(f"[freeze_openapi] Surface unchanged ({len(actual)} paths match locked contract)")
        return 0

    print("[freeze_openapi] API surface has drifted from the locked contract!", file=sys.stderr)
    if added_paths:
        print(f"  Added paths ({len(added_paths)}):", file=sys.stderr)
        for path in sorted(added_paths):
            print(f"    + {path}", file=sys.stderr)
    if removed_paths:
        print(f"  Removed paths ({len(removed_paths)}):", file=sys.stderr)
        for path in sorted(removed_paths):
            print(f"    - {path}", file=sys.stderr)
    if method_drift:
        print(f"  Method changes ({len(method_drift)} paths):", file=sys.stderr)
        for path, (locked_methods, actual_methods) in sorted(method_drift.items()):
            print(f"    {path}: locked={locked_methods} actual={actual_methods}", file=sys.stderr)
    print(
        "\n  To update after a deliberate surface change:\n"
        "    python scripts/generate_contracts.py\n"
        "    git add docs/api/openapi_locked.json && git commit",
        file=sys.stderr,
    )
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ZEN70 OpenAPI path+method surface snapshot tool (ADR-0047).",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--sync", action="store_true", help="Capture and save the current path-surface snapshot")
    group.add_argument("--check", action="store_true", help="Verify current path surface matches the snapshot")
    parser.add_argument("--quiet", action="store_true", help="Suppress informational output")
    parser.add_argument(
        "--export-dist",
        action="store_true",
        help="Overwrite dist/openapi_v3.43.json with the current surface",
    )
    args = parser.parse_args()

    if args.sync:
        raise SystemExit(cmd_sync(quiet=args.quiet, export_dist=args.export_dist))
    raise SystemExit(cmd_check(quiet=args.quiet, export_dist=args.export_dist))


if __name__ == "__main__":
    main()
