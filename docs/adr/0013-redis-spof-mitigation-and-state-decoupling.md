# ADR 0013: Redis 单点承压缓解与状态解耦策略 (SPOF Mitigation)

- Status: Accepted
- Date: Unknown
- Scope: Redis 单点承压缓解与状态解耦策略 (SPOF Mitigation)

> Source of truth: code and tests override ADR text. See ADR 0052 when documentation and implementation diverge.

## Context
在当前的 ZEN70 V2.0 架构中，Redis 承担了过于沉重的“超载中枢”角色（上帝组件）：
1. **拓扑探针状态机** (`topology_sentinel` 定期刷新硬件探针到 KV)。
2. **全局事件总线** (Pub/Sub SSE 推送，如 `switch:events`；~~`board:events` v3.43 已从运行面移除~~)。
3. **分布式锁管理器** (防并发执行、定时任务互斥)。
4. **API 热数据缓存**。

由于 `redis-py` 和 Python FastAPI Gateway 之间的强阻塞特性，以及 Redis 本身的单核串行特性。一旦发生：
- AOF 物理落盘 (fsync) 引发的 I/O 夯死。
- OOM 策略引发的 CPU 计算满载。
- 偶发的长事务或网络抖动。

整个网关（Gateway）将无法获取心跳、无法更新拓扑甚至无法推送流，瞬间引发全体 503 级联雪崩（Cascading Failure）。
在不引入重型中间件（如 Kafka / RabbitMQ）且继续维持全栈单节点高韧性的前提下，我们需要对 Redis 单点故障（SPOF）进行低成本、高回报的架构重构。

## Decision

任何脱离 `system.yaml` 与编译器流水线的架构调整均视为违建。我们采取“**应用层降级 + IaC 声明式基础扩容**”的 3 阶段解耦策略：

### Phase 1: 应用层状态降级兜底 (Gateway LRU Fallback)
赋予 API Gateway 在 Redis 闪断时的“抗休克”能力（纯代码层，无基建变动）：
在网关（如能力矩阵和鉴权）数据流上方，引入 Python 本地的 `cachetools.TTLCache`（TTL=5秒）。
当操作 Redis 发生 `TimeoutError` 时，**严禁抛出 503**，必须无缝降级读取 5 秒前的“陈腐内存数据”，并在 Header 宣告 `X-Fallback-State: stale`。用微小的最终一致性延迟换取最高可用性。

### Phase 2: IaC 声明式基建解耦 (Split Schema)
*(注：Redis 单实例不支持对不同 DB 设置不同的 `maxmemory` 驱逐策略。因此单纯把锁放在 DB 1、缓存放在 DB 0 并不能防止锁被误杀，这在物理架构上是无效的。)*

当并发量达到临界值，**必须在 IaC 框架内进行结构重组**：
1. **扩展编排编译器与 Schema**：升级 `scripts/iac_core/models.py` 中的 `ServiceSettings`，将原单一的 `redis` 实例模型演进为分离式的结构定义。
2. **`system.yaml` 动态伸缩声明**：
   在配置文件中开放基础设施切割能力，允许编译器渲染出两个独立的进程：
   - `services.state_core` (Redis AOF 模式)：负责锁与高可用拓扑。配置 `noeviction` 保障强持久。
   - `services.event_bus` (纯内存模式)：负责 Pub/Sub 与弱状态 Cache。配置 `allkeys-lru` 并在 Docker 层面完全禁用外挂卷。

### Phase 3: 事件流底层驱动平移 (PostgreSQL NOTIFY)
若纯内存 `event_bus` 遭遇极速消息流颈瓶，则通过更改 `backend/core/events_schema` 配置字典，在应用侧将 SSE 事件分发引擎由 `redis.pubsub` 切换为系统已有的 `PostgreSQL LISTEN/NOTIFY` 机制。这不需要增加任何新容器，直接重用现存最稳定的强一致性基建引擎榨取吞吐量。

## Consequences

### Positive
- **绝对契合 IaC 本质**：所有的拆分不是靠手工维护多个 docker-compose，而是完全收束进了 `system.yaml` 语义与 `compiler.py` 的解析树。
- **物理防火墙**：通过 IaC 分制完全隔离了持久化 IO（AOF 刷盘阻塞）与内存风暴对业务的影响。
- **避免心智损耗**：第一阶段无需变更架构即可落地抗突刺能力，扩展随业务自然演进。

### Negative
- **升级成本**：Phase 2 要求对现有的代码库环境抽取（`os.getenv("REDIS_HOST")`）进行梳理，将 `CACHE_HOST` 和 `STATE_HOST` 脱钩。
- **运行时资源增加**：执行基座分裂会导致多运行至少一个 30MB 左右的纯内存容器。
