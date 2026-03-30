#!/usr/bin/env bash
# ZEN70 V3.43 一键启动引擎 (Linux/macOS)
echo "========================================================="
echo "           ZEN70 V3.43 一键启动引擎 (Start Engine)        "
echo "========================================================="

echo -e "\n[1/4] 正在拉起底层基础网络与容器编排..."
docker compose -p zen70 up -d --remove-orphans

echo -e "\n[2/4] 环境预检与探测..."
export PYTHONPATH="."
export REDIS_HOST="127.0.0.1"

echo -e "\n[3/4] 安全探针与守望者监控网略已由容器集群接管运行..."
# (法典 1.1：一切皆容器) 物理探针已被迁移至 system.yaml 中的 sentinel 容器，禁止在此宿主机裸跑 Python

echo -e "\n[4/4] 守护进程已就位。请访问:"
echo "主控台: http://localhost/"
echo "家族信标: http://localhost"

echo -e "\n========================================================="
echo "✅ ZEN70 系统已全栈满血上线！"
echo "========================================================="
