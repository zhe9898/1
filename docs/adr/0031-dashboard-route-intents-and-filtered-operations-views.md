# ADR 0031：Dashboard 路由意图与过滤后的运维视图

- **状态**: 已接受
- **日期**: 2026-03-27
- **范围**: Phase 3 dashboard 产品化与 drill-down 行为

## 1. 背景

ADR 0030 已经把资源创建表单和动作 payload 收回后端所有权，但 dashboard 当时还缺一层关键收口：

- 卡片和诊断数据虽然来自后端，但 drill-down 行为仍大多是前端约定
- 从 dashboard 点击进入 `Nodes`、`Jobs`、`Connectors` 时，操作者经常只是落到一个宽泛列表页
- “高优先级 backlog”“只看 attention”“陈旧 lease” 这类过滤语义还不是后端合同的一部分

这意味着 dashboard 看起来像运营首页，但点击后仍要求用户自己重建操作意图。

## 2. 决策

### 2.1 Dashboard summary cards 由后端拥有

`GET /api/v1/console/overview` 现在返回：

- `summary_cards[]`

每张卡片拥有：

- `key`
- `kicker`
- `title`
- `value`
- `badge`
- `detail`
- `tone`
- `route`

前端可以把 `tone` 映射成视觉样式，但不能重建卡片含义或导航目标。

### 2.2 Attention 与 diagnostics 必须包含 route/filter intent

`overview.attention[]` 和 `diagnostics` 项现在都带后端签发的路由目标：

- `route_path`
- `query`

这些字段定义操作者应该落到哪里，以及应该看到哪一层过滤视图。

例子：

- stale lease 跳到 `Jobs`，并带 `lease_state=stale`
- 高优先级 backlog 跳到 `Jobs`，并带 `status=pending` 与 `priority_bucket=high`
- 节点健康 attention 跳到 `Nodes`，并带 `attention=attention`
- 连接器 attention 跳到 `Connectors`，并带 `attention=attention`

### 2.3 Diagnostics 可以直接暴露推荐动作

当某个诊断项存在安全且已经合同化的处置动作时，允许该诊断项直接携带后端拥有的 `actions[]`。

例子：

- 节点诊断可直接暴露 `rotate token`、`revoke`、`drain`、`undrain`
- 陈旧或阻塞任务诊断可直接暴露 `cancel`、`retry`、`explain`
- 连接器诊断可直接暴露 `test`、`invoke`

dashboard 可以通过共享 action dialog 渲染这些动作，但不能本地发明新的运维动作。

### 2.4 资源页必须消费 dashboard 下发的过滤条件

`Nodes`、`Jobs`、`Connectors` 现在都把 route query filter 视为控制面合同的一部分。

这一步还不等于服务端过滤，但它已经能保证当前阶段 drill-down 行为确定、稳定、可测试。

## 3. 影响

### 正向影响

- Dashboard 从只读概览页升级成真正的运营首页。
- 操作者会直接落到相关的舰队、队列或连接器切片，而不是宽泛列表页。
- 过滤语义成为后端合同的一部分，可被文档和测试固定。
- 前端不再自己发明运维导航语义。
- 操作者可以直接在 dashboard 完成常见处置动作，而不需要先跳转再定位。

### 代价

- Query key 兼容性现在变得重要，因为 dashboard 和资源页共享这一层合同面。
- 当前过滤逻辑仍在前端资源视图里执行；部署规模继续放大后，需要补服务端过滤以保证性能。
- 后端和前端必须对 route/filter key 保持完全一致。
- Dashboard 动作必须受限于已批准的资源动作，overview 页不能演化成第二套独立业务工作流引擎。

## 4. 后续约束

未来任何变更只要发生以下任一情况，都必须更新或替代本 ADR：

- 重新引入仅由前端拥有的 dashboard drill-down 逻辑
- 修改 route/filter key 却不同时更新资源页消费者和协议文档
- 新增 dashboard 运维卡片或诊断项，却没有后端签发的 route intent
- 在 triage 场景下重新把操作者导向宽泛列表页，而不是过滤后的运维视图
