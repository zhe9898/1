# 高级调度算法文档

## 概述

ZEN70 Gateway Kernel 实现了三层调度算法：
1. **内核调度算法** - 基础资源匹配和负载均衡
2. **边缘算力编排算法** - 数据局部性、网络时延、功率管理、热管理
3. **业务调度算法** - 优先级继承、依赖链、Gang调度、抢占、SLA

---

## 1. 内核调度算法

### 1.1 调度策略

支持 5 种调度策略，通过 `job.scheduling_strategy` 字段指定：

#### Spread（默认）
- **目标**: 均匀分布负载
- **适用场景**: 通用工作负载，避免热点
- **评分逻辑**: 优先选择负载较低的节点
- **评分范围**: 0-100（负载越低分数越高）

```python
utilization = node.active_lease_count / node.max_concurrency
spread_score = int(100 * (1.0 - utilization))
```

#### Binpack
- **目标**: 紧密打包，最小化活跃节点数
- **适用场景**: 功耗优化，节省资源
- **评分逻辑**: 优先选择已有负载的节点（50-90%利用率最优）
- **评分范围**: 0-100

```python
if 0.5 <= utilization <= 0.9:
    binpack_score = 100  # 最优区间
elif utilization < 0.5:
    binpack_score = int(100 * utilization * 2)
else:
    binpack_score = int(100 * (1.0 - (utilization - 0.9) * 10))
```

#### Locality
- **目标**: 优先数据局部性和网络接近度
- **适用场景**: 数据密集型任务，边缘计算
- **评分逻辑**: 数据缓存命中 + 网络时延
- **评分范围**: 0-100

```python
# 数据局部性（50分）
if data_locality_key in node.cached_data_keys:
    score += 50

# 网络接近度（50分）
latency_ratio = 1.0 - min(node.network_latency_ms / max_latency, 1.0)
score += int(50 * latency_ratio)
```

#### Performance
- **目标**: 优先最快/最强节点
- **适用场景**: 计算密集型任务，低延迟要求
- **评分逻辑**: 可靠性 + 资源容量 + 热状态 + 带宽
- **评分范围**: 0-100

```python
score = (
    int(40 * reliability_score) +  # 可靠性
    int(30 * resource_capacity) +  # CPU/内存/GPU
    int(15 * thermal_bonus) +      # 热状态
    int(15 * bandwidth_bonus)      # 带宽
)
```

#### Balanced
- **目标**: 平衡 Spread、Locality、Performance
- **适用场景**: 混合工作负载
- **评分逻辑**: 加权平均
- **评分范围**: 0-100

```python
balanced_score = int(
    0.4 * spread_score +
    0.3 * locality_score +
    0.3 * performance_score
)
```

### 1.2 节点亲和性（Affinity）

通过 `job.affinity_labels` 和 `job.affinity_rule` 控制节点选择：

#### Required Affinity（硬约束）
```python
job.affinity_labels = {"gpu_type": "nvidia-a100", "region": "us-west"}
job.affinity_rule = "required"
```
- 任务**必须**运行在匹配标签的节点上
- 不匹配的节点会被 `node_blockers_for_job()` 阻止

#### Preferred Affinity（软约束）
```python
job.affinity_labels = {"ssd": "true"}
job.affinity_rule = "preferred"
```
- 任务**优先**运行在匹配标签的节点上
- 不匹配的节点不会被阻止，但评分较低
- 匹配节点获得 +20 分加分

### 1.3 反亲和性（Anti-Affinity）

通过 `job.anti_affinity_key` 防止相同类型任务运行在同一节点：

```python
job.anti_affinity_key = "video-transcoding-batch-001"
```

- 相同 `anti_affinity_key` 的任务不会调度到同一节点
- 违反反亲和性的节点会被扣除 50 分

---

## 2. 边缘算力编排算法

### 2.1 数据局部性

**目标**: 减少数据传输，提高执行效率

**实现**:
```python
# Job 指定数据键
job.data_locality_key = "dataset-imagenet-2024"
job.prefer_cached_data = True

# Node 声明缓存数据
node.cached_data_keys = ["dataset-imagenet-2024", "model-resnet50"]
```

