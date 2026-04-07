# 0020: 轻量级认证审计流与强隔离 Redis 依赖 (Lightweight Auth Audit Stream & Required Redis Deps)

- **状态**: 接受
- **日期**: 2026-03-23

## 1. 背景上下文
在 V3 架构中，Redis 作为核心控制面和 LRU 缓存占据重要地位。然而面临以下两个挑战：
1. **缺乏细粒度审计**: 面向公网暴露的 Gateway 需要捕捉越权或爆破试探。历史上捷报仅落盘在 Python 日志中，难以配合可视化面板采集。
2. **Redis 弱影响降级被击穿**: `get_redis` 依赖默认返回 `RedisClient | None`。部分强依赖 Redis 的业务（如 IoT, Scenes, Scheduler）在 Redis 短时宕机时，会因未做 `NoneType` 判断而直接抛出 HTTP 500 `AttributeError` 崩溃，违背了 503 优雅降级原则。

## 2. 决策选项
1. **方案 A (保持原样)**: 继续容忍 500 报错并忽略审计体系。
2. **方案 B (引入 PostgreSQL 审计表)**: 将登录、密码重置等行为同步写入关系型数据库。
3. **方案 C (基于 Redis Stream 的旁路审计 + get_redis_required 强隔离)**: 充分压榨 Redis 高吞吐特性，打造轻量级滚动追踪。

## 3. 评估对比
- 方案 B 会增加关键链路的 DB I/O 阻塞，影响高并发登录或冲击。
- 方案 C 通过 `redis.lpush` + `redis.ltrim` 构筑固定长度的日志流（默认 500 条），即存即删，无内存泄漏风险；并通过封锁式的 `get_redis_required` 依赖将 500 崩溃前置阻断为 503 熔断。

## 4. 最终决定
采用 **方案 C**。
1. 新增 `get_redis_required`，对强依赖 Redis 的接口强制前置拦截。
2. 引入 `_append_auth_audit_event` 异步方法包裹 Redis 写入，捕获所有 `Exception` 确保审计流绝不影响主业务流。
3. 暴露 `/api/v1/audit/events` 与 `/users/{id}/devices` 以供管理员溯源横向移动轨迹。

## 5. 影响范围
- 彻底斩断了底层组件 `NoneType` 对业务层的穿透，确保了 503 降级的 100% 覆盖。
- 系统进入准“零信任网络”监控级，终端管理员获得了立体的溯源视角。
