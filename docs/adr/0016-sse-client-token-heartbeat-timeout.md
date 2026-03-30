# ADR 0016: Client-Token-in-URL 与 Redis SETEX 实现 SSE 心跳超时机制

> **法典 8.1.4**: 任何涉及核心技术栈更迭、API 契约修改、硬件抽象层重构或偏离“建议级”规范的重大变更，必须在合并代码前，在此提交 Markdown 格式的 ADR 文档。

## Client-Token-in-URL & Redis SETEX for SSE Heartbeat

- **状态**: 接受
- **日期**: 2026-03-22

## 1. 背景上下文

法典 §2.1 规定：“前端 30 秒发送 Ping，后端连续 45 秒未收到 Ping 必须显式调用 `cancel()` 强斩挂起协程，杜绝 FD 耗尽”。

由于 EventSource API 的局限性，在建立 SSE 连接时无法自定义 HTTP Headers，因此在跨 Worker 或跨节点的拓扑中，网关需要一种机制来：
1. **可靠地关联连接与 Ping 请求**：无论是不是首包延迟或丢失，前端能准确告诉后端当前 Ping 属于哪个 SSE 流。
2. **多 Worker 一致性**：/events 挂载于 Worker A，而后续的 /events/ping 路由请求可能被哈希至 Worker B。单机内存字典（如 `_sse_last_ping`）无法实现跨进程兜底。

原先尝试让服务端生成 UUID 并在首包下发给前段，但这存在竞争条件：如果前端首包接收超时，或后端首包发送因为缓冲拥塞而滞后，前端将无法获取 `connection_id`，进而无法发送 Ping，触发假阳性熔断。

## 2. 决策选项

1. **方案 A: 依赖服务端首包下发 UUID + 本地内存驻留**  
   - 依赖 EventSource `onmessage("connected")` 读取 ID 后作为 Ping 载荷。
2. **方案 B: JWT Token 映射**  
   - 通过 JWT Claim 直接作为连接组标识，每个 User 只能保持最新的一条连接，旧连接相互覆盖。
3. **方案 C: Client-Token-in-URL + Redis SETEX**  
   - 客户端建连前通过 `crypto.randomUUID()` 预生成 `client_token` 并拼接于 URL query。
   - 使用 Redis `SETEX sse:ping:{token} 45` 实现跨 Worker 超时锁。

## 3. 评估对比

### 方案 A (服务端首包 + 本地内存)
- **优势**: 对前端无侵入。
- **劣势**: 在负载均衡多 Worker 场景下状态分裂无法实现；同时存在假阳性超时竞争风险。

### 方案 B (JWT 映射)
- **优势**: 与用户身份天然绑定，安全。
- **劣势**: 同一用户多终端登录时会造成连接互踢，无法满足多设备同频监控的需求。

### 方案 C (Client-Token-in-URL + Redis)
- **优势**: 客户端主控 ID，杜绝了网络延迟导致的“盲 Ping”问题；采用 Redis 天然支持跨节点、跨 Worker，符合工业级分布式应用架构。若 Redis 闪断，基于 ADR 0013 退避降级，可安全兜底为“免死金牌”。
- **劣势**: 前端建立 EventSource 前需要显式生成 UUID 并组装 URL，增加少量逻辑负担。无 token 的非法客户端将在 45s 后强制断开（预期内防线）。

## 4. 最终决定

选择 **方案 C (Client-Token-in-URL + Redis SETEX)**，将其确立为目前 ZEN70 SSE 保活与连接追踪的唯一标准模式。
后端新增 `POST /api/v1/events/ping` 端点（要求 JWT 鉴权），主频路由 `/api/v1/events` 使用 Redis `EXISTS` 判断键存活，并于 `finally` 块中执行 `DEL` 清理。

## 5. 影响范围

- **现有模块**: 旧的 `gateway_routes.py` 由于使用本地内存被全面声明 `deprecated`。
- **前端适配**: `utils/sse.ts` 已重构以适应 Client-Token 注入，并移除首包依赖。
- **安全防线**: JWT 认证被融合在心跳（`/events/ping`）端点上，而并未强加于建连握手，确保“未登录用户45s安全静默断开”。
