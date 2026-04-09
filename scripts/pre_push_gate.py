#!/usr/bin/env python3
"""Local wrapper around the shared repository quality gate."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
QUALITY_GATE = REPO_ROOT / "scripts" / "quality_gate.py"


def main() -> int:
    if not QUALITY_GATE.exists():
        print(f"missing shared quality gate: {QUALITY_GATE}", file=sys.stderr)
        return 2

    return subprocess.run(
        [sys.executable, str(QUALITY_GATE), "backend-ci", "frontend-ci"],
        cwd=REPO_ROOT,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
