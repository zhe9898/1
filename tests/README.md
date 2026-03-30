# ZEN70 集成测试与硬件缺失模拟

## 目录

- **integration/** — 集成测试（pytest）：网关健康、能力/软开关、Redis hw 状态、SSE、挂载模拟流程。
- **chaos/** — 混沌工具：挂载模拟（环回设备）、网络模拟（tc）。

## 环境变量（集成测试）

| 变量 | 默认 | 说明 |
|------|------|------|
| `BASE_URL` | http://localhost:8000 | 网关地址 |
| `REDIS_HOST` / `REDIS_PORT` | localhost / 6379 | Redis（断言 hw 状态） |
| `TEST_MOUNT_PATH` | /mnt/test | 挂载模拟路径 |
| `CONTAINER_NAME` | （空） | 可选，用于断言容器 pause/unpause 状态 |
| `PROBE_DEBOUNCE_WAIT` | 25 | 等待探针防抖的秒数 |
| `ZEN70_RUN_MOUNT_TESTS` | （空） | 设为 1 或 true 时在 Linux 下运行真实挂载模拟（需 root/环回） |

## 启动测试环境

从**项目根目录**执行：

```bash
docker compose -f tests/docker-compose.yml up -d
```

将启动：Redis、PostgreSQL、网关、探针（Mock 模式）。可选 `--profile with-jellyfin` 启动 Jellyfin。

首次需构建镜像：

```bash
docker compose -f tests/docker-compose.yml build
```

## 运行集成测试

```bash
# 从项目根
export PYTHONPATH=.
pytest tests/integration -v
```

- 不启动 compose 时，依赖网关/Redis 的用例会因 `gateway_available` / `redis_available` 被跳过。
- 仅运行 Mock 相关（不依赖真实挂载）：
  ```bash
  pytest tests/integration -v -k "mock or health or capabilities or sse or gpu"
  ```
- 运行挂载模拟（Linux + 可挂载环回设备，且 `ZEN70_RUN_MOUNT_TESTS=1`）：
  ```bash
  ZEN70_RUN_MOUNT_TESTS=1 pytest tests/integration -v -k "storage_loss"
  ```

## 混沌工具

### 挂载模拟

```bash
# 创建并挂载虚拟磁盘到 /mnt/test
python tests/chaos/mount_simulator.py --action mount --mount-point /mnt/test

# 卸载并清理
python tests/chaos/mount_simulator.py --action umount --mount-point /mnt/test
```

需 root 或 `sudo`（或 `MOUNT_SIMULATOR_SUDO=0` 且当前用户有 mount 权限）。CI 无环回设备时可仅跑 Mock 测试。

### 网络模拟（仅 Linux）

```bash
# 添加延迟
python tests/chaos/network_simulator.py --action delay --interface eth0 --delay-ms 100

# 恢复
python tests/chaos/network_simulator.py --action clear --interface eth0
```

## SRE 合规门禁（法典 2.1 / 3.3）

从项目根执行以下测试，可审计「外部 I/O 必须带 timeout」「禁止 subprocess shell=True」等红线：

```bash
PYTHONPATH=. pytest tests/test_compliance_sre.py -v
```

- 扫描范围：`backend/`、`scripts/`、`deploy/`；`backend/` 内所有 `subprocess.run` 必须带 `timeout=`。
- CI 已在 `.github/workflows/compliance.yml` 中纳入该步骤。

## 完成度自检

1. **挂载点模拟** — 使用 `mount_simulator.py` 可完成环回创建、格式化、挂载/卸载；探针通过 `MOUNT_POINTS` 检测。
2. **存储拔出** — 卸载后防抖周期内探针将 hw 置为 pending/offline，关联容器（若 CONTAINER_MAP 配置）pause；SSE 可收到事件。
3. **存储恢复** — 重新挂载后三重核验通过则 online，容器 unpause。
4. **GPU 离线** — 探针 Mock 时写 `hw:gpu`；真实环境 nvidia-smi 失败则上报 offline。
5. **CI** — 不设 `ZEN70_RUN_MOUNT_TESTS` 时仅执行 Mock/网关/Redis/SSE 等不依赖硬件的用例。
