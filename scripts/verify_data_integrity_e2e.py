#!/usr/bin/env python3
"""
主机侧 E2E 入口：复用 backend/scripts 的唯一实现，防止双实现漂移。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
workspace = str(PROJECT_ROOT)
if workspace not in sys.path:
    sys.path.insert(0, workspace)

from backend.scripts.verify_data_integrity_e2e import run_e2e

if __name__ == "__main__":
    raise SystemExit(run_e2e())
