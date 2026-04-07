# ADR 0005: 探针使用 redis-py 作为 Redis 客户端

- Status: Accepted
- Date: 2025-03-14
- Scope: 探针使用 redis-py 作为 Redis 客户端

> Source of truth: code and tests override ADR text. See ADR 0052 when documentation and implementation diverge.

## 决策

探针采用 **redis-py**（同步客户端）作为 Redis 通信依赖，不再遵守「仅标准库」约束。

## 理由

1. **协议复杂度**：Redis 协议（RESP）需正确序列化/反序列化，自实现易出错且难以维护。
2. **工业级成熟**：redis-py 为官方推荐客户端，被广泛使用和持续维护。
3. **探针职责边界**：探针为独立守护进程，与网关共享 Redis 部署，引入单一成熟依赖的风险可控。
4. **部署一致性**：网关已依赖 redis（async），探针使用同步版本，部署时仅需同一 redis 包。

## 备选方案

- **socket 自实现**：仅用标准库，需自实现 RESP 协议；工作量大、易引入 bug，不采纳。
- **纯文件状态**：用本地文件替代 Redis；无法与网关/SSE 联动，违反架构设计，不采纳。

## 后果

- 探针 `requirements.txt` 显式依赖 `redis`。
- 需在部署说明中标注探针依赖 redis-py，与 .cursorrules 的「仅标准库」表述存在例外，以本 ADR 为准。