**评分**:
- 数据缓存命中: +15 分
- 节点有其他缓存数据: +10 分（可能有相关数据）

**阻塞**:
- 如果 `prefer_cached_data=True` 且数据未缓存，节点被阻止

### 2.2 网络时延约束

**目标**: 保证低延迟要求

**实现**:
```python
# Job 指定最大时延
job.max_network_latency_ms = 50  # 50ms

# Node 上报网络时延
node.network_latency_ms = 30  # 30ms
```

**评分**:
- 网络接近度加分: 0-5 分（时延越低分数越高）

**阻塞**:
- 如果 `node.network_latency_ms > job.max_network_latency_ms`，节点被阻止

### 2.3 功率管理

**目标**: 功耗优化，避免过载

**实现**:
```python
# Job 指定功率预算
job.power_budget_watts = 100  # 100W

# Node 上报功率容量和当前功率
node.power_capacity_watts = 300  # 300W
node.current_power_watts = 150   # 150W
```

**评分**:
- 功率余量加分: 0-15 分（余量越大分数越高）

**阻塞**:
- 如果 `available_power < power_budget`，节点被阻止

### 2.4 热管理

**目标**: 避免过热节点，延长硬件寿命

**实现**:
```python
# Job 指定热敏感度
job.thermal_sensitivity = "high"  # high/normal/low

# Node 上报热状态
node.thermal_state = "cool"  # cool/normal/warm/hot/throttling
```

**评分**:
- 热敏感任务在 cool 节点: +10 分
- 热敏感任务在 normal 节点: +5 分

**阻塞**:
- 如果 `thermal_sensitivity=high` 且 `thermal_state in (hot, throttling)`，节点被阻止

### 2.5 云回退

**目标**: 边缘节点离线时回退到云端

**实现**:
```python
# Job 启用云回退
job.cloud_fallback_enabled = True

# Node 上报云连接状态
node.cloud_connectivity = "online"  # online/degraded/offline
```

**阻塞**:
- 如果 `cloud_fallback_enabled=False` 且 `cloud_connectivity=offline`，节点被阻止

---

## 3. 业务调度算法

### 3.1 优先级继承

**目标**: 子任务继承父任务优先级

**实现**:
```python
# 父任务
parent_job.priority = 80

# 子任务
child_job.parent_job_id = parent_job.job_id
child_job.priority = 50

# 有效优先级
effective_priority = 50 + 10 = 60  # 继承 +10
```

### 3.2 任务依赖链

**目标**: DAG 执行，确保依赖顺序

**实现**:
```python
# Job A 无依赖
job_a.depends_on = []

# Job B 依赖 A
job_b.depends_on = [job_a.job_id]

# Job C 依赖 A 和 B
job_c.depends_on = [job_a.job_id, job_b.job_id]
```

**调度逻辑**:
- 只有所有依赖任务完成后，任务才能被调度
- `check_job_dependencies_satisfied()` 检查依赖状态

### 3.3 Gang 调度

**目标**: 一组任务必须同时调度

**实现**:
```python
# 一组 MPI 任务
for i in range(8):
    job.gang_id = "mpi-training-001"
```

**调度逻辑**:
- 只有当节点有足够 slots 容纳整个 gang 时才调度
- 所有 gang 成员必须同时处于 ready 状态

### 3.4 批处理调度

**目标**: 相似任务批量调度，提高效率

**实现**:
```python
# 一批图像处理任务
for image in images:
    job.batch_key = "image-processing-batch-001"
```

**评分**:
- 批量大小加分: 0-100 分（批量越大分数越高）
- 10+ 任务的批量获得满分 100

### 3.5 任务抢占

**目标**: 高优先级任务抢占低优先级任务

**抢占规则**:
1. 优先级差距 >= 40
2. 被抢占任务运行时间 < 5 分钟
3. 高优先级任务有 deadline 或 SLA
4. 被抢占任务标记为 `preemptible=True`

