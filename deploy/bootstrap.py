#!/usr/bin/env python3
"""
Compatibility wrapper for the canonical bootstrap entrypoint.

`scripts/bootstrap.py` is the single source of truth for bootstrap behavior,
preflight thresholds, config resolution, compile, and deploy orchestration.
This wrapper preserves the historical path while removing the divergent
implementation.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    cmd = [sys.executable, str(root / "scripts" / "bootstrap.py"), *sys.argv[1:]]
    try:
        completed = subprocess.run(cmd, cwd=str(root), check=True)
    except subprocess.CalledProcessError as exc:
        print(
            f"[deploy/bootstrap.py] canonical bootstrap failed with exit code {exc.returncode}",
            file=sys.stderr,
        )
        return exc.returncode
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
