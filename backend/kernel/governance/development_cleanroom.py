from __future__ import annotations

from typing import Final

GOVERNED_CLEANROOM_ROOTS: Final[tuple[str, ...]] = (
    "backend/control_plane",
    "backend/kernel",
    "backend/runtime",
    "backend/extensions",
    "backend/platform",
    "backend/workers",
    "backend/sentinel",
    "runner-agent",
    "scripts",
    "tools",
    "frontend/src",
)

CLEANROOM_FILE_EXTENSIONS: Final[tuple[str, ...]] = (
    ".py",
    ".go",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
)

CLEANROOM_IGNORED_PATH_PREFIXES: Final[tuple[str, ...]] = (
    "backend/tests/",
    "tests/",
    "docs/",
    "backend/alembic/",
)

CLEANROOM_EXCLUDED_FILES: Final[tuple[str, ...]] = ("backend/kernel/governance/development_cleanroom.py",)

FORBIDDEN_TRANSITIONAL_MARKERS: Final[dict[str, tuple[str, ...]]] = {
    "sanitized_legacy_docstring": ("Sanitized legacy docstring",),
    "compat_exports_comment": ("compat exports",),
    "compat_helper_prefix": ("compat_get_",),
    "backward_compatibility_phrase": (
        "backward compatibility",
        "backward compat",
    ),
    "compatibility_reexport_phrase": ("re-exports for backward compatibility",),
    "migration_bridge_phrase": ("migration bridge",),
    "drop_in_replacement_phrase": (
        "drop-in async replacement",
        "async drop-in",
        "drop-in replacement",
        "drop-in for ``",
    ),
    "legacy_adapter_phrase": ("legacy SchedulingConstraint",),
}


def export_development_cleanroom_contract() -> dict[str, object]:
    return {
        "development_phase": True,
        "policy": "clean-room",
        "governed_roots": list(GOVERNED_CLEANROOM_ROOTS),
        "file_extensions": list(CLEANROOM_FILE_EXTENSIONS),
        "ignored_path_prefixes": list(CLEANROOM_IGNORED_PATH_PREFIXES),
        "excluded_files": list(CLEANROOM_EXCLUDED_FILES),
        "forbidden_transitional_markers": {key: list(markers) for key, markers in FORBIDDEN_TRANSITIONAL_MARKERS.items()},
        "intent": (
            "Development-phase production code must not accumulate compatibility shims, " "migration bridge language, or placeholder legacy docstrings."
        ),
    }