**实现**:
```python
# 可抢占任务
low_priority_job.priority = 30
low_priority_job.preemptible = True

# 高优先级任务
high_priority_job.priority = 90
high_priority_job.deadline_at = now + timedelta(hours=1)

# 抢占检查
should_preempt, reason = should_preempt_for_job(
    high_priority_job,
    low_priority_job,
    now=now,
)
```

### 3.6 Deadline 调度

**目标**: 确保任务在截止时间前完成

**实现**:
```python
# 设置截止时间
job.deadline_at = datetime(2026, 3, 30, 18, 0, 0)
```

**优先级调整**:
- 剩余时间 < 1 小时: +30 分
- 剩余时间 < 6 小时: +15 分
- 剩余时间 < 24 小时: +5 分

### 3.7 SLA 管理

**目标**: 监控和防止 SLA 违约

**实现**:
```python
# 设置 SLA
job.sla_seconds = 3600  # 1 小时内完成
```

**风险评估**:
```python
risk_score, risk_level = calculate_sla_breach_risk(job, now=now)
# risk_level: none/low/medium/high/critical/breached
```

**优先级调整**:
- SLA 消耗 > 80%: +20 分

---

## 4. 综合评分算法

### 4.1 评分组成

```python
total_score = (
    priority_score          # 0-100: 基础优先级
    + age_score             # 0-60: 等待时间
    + scarcity_score        # 0-100: 节点稀缺度
    + reliability_score     # 0-20: 节点可靠性
    + strategy_score        # 0-100: 调度策略
    + zone_bonus            # 0-10: 可用区匹配
    + resource_fit_bonus    # 0-24: 资源匹配
    + power_bonus           # 0-15: 功率效率
    + thermal_bonus         # 0-10: 热状态
    + affinity_bonus        # 0-20: 亲和性
    + sla_urgency           # 0-30: SLA 紧急度
    + batch_bonus           # 0-15: 批量共置奖励
    - load_penalty          # 0-40: 负载惩罚
    - recent_failure_penalty # 0-40: 最近失败惩罚
    - anti_affinity_penalty  # 0-50: 反亲和性惩罚
)
```

**总分范围**: -130 到 504

### 4.2 评分示例

#### 示例 1: 数据密集型边缘任务

```python
job = Job(
    priority=70,
    scheduling_strategy="locality",
    data_locality_key="sensor-data-2024",
    max_network_latency_ms=50,
    prefer_cached_data=True,
)

node = Node(
    network_latency_ms=30,
    cached_data_keys=["sensor-data-2024"],
    active_lease_count=2,
    max_concurrency=10,
)

# 评分计算
priority_score = 70
strategy_score = 100  # 数据缓存命中 + 低时延
locality_bonus = 15   # 数据缓存命中
load_penalty = 8      # 20% 负载

total = 70 + 100 + 15 - 8 = 177
```

#### 示例 2: 高优先级紧急任务

```python
job = Job(
    priority=90,
    deadline_at=now + timedelta(minutes=30),
    sla_seconds=3600,
    scheduling_strategy="performance",
)

# 有效优先级
effective_priority = 90 + 30 (deadline) + 20 (SLA) = 140

node = Node(
    reliability_score=0.95,
    cpu_cores=16,
    thermal_state="cool",
)

# 评分计算
priority_score = 100  # 上限
age_score = 10
strategy_score = 90   # 高性能节点
reliability_score = 19
thermal_bonus = 10

total = 100 + 10 + 90 + 19 + 10 = 229
```

---

## 5. 数据库迁移

