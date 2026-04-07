# ADR 0037: Phase 5 原生客户端合同与资源感知异构调度

- 状态：Accepted
- 日期：2026-03-27

## 背景

`Phase 4` 已经把 `Health Pack` 和 `Vector/AI Pack` 从默认 kernel 边界中剥离出去，但控制面仍然缺两层关键能力：

1. 节点合同还停留在 `executor/os/arch/zone/max_concurrency`，没有显式资源画像
2. 作业合同还不能声明 `target_executor` 和资源选择器
3. scheduler explain 与 diagnostics 只能解释到 `os/arch/capability/zone`，不能解释异构执行器和资源不足
4. 节点发证回执只有 Win/macOS/Linux runner 指令，没有原生客户端 bootstrap receipt

这会导致两个现实问题：

- `Health Pack` 和移动原生客户端虽然在 pack 层存在，但在控制面里不是一等公民
- 调度器虽然已经不是 FIFO，但对异构算力仍然“不够硬”，无法解释 executor/resource 维度的放置原因

## 决策

我们把 `Phase 5` 在本仓库中的范围固定为“控制面与合同完成”，不把 iOS/Android 原生 App 本体实现伪装成网关仓库职责。

具体决策如下：

1. 节点合同显式增加资源字段：`cpu_cores`、`memory_mb`、`gpu_vram_mb`、`storage_mb`
2. 节点合同支持 `native-client` 节点类型，以及 `swift-native`、`kotlin-native`、`vector-worker`、`search-service` 执行器
3. 作业合同显式增加 `target_executor` 和资源选择器：`required_cpu_cores`、`required_memory_mb`、`required_gpu_vram_mb`、`required_storage_mb`
4. scheduler 必须把 `executor/resources` 纳入 eligibility、score、explain 和 diagnostics
5. 发证/轮换回执由后端下发原生客户端 bootstrap receipts，不允许前端拼装移动接入文案
6. `Health Pack` 与 `Vector/AI Pack` 的 selector hints 必须显式包含 executor 提示

## 具体落地

### 后端

- `Node`
  - 增加显式资源字段
  - `nodes/schema` 增加 `native-client`、`ios/android`、新执行器选项
  - `nodes` 列表支持 `node_type/executor/os/zone` 服务端过滤
  - 发证/轮换响应增加 `bootstrap_receipts`
- `Job`
  - 增加 `target_executor` 与资源选择器
  - `jobs/schema` 与 `jobs` 列表过滤同步更新
- `Scheduler`
  - eligibility 新增 `executor` 和资源校验
  - score 新增 executor/resource fit bonus
  - explain 返回节点 executor/platform/resource 事实
- `Console`
  - diagnostics 增加 `backlog_by_executor`
  - node diagnostics 暴露 executor/platform/resource 画像

### Runner

- Go runner 上报 `cpu_cores`、`memory_mb`、`gpu_vram_mb`、`storage_mb`
- 资源字段进入 `register` 与 `heartbeat` 合同

### 前端

- Nodes 展示 executor 与资源画像
- Jobs 展示 executor 与资源选择器
- Dashboard 展示 `backlog_by_executor`
- 节点发证回执展示原生客户端 bootstrap receipts

## 影响

### 正向影响

- `Health Pack`、移动原生客户端、向量检索 worker 在控制面里成为一等公民
- 异构节点调度首次具备显式 executor/resource 合同，而不是只靠 `metadata` 约定
- explain 与 diagnostics 可以直接回答“为什么这份任务没有被派到这台节点”
- 前后端继续保持后端驱动闭环，没有回到前端拼装文案或状态机

### 代价与约束

- 节点与作业合同更长，OpenAPI 和前端 DTO 必须同步维护
- 现有 runner 若不升级资源上报，只能领取不带资源要求的任务
- 本仓库只完成控制面和合同，不承担 iOS/Android 客户端产品实现

## 不做的事

- 不在 `gateway` 默认运行时内接入健康 SDK 或向量检索引擎
- 不把移动端接入变成第二套独立协议
- 不在当前阶段引入独立向量服务集群编排
