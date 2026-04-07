# ZEN70 全链路打通实现文档

**日期**: 2026-04-04
**范围**: Runner-Agent Go 重写 + 控制面故障治理 + 调度器业务维度补全 + 扩展执行器 + PolicyStore

---

## 1. 变更总览

本轮实现解决了四个成熟度缺口，使 调度→拉取→执行→回调→重试 全链路成为真厚度实现：

| 缺口 | 修复前 | 修复后 |
|------|--------|--------|
| 动态遥测 | Runner 发送静态环境变量 | Go 运行时探针 (gateway HEAD / sysfs thermal & power) |
| 业务调度 | 仅前置过滤器 | SLA 紧急度 (0-30) + 批量共置 (0-15) 纳入评分主干 |
| 故障控制面 | 缺失 | 节点隔离 / 连接器冷却 / kind 断路器 / 爆发检测 |
| 执行器 | 骨架 noop | 超时强制 / 输出截断 / 错误分类 / kind 路由 |

---

## 2. Runner-Agent Go 侧

### 2.1 遥测采集器 `internal/telemetry/collector.go`

新增包，零外部依赖。

**核心结构**:
```go
type Snapshot struct {
    NetworkLatencyMs  int
    CurrentPowerWatts int
    ThermalState      string      // "cool" | "normal" | "warm" | "critical"
    CloudConnectivity string      // "full" | "degraded" | "offline"
}

type Collector struct { /* RWMutex + defaults + gatewayURL + httpClient */ }
```

**采集策略**:
- **网络延迟**: 对 gateway URL 执行 `HEAD` 请求，测量 RTT
- **云连通性**: 根据 HEAD 响应状态 (成功=full, 超时=degraded, 无响应=offline)
- **温度**: 读取 `/sys/class/thermal/thermal_zone0/temp` (Linux sysfs)
- **功率**: 遍历 `/sys/class/power_supply/*/power_now` 取最大值
- **降级策略**: 所有探针失败时回退到配置文件默认值

**生命周期**: `NewCollector() → Run(ctx, interval)` 作为独立 goroutine 运行

### 2.2 心跳重写 `internal/heartbeat/heartbeat.go`

- 接受 `*telemetry.Collector` 参数
- 拆分为 `Loop()` + `buildHeartbeatRequest()`
- 动态字段 (`NetworkLatencyMs`, `CurrentPowerWatts`, `ThermalState`, `CloudConnectivity`) 从 `collector.Get()` 读取
- 静态字段 (`BandwidthMbps`, `CachedDataKeys`, `PowerCapacityWatts`) 仍来自配置

### 2.3 执行器重写 `internal/exec/executor.go`

**全新实现**:
```go
type ExecError struct {
    Category string  // 映射 Python FailureCategory 枚举
    Details  string
    Inner    error
}

type Config struct {
    DefaultTimeoutSeconds int
    MaxOutputBytes        int
}
```

**关键行为**:
- 超时 = `min(leaseSeconds - 5, config.DefaultTimeout)` — 留 5 秒汇报余量
- Kind 路由: `runNoop()` (健康探针) / `runConnectorInvoke()` (连接器调用)
- `classifyError()`: `DeadlineExceeded → "timeout"`, `Canceled → "canceled"`, `ExecError` 透传 Category
- 输出截断到 `MaxOutputBytes` 防止 OOM

### 2.3.1 扩展执行器 `internal/exec/executor_extended.go`（v3.42 新增）

在基础执行器之上，新增 6 种原生任务类型（ADR 0050）：

| Kind | 处理器 | 用途 | 关键参数 |
|------|--------|------|----------|
| `healthcheck` | `runHealthcheck()` | HTTP/TCP 健康探测 | `target`, `check_type`, `timeout_ms`(默认 5000), `expected_status`, `headers` |
| `file.transfer` | `runFileTransfer()` | 文件传输 + SHA-256 校验 | `source`, `destination`, `expected_sha256` |
| `container.run` | `runContainer()` | Docker 容器创建与执行 | `image`, `command`, `env`, `volumes` |
| `cron.tick` | `runCronTick()` | 定时触发器与动作分派 | `action`, `schedule`, `payload` |
| `data.sync` | `runDataSync()` | 边缘↔云端 rsync 同步 | `source`, `destination`, `direction` |
| `wasm.run` | `runWasm()` | WASM 模块执行（预留） | `module_path`, `function`, `args` |

**常量**:
- `DefaultProbeTimeoutMs = 5000` — healthcheck 默认超时
- `DiagnosticsSnippetBytes = 512` — 诊断信息截断长度

