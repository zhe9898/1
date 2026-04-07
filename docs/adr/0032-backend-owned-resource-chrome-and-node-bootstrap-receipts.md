# ADR 0032: 后端拥有的资源页文案与节点启动回执

- Status: Accepted
- Date: 2026-03-27
- Scope: 后端拥有的资源页文案与节点启动回执

> Source of truth: code and tests override ADR text. See ADR 0052 when documentation and implementation diverge.

## 1. 背景

ADR 0030 已经把资源表单和动作 payload 收回后端，但当时仍有两个真实残口：

- 页面标题、说明、空状态仍然写死在前端各自页面里
- 节点发证虽然已经返回一次性 token，但发证后的 runner 启动命令仍然由前端本地拼装

这意味着控制台看上去是后端驱动的，但资源页文案和最敏感的入网引导仍然部分落在浏览器侧。

## 2. 决策

### 2.1 Resource schema 必须拥有资源页文案

`ResourceSchemaResponse` 现在新增：

- `title`
- `description`
- `empty_state`

前端资源页必须消费这些字段，而不能再为 `Nodes`、`Jobs`、`Connectors` 硬编码页面文案。

### 2.2 Resource policies 是可见 UI 合同，不是死元数据

前端仍然可以把 policy 渲染成标签或说明，但 policy 的值本身必须来自后端下发的 `policies`。

当前典型字段包括：

- `resource_mode`
- `ui_mode`
- `secret_delivery.visibility`

### 2.3 节点发证回执必须拥有启动指引

`POST /api/v1/nodes` 和 `POST /api/v1/nodes/{id}/token` 现在返回：

- 一次性 `node_token`
- `auth_token_version`
- `bootstrap_commands`
- `bootstrap_notes`

前端必须直接渲染这份回执，不能再在浏览器里重建 runner 的环境变量导出和启动命令。

## 3. 影响

### 正向影响

- 资源页不再重复维护产品文案和入网引导。
- 节点启动指引进入后端可审计合同范围。
- 一次性 secret 流程更清晰，因为交付策略和后续操作说明会一起返回。
- Phase 3 更接近真正的后端驱动控制台，而不是“后端给表单，前端还在补业务文案”。

### 代价

- schema 变更现在会直接影响 UI 文案和空状态，合同漂移风险更高。
- 后端必须承担后续 runner 启动文案和命令模板的演进责任。
- 前端仍保留展示层职责，例如布局和 CSS 样式映射。

## 4. 后续约束

未来任何变更只要发生以下任一情况，都必须更新或替代本 ADR：

- 重新在前端资源页里硬编码页面文案或空状态
- 节点发证后重新由前端拼装 runner 启动命令
- 新增资源页，但没有后端拥有的 `title`、`description`、`empty_state`
