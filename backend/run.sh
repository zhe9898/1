#!/bin/bash
# 从 backend 目录或项目根目录均可启动 API 网关
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="${ROOT}:${PYTHONPATH}"
exec uvicorn backend.api.main:app --host 0.0.0.0 --port 8000 --reload