**错误分类**: 每个 kind 返回结构化 `ExecError`，Category 值为：

| Category | 含义 | 控制面响应 |
|----------|------|-----------|
| `timeout` | 执行超时 | 可重试（延长超时） |
| `resource_exhausted` | 资源不足 | 标记节点容量不足 |
| `invalid_payload` | 参数缺失/非法 | 不重试 |
| `canceled` | 被取消 | 不重试 |
| `transient` | 瞬时网络/IO 错误 | 可重试 |
| `execution_error` | 业务逻辑失败 | 视场景重试 |
| `not_found` | 资源不存在 | 不重试 |

### 2.3.2 AcceptedKinds 节点声明（v3.42 新增）

Runner 通过 `RUNNER_ACCEPTED_KINDS` 环境变量声明支持的任务类型：

```
RUNNER_ACCEPTED_KINDS=healthcheck,file.transfer,container.run
```

`config.go` 中的 `EffectiveAcceptedKinds()` 方法实现向后兼容：
- 若 `AcceptedKinds` 非空 → 返回 `AcceptedKinds`
- 否则 → 回退到旧的 `Capabilities` 字段

注册和心跳请求中携带 `AcceptedKinds`，控制面据此过滤可分派的任务类型。

### 2.4 轮询器拆分 `internal/jobs/poller.go`

原 160 行单函数拆为 6 个:
| 函数 | 职责 |
|------|------|
| `Loop()` | 主循环: pull → executeAndReport → sleep |
| `executeAndReport()` | 调度执行 + 结果汇报 |
| `startLeaseRenewal()` | 后台 goroutine 续租（指数退避，3 次上限） |
| `reportProgress()` | 进度汇报 |
| `reportFailure()` | 提取 ExecError.Category → 发送 FailureCategory |
| `reportResult()` | 正常结果汇报 |

**Lease 续约策略**（v3.42 增强）:
- 续约间隔 = `max(5s, lease/2)`
- 失败时指数退避（1x → 2x → 4x 间隔）
- 最多 3 次连续失败后放弃续约
- 并发控制通过 `chan struct{}` 信号量（`cfg.MaxConcurrency`）实现

### 2.5 服务层重写 `internal/service/service.go`

- 运行 **3 个 goroutine**（原 2 个）: 遥测采集 / 心跳 / 任务轮询
- 注册逻辑提取为 `registerNode()` 方法
- 遥测采集器与心跳共享 `client.HTTPClient()` TLS 实例
- 错误通道 `errs` 容量 3，任一 goroutine 失败即退出

---

## 3. 控制面 Python 侧

### 3.1 故障控制面 `backend/core/failure_control_plane.py`

进程内单例，`asyncio.Lock` 保护并发。四个子系统:

| 子系统 | 触发条件 | 效果 | 持续时间 |
|--------|----------|------|----------|
| 节点隔离 | 连续 5 次失败 | `pull_jobs` 返回空 `[]` | 5 分钟 |
| 连接器冷却 | 5 分钟窗口内 10 次失败 | 日志告警 + 快照诊断 | 2 分钟 |
| Kind 断路器 | 5 分钟窗口内 15 次失败 | open→half-open→closed | open 60 秒 |
| 爆发检测 | 5 分钟窗口内全局 20 次失败 | 日志告警 | 窗口级 |

**集成点** (`backend/api/jobs/routes.py`):
- `pull_jobs()`: 开头检查 `is_node_quarantined()`
- `fail_job()`: 分类后调用 `record_failure()`
- `complete_job()`: 成功时调用 `record_success()` 重置连续失败计数

### 3.1.1 调度策略存储 `backend/core/scheduling_policy_store.py`（v3.42 新增）

PolicyStore 单例取代了四个模块分别解析 system.yaml 的做法（ADR 0049）：

**生命周期**:
1. `get_policy_store()` 首次调用 → 创建单例 → `load_from_yaml()` 缓存 5 个 scheduling 配置段
2. 运行时 `apply(new_policy, operator, reason)` → 验证 + 版本递增 + 审计记录
3. 紧急时 `freeze(reason)` → 阻断所有变更
4. 回滚时 `rollback(target_version, operator, reason)` → 从历史 deque（最多 200 条）恢复

**消费方映射**:
| 模块 | 消费的配置 |
|------|-----------|
| `executor_registry.py` | `executor_contracts_config` |
| `placement_policy.py` | `placement_policies_config` |
| `queue_stratification.py` | `tenant_quotas_config` + `default_service_class_override` |
| `quota_aware_scheduling.py` | `resource_quotas_config` |

### 3.2 评分器 15 维度 `backend/core/job_scheduler.py`

