#!/bin/bash
# ZEN70 私有镜像仓库一键启动脚本 (Registry v2)
# 使用官方 registry:2 镜像，数据卷持久化，restart: unless-stopped
set -e

REGISTRY_CONTAINER="${ZEN70_REGISTRY_CONTAINER:-zen70-registry}"
REGISTRY_PORT="${ZEN70_REGISTRY_PORT:-5000}"
REGISTRY_VOLUME="${ZEN70_REGISTRY_VOLUME:-zen70-registry_data}"

echo "Starting private Docker registry (Registry v2)..."

if ! command -v docker &>/dev/null; then
    echo "Error: docker not found. Install Docker first." >&2
    exit 1
fi

# 已运行则直接成功
if docker ps --format '{{.Names}}' | grep -q "^${REGISTRY_CONTAINER}$"; then
    echo "Registry already running: ${REGISTRY_CONTAINER}"
    echo "Catalog: http://localhost:${REGISTRY_PORT}/v2/_catalog"
    exit 0
fi

# 已存在但已停止则启动
if docker ps -a --format '{{.Names}}' | grep -q "^${REGISTRY_CONTAINER}$"; then
    docker start "${REGISTRY_CONTAINER}"
    echo "Registry started: ${REGISTRY_CONTAINER} at localhost:${REGISTRY_PORT}"
    exit 0
fi

# 创建数据卷
docker volume inspect "${REGISTRY_VOLUME}" &>/dev/null || docker volume create "${REGISTRY_VOLUME}"

# 启动容器
docker run -d \
  --name "${REGISTRY_CONTAINER}" \
  --restart unless-stopped \
  -p "${REGISTRY_PORT}:5000" \
  -v "${REGISTRY_VOLUME}:/var/lib/registry" \
  registry:2

echo "Registry started: ${REGISTRY_CONTAINER} at localhost:${REGISTRY_PORT}"
echo "Catalog: http://localhost:${REGISTRY_PORT}/v2/_catalog"
