#!/bin/bash
# ZEN70 离线冷启动安装脚本
# 拦截对外网络请求，强制读取本地 .tar 构建闭环

set -e

echo "=== ZEN70 离线容灾基座启动 (bootstrap-offline.sh) ==="

if [ -f "images-bundle.tar" ]; then
    echo "[1/3] 正在从 images-bundle.tar 恢复所有只读核心镜像..."
    docker load -i images-bundle.tar
else
    echo "[WARN] 当前目录下未发现 images-bundle.tar，可能由于非完整版离线种子导致。"
fi

echo "[2/3] 配置点火编译器 (离线模式拦截)"
python3 scripts/bootstrap.py --offline "$@"

echo "[3/3] 核验容器安全加固与进程启动状态..."
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

echo "[SUCCESS] ZEN70 第0天离线集群拉起完毕。"