从 13 维度扩展到 15 维度，总分范围 `-130 ~ 504`:

| 维度 | 范围 | 新增 | 说明 |
|------|------|------|------|
| priority | 0-100 | | 作业基础优先级 |
| age | 0-60 | | 等待分钟数（防饿死） |
| scarcity | 0-100 | | 可调度节点越少分越高 |
| reliability | 0-20 | | 节点成功率 |
| strategy | 0-100 | | 调度策略加分 (spread/locality/etc) |
| zone | 0-10 | | 可用区匹配 |
| resource_fit | 0-24 | | 执行器 + 资源亲和 |
| power | 0-15 | | 功率余量 |
| thermal | 0-10 | | 温度状态 |
| affinity | 0-20 | | 亲和标签匹配 |
| **sla_urgency** | **0-30** | ✅ | SLA 违约风险 → 紧急调度 |
| **batch** | **0-15** | ✅ | 同批次共置奖励 |
| load_penalty | 0-40 | | 当前负载惩罚 |
| failure_penalty | 0-40 | | 近期失败惩罚 |
| anti_affinity | 0-50 | | 反亲和违反惩罚 |

**辅助函数拆分**:
- `_power_efficiency_bonus(job, node)` — 功率余量计算
- `_thermal_bonus(job, node)` — 温度偏好
- `_affinity_bonus(job, node)` — 亲和标签匹配
- `_sla_risk_to_score(risk, level)` — SLA 风险映射
- `_batch_co_location_bonus(job, active_jobs)` — 批次共置

---

## 4. 全链路时序

```
Runner                          Gateway (FastAPI)
  │                                    │
  │──── POST /heartbeat ──────────────▶│  动态遥测 (collector.Get())
  │                                    │  更新 nodes 表
  │                                    │
  │──── POST /jobs/pull ──────────────▶│  ① quarantine gate
  │                                    │  ② DB 候选查询 (SKIP LOCKED)
  │                                    │  ③ apply_business_filters()
  │                                    │  ④ select_jobs_for_node() → 15维评分
  │                                    │  ⑤ lease 写入
  │◀─── [JobLeaseResponse] ───────────│
  │                                    │
  │  executor.Run(kind, payload, lease)│
  │  ├── timeout = lease - 5s          │
  │  ├── kind dispatch                 │
  │  └── output truncation             │
  │                                    │
  │──── POST /jobs/{id}/complete ─────▶│  record_success() → 重置隔离计数
  │  或                                │
  │──── POST /jobs/{id}/fail ─────────▶│  record_failure() → 隔离/冷却/断路
  │                                    │  retry_delay → retry_at
  │                                    │  或 DLQ
```

---

## 5. 配置参数

### Runner-Agent 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ZEN70_GATEWAY_URL` | (必填) | 控制面网关地址 |
| `ZEN70_NODE_ID` | (必填) | 节点唯一标识 |
| `ZEN70_POLL_INTERVAL` | `5s` | 任务轮询间隔 |
| `ZEN70_HEARTBEAT_INTERVAL` | `30s` | 心跳间隔 |
| `ZEN70_NETWORK_LATENCY_MS` | `50` | 默认网络延迟（探针失败时回退） |
| `ZEN70_CURRENT_POWER_WATTS` | `100` | 默认功率（探针失败时回退） |
| `ZEN70_THERMAL_STATE` | `normal` | 默认温度状态 |
| `RUNNER_ACCEPTED_KINDS` | (空) | 接受的任务类型（逗号分隔），空则回退 Capabilities |
| `RUNNER_BANDWIDTH_MBPS` | `0` | 网络带宽 (Mbps) |
| `RUNNER_CACHED_DATA_KEYS` | (空) | 已缓存数据集 ID（逗号分隔） |
| `RUNNER_POWER_CAPACITY_WATTS` | `0` | 总电力容量 (W) |
| `RUNNER_CLOUD_CONNECTIVITY` | `online` | 云连接状态 (online/degraded/offline) |

### 控制面环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `RETRY_BASE_DELAY_SECONDS` | `10` | 重试基础延迟 |
| `RETRY_MAX_DELAY_SECONDS` | `600` | 重试最大延迟 |

---

## 6. 待优化项（非阻塞）

1. **故障控制面持久化**: 当前为进程内内存，重启丢失；可升级为 Redis 或 DB 存储
2. **遥测采集频率自适应**: 高负载时降低探针频率以减少开销
3. **断路器 half-open 验证**: 当前 half-open 仅靠时间窗口，可增加探针验证
4. **Batch 共置感知扩展**: 目前仅统计同节点作业数，可增加跨节点负载均衡
