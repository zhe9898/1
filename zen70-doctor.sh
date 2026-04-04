#!/usr/bin/env bash
# 一键诊断脚本（法典 6.9）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR" && pwd)"
COMPOSE_FILE="${ROOT}/docker-compose.yml"
PROJECT="${COMPOSE_PROJECT_NAME:-zen70}"

echo "=== ZEN70 诊断 (zen70-doctor) ==="

# 1. Docker
if command -v docker &>/dev/null; then
  if docker info &>/dev/null; then
    echo "[OK] Docker daemon 运行中"
    docker version --format '{{.Server.Version}}' 2>/dev/null | xargs -I{} echo "    版本: {}"
  else
    echo "[FAIL] Docker daemon 未运行或不可访问"
    exit 1
  fi
else
  echo "[FAIL] 未找到 docker 命令"
  exit 1
fi

# 2. Compose
if command -v docker &>/dev/null && docker compose version &>/dev/null 2>&1; then
  echo "[OK] Docker Compose V2 可用"
else
  echo "[WARN] Docker Compose V2 可能未安装"
fi

# 3. 磁盘
if [ -d "$ROOT" ]; then
  df -h "$ROOT" 2>/dev/null | tail -1 | awk '{print "[INFO] 磁盘: 可用 "$4" / "$2}'
fi

# 4. Compose 文件
if [ -f "$COMPOSE_FILE" ]; then
  echo "[OK] 找到 docker-compose.yml"
else
  echo "[WARN] 未找到 docker-compose.yml，请先执行编译与点火"
fi

# 5. 容器状态
if [ -f "$COMPOSE_FILE" ]; then
  echo ""
  echo "--- 容器状态 ---"
  cd "$ROOT"
  docker compose -f "$COMPOSE_FILE" ps 2>/dev/null || true
  echo ""
  echo "--- 健康检查 ---"
  docker compose -f "$COMPOSE_FILE" ps --format json 2>/dev/null | grep -o '"Health":"[^"]*"' || echo "   (无健康状态输出)"
fi

echo ""
echo "=== 诊断完成 ==="
