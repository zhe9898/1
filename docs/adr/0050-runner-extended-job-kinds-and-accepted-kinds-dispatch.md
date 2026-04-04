# 0050. Runner 扩展任务类型与 AcceptedKinds 调度

- 状态: 已采纳
- 日期: 2026-04-04

## 1. 背景上下文

Runner Agent 最初仅支持单一 `connector.invoke` 任务类型，通过 `Capabilities` 字段声明节点能力。随着边缘计算场景扩展（健康检查、文件传输、容器编排、定时任务、数据同步、WASM 执行），需要：

1. 支持多种原生任务类型（job kind），每种类型有独立的执行逻辑和错误分类。
2. 节点能精确声明自己接受的任务类型（而非泛泛的"能力标签"）。
3. 执行器对失败进行结构化分类，以便控制面做出智能重试决策。

## 2. 决策选项

1. **方案 A — 通用 Webhook**：所有任务类型通过统一 HTTP 回调分派。
2. **方案 B — Executor 内置 Kind 路由**：在 Go executor 中为每种 kind 实现原生处理器。
3. **方案 C — 外部插件体系**：通过 gRPC/进程间通信加载外部 kind 处理器。

## 3. 评估对比

### 方案 A（通用 Webhook）
- **优势**：简单统一
- **劣势**：无法充分利用本地硬件（文件系统、容器运行时）；延迟高

### 方案 B（Executor 内置路由）
- **优势**：零网络开销；可直接访问本地资源（文件、Docker、sysfs）；错误分类精准
- **劣势**：新 kind 需修改 executor 代码并重新编译

### 方案 C（外部插件）
- **优势**：可动态扩展
- **劣势**：架构复杂度高；当前场景 kind 数量有限不需要

## 4. 最终决定

采用 **方案 B**：在 `runner-agent/internal/exec/executor_extended.go` 中实现 6 种扩展任务类型，通过 `executor.go` 的 kind 路由分派。同时引入 `AcceptedKinds` 配置字段替代旧的 `Capabilities` 泛能力声明。

### 支持的任务类型

| Kind | 处理器 | 用途 |
|------|--------|------|
| `connector.invoke` | `executor.go` | 调用已注册连接器 |
| `http.request` | `executor.go` | 通用 HTTP 请求执行 |
| `healthcheck` | `executor_extended.go` | HTTP/TCP 健康探测（超时 5000ms，诊断截断 512B） |
| `file.transfer` | `executor_extended.go` | 本地/远程文件传输 + SHA-256 校验 |
| `container.run` | `executor_extended.go` | Docker 容器创建与执行 |
| `cron.tick` | `executor_extended.go` | 定时触发器与动作分派 |
| `data.sync` | `executor_extended.go` | 边缘↔云端文件同步（rsync） |
| `wasm.run` | `executor_extended.go` | WebAssembly 模块执行（预留） |

### AcceptedKinds 机制

```
# 环境变量配置
RUNNER_ACCEPTED_KINDS=healthcheck,file.transfer,container.run

# 配置优先级
EffectiveAcceptedKinds() →
  if len(AcceptedKinds) > 0 → return AcceptedKinds
  else → return Capabilities  // 向后兼容
```

- `AcceptedKinds` 通过 `RUNNER_ACCEPTED_KINDS` 环境变量配置（逗号分隔）。
- 注册和心跳请求中携带 `AcceptedKinds`，控制面据此过滤可分派的任务类型。
- 若未配置 `AcceptedKinds`，回退到旧的 `Capabilities` 字段以保持向后兼容。

### 错误分类 (ExecError)

执行器返回结构化 `ExecError`，包含 `Category` 字段映射到 Python 侧 `FailureCategory` 枚举：

| Category | 含义 | 控制面响应 |
|----------|------|-----------|
| `timeout` | 执行超时 | 可重试（延长超时） |
| `resource_exhausted` | 磁盘/内存不足 | 标记节点容量不足 |
| `invalid_payload` | 任务参数缺失/非法 | 不重试（通知调用方） |
| `canceled` | 任务被取消 | 不重试 |
| `transient` | 瞬时网络/IO 错误 | 可重试 |
| `execution_error` | 业务逻辑失败 | 视具体场景重试 |
| `not_found` | 资源不存在 | 不重试 |

### 边缘计算遥测

Runner 心跳新增以下字段，支持 15 维评分中的边缘感知调度：

| 字段 | 环境变量 | 用途 |
|------|----------|------|
| `NetworkLatencyMs` | `RUNNER_NETWORK_LATENCY_MS` | 网络延迟 |
| `BandwidthMbps` | `RUNNER_BANDWIDTH_MBPS` | 带宽 |
| `CachedDataKeys` | `RUNNER_CACHED_DATA_KEYS` | 已缓存数据集 |
| `PowerCapacityWatts` | `RUNNER_POWER_CAPACITY_WATTS` | 总电力容量 |
| `CurrentPowerWatts` | `RUNNER_CURRENT_POWER_WATTS` | 当前功耗 |
| `ThermalState` | `RUNNER_THERMAL_STATE` | 热状态（默认 normal） |
| `CloudConnectivity` | `RUNNER_CLOUD_CONNECTIVITY` | 云连接状态（默认 online） |

## 5. 影响范围

正面影响：

- 节点可精确声明和过滤任务类型，避免不匹配的任务分派。
- 结构化错误分类使故障控制面（quarantine/cooldown/circuit-breaker）做出更精准的决策。
- 边缘遥测为 15 维评分中的 `edge_proximity`、`thermal_headroom` 等因子提供数据源。

成本：

- 新增 kind 需在 `executor_extended.go` 中实现处理器并重新编译 runner 二进制。
- `AcceptedKinds` 配置需在部署时同步更新。

## 落地

- `runner-agent/internal/exec/executor.go` — ExecError 结构化错误、kind 路由
- `runner-agent/internal/exec/executor_extended.go` — 6 种扩展 kind 处理器
- `runner-agent/internal/config/config.go` — AcceptedKinds、边缘遥测字段、EffectiveAcceptedKinds()
- `runner-agent/internal/config/config_test.go` — 配置加载测试
- `runner-agent/internal/api/client.go` — AcceptedKinds 注册/心跳请求
- `runner-agent/internal/jobs/poller.go` — 失败分类上报、lease 续约指数退避