```sql
-- 调度策略和亲和性
ALTER TABLE jobs ADD COLUMN scheduling_strategy VARCHAR(32);
CREATE INDEX idx_jobs_scheduling_strategy ON jobs(scheduling_strategy);

ALTER TABLE jobs ADD COLUMN affinity_labels JSON DEFAULT '{}';
ALTER TABLE jobs ADD COLUMN affinity_rule VARCHAR(32);
ALTER TABLE jobs ADD COLUMN anti_affinity_key VARCHAR(128);
CREATE INDEX idx_jobs_anti_affinity_key ON jobs(anti_affinity_key);

-- 业务调度
ALTER TABLE jobs ADD COLUMN parent_job_id VARCHAR(128);
CREATE INDEX idx_jobs_parent_job_id ON jobs(parent_job_id);

ALTER TABLE jobs ADD COLUMN depends_on JSON DEFAULT '[]';
ALTER TABLE jobs ADD COLUMN gang_id VARCHAR(128);
CREATE INDEX idx_jobs_gang_id ON jobs(gang_id);

ALTER TABLE jobs ADD COLUMN batch_key VARCHAR(128);
CREATE INDEX idx_jobs_batch_key ON jobs(batch_key);

ALTER TABLE jobs ADD COLUMN preemptible INTEGER DEFAULT 1;
ALTER TABLE jobs ADD COLUMN deadline_at TIMESTAMP;
CREATE INDEX idx_jobs_deadline_at ON jobs(deadline_at);

ALTER TABLE jobs ADD COLUMN sla_seconds INTEGER;
```

---

## 6. 使用示例

### 6.1 边缘视频处理

```python
job = Job(
    kind="video.transcode",
    priority=60,
    scheduling_strategy="locality",
    data_locality_key="video-raw-2024-03",
    max_network_latency_ms=100,
    prefer_cached_data=True,
    power_budget_watts=150,
    thermal_sensitivity="high",
)
```

### 6.2 分布式训练（Gang 调度）

```python
gang_id = str(uuid.uuid4())
for rank in range(8):
    job = Job(
        kind="ml.training",
        priority=80,
        scheduling_strategy="performance",
        gang_id=gang_id,
        required_gpu_vram_mb=8192,
        affinity_labels={"gpu_type": "nvidia-a100"},
        affinity_rule="required",
    )
```

### 6.3 批量图像处理

```python
batch_key = f"image-batch-{date.today()}"
for image in images:
    job = Job(
        kind="image.process",
        priority=40,
        scheduling_strategy="binpack",
        batch_key=batch_key,
        preemptible=True,
    )
```

### 6.4 紧急任务（抢占）

```python
job = Job(
    kind="alert.process",
    priority=95,
    scheduling_strategy="performance",
    deadline_at=now + timedelta(minutes=15),
    sla_seconds=900,
    preemptible=False,
)
```

---

## 7. 性能优化建议

### 7.1 调度策略选择

| 工作负载类型 | 推荐策略 | 原因 |
|------------|---------|------|
| 通用 Web 服务 | Spread | 均匀分布，避免热点 |
| 批处理任务 | Binpack | 节省资源，降低功耗 |
| 边缘计算 | Locality | 减少数据传输 |
| 实时计算 | Performance | 低延迟，高吞吐 |
| 混合负载 | Balanced | 平衡各方面需求 |

### 7.2 边缘算力优化

1. **数据预热**: 提前将常用数据缓存到边缘节点
2. **网络拓扑**: 合理规划节点 zone，减少跨 zone 调度
3. **功率预算**: 根据节点功率容量合理设置任务功率预算
4. **热管理**: 监控节点热状态，及时调整调度策略

### 7.3 业务调度优化

1. **优先级分层**: 合理设置优先级，避免优先级反转
2. **依赖链优化**: 减少依赖深度，增加并行度
3. **Gang 调度**: 只在必要时使用，避免资源浪费
4. **SLA 监控**: 实时监控 SLA 风险，提前调整优先级

---

## 8. 监控指标

### 8.1 调度效率

- **调度延迟**: 任务从 pending 到 leased 的时间
- **调度成功率**: 成功调度的任务比例
- **节点利用率**: 各节点的平均负载

### 8.2 边缘算力

- **数据局部性命中率**: 任务在有缓存数据的节点上执行的比例
- **网络时延分布**: 任务执行节点的网络时延分布
- **功率效率**: 单位功耗完成的任务数

### 8.3 业务调度

- **SLA 达成率**: 在 SLA 时间内完成的任务比例
- **Deadline 达成率**: 在 deadline 前完成的任务比例
- **抢占次数**: 任务被抢占的次数
- **Gang 调度等待时间**: Gang 任务等待所有成员就绪的时间
