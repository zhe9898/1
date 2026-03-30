#!/usr/bin/env python3
"""
Compatibility wrapper for the canonical IaC compiler entrypoint.

`scripts/compiler.py` is the single source of truth for runtime profile
normalization, migration, manifest rendering, and release inputs.
This wrapper exists only for historical offline/deploy paths and forwards all
arguments unchanged. New operator guidance must point to the root `system.yaml`
and `scripts/compiler.py`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    cmd = [sys.executable, str(root / "scripts" / "compiler.py"), *sys.argv[1:]]
    try:
        completed = subprocess.run(cmd, cwd=str(root), check=True)
    except subprocess.CalledProcessError as exc:
        print(
            f"[deploy/config-compiler.py] canonical compiler failed with exit code {exc.returncode}",
            file=sys.stderr,
        )
        return exc.returncode
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
