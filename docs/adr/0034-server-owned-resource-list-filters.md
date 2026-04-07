# ADR 0034: 服务端拥有的资源列表过滤合同

- Status: Accepted
- Date: 2026-03-27
- Scope: 服务端拥有的资源列表过滤合同

> Source of truth: code and tests override ADR text. See ADR 0052 when documentation and implementation diverge.

## 1. 背景

在 ADR 0033 之后，控制台的动作、文案、状态语义已经由后端拥有，但资源列表仍有最后一个漂移点：

- dashboard 已经由后端下发 route/query intent
- 前端资源页会读取这些 query
- 但真正的过滤动作仍发生在浏览器里，也就是“先拉全量列表，再本地筛”

这种做法在 Phase 3 已经不够了，原因很直接：

- 同一份 query 并没有被后端真正执行，无法称为完整合同
- 带过滤的列表页收到 SSE 事件后，局部 `upsert` 会把不匹配对象塞回当前结果集
- 页面越多，本地过滤逻辑越容易再次分叉

## 2. 决策

### 2.1 列表过滤必须由服务端执行

以下资源列表统一支持 query 过滤：

- `GET /api/v1/jobs`
- `GET /api/v1/nodes`
- `GET /api/v1/connectors`

当前固定过滤键：

- `jobs`: `job_id`、`status`、`lease_state`、`priority_bucket`、`target_zone`、`required_capability`
- `nodes`: `node_id`、`enrollment_status`、`drain_status`、`heartbeat_state`、`capacity_state`、`attention`
- `connectors`: `connector_id`、`status`、`attention`

### 2.2 schema 必须暴露过滤合同

资源 schema 的 `policies` 现在必须包含：

- `list_query_filters`

它声明该资源支持哪些 query key，以及这些 key 的匹配语义，例如：

- `exact`
- `status-view`
- `derived`
- `contains`

### 2.3 带过滤视图下的 SSE 不再直接本地 upsert

当资源页当前带有过滤条件时：

- 收到相关 SSE 事件后，不再直接把对象合并进本地列表
- 而是重新请求当前 query 对应的服务端结果

无过滤视图仍可继续走本地 `upsert`，以减少不必要的重拉。

## 3. 影响

### 正向影响

- dashboard drill-down 真正变成“后端意图 -> 后端结果”
- 资源列表与诊断跳转共享同一套 query 语义
- 过滤视图下的事件一致性更强
- Phase 3 可以正式满足“读模型和写模型都由后端驱动”

### 代价

- API 列表接口需要承担更多过滤职责
- 带过滤视图在高频 SSE 下会触发额外重拉
- 需要持续维护 `policies.list_query_filters` 与实际后端实现一致

## 4. 后续约束

未来任何变更只要出现以下任一情况，都必须更新或替代本 ADR：

- 新增资源列表过滤，但没有服务端 query 支持
- schema 没有同步暴露 `list_query_filters`
- dashboard 再次下发前端独占的过滤语义
- 带过滤列表页重新回到“收到事件就本地 upsert”模式
