#!/bin/bash
# Start the canonical backend control-plane gateway from either backend/ or repo root.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$ROOT"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

exec uvicorn backend.control_plane.app.entrypoint:app --host 0.0.0.0 --port 8000 --reload
