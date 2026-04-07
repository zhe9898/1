# ADR 0030: 后端驱动的资源表单与动作对话框

- Status: Accepted
- Date: 2026-03-27
- Scope: 后端驱动的资源表单与动作对话框

> Source of truth: code and tests override ADR text. See ADR 0052 when documentation and implementation diverge.

## 1. 背景

ADR 0024 已经固定了控制面方向：默认 kernel 页面必须由后端驱动。ADR 0029 又把这个原则扩展到舰队动作和任务动作，但控制台当时还剩两个明显产品残口：

- 只有 `Jobs` 和 `Connectors` 已经拥有后端下发的表单 schema，而 `Nodes` 发证仍然是页面本地实现细节
- `Nodes`、`Jobs`、`Connectors` 各自使用自己的 prompt 弹法收集动作参数，这意味着动作合同虽然来自后端，但交互形态仍然散落在各页面代码里

这种状态足够做技术管理页，但还不够支撑产品级运维控制台。

## 2. 决策

### 2.1 资源创建表单必须由后端拥有

控制面对所有默认运维资源暴露 schema endpoint：

- `GET /api/v1/nodes/schema`
- `GET /api/v1/jobs/schema`
- `GET /api/v1/connectors/schema`

每个 schema 响应拥有：

- `resource`
- `policies`
- `submit_action`
- `sections[]`

前端必须渲染这些 schema，而不能再用硬编码字段列表重建 create/provision payload。

### 2.2 舰队发证必须通过控制面合同返回一次性机器凭证

节点发证和 token 轮换返回：

- `node`
- `node_token`
- `auth_token_version`

控制台可以在响应后立即展示一次性凭证和安装提示，但必须把 token 视为瞬时 UI 状态，不能寄望后续 list/detail API 再次取回。

### 2.3 运维动作统一走一个共享对话框合同

节点、任务、连接器动作仍然由后端 `actions[]` 拥有，但控制台统一通过共享 action dialog 渲染这些动作字段。

共享 dialog 消费：

- `label`
- `confirmation`
- `fields[]`
- 字段默认值和输入类型

页面可以决定何时打开 dialog，但不能再手工拼 prompt 或本地动作 schema。

## 3. 影响

### 正向影响

- 默认控制台更像一个完整产品，而不是三张互不相干的后台页。
- 资源字段的新增或修改会先发生在后端合同，再体现在前端。
- 节点发证和 token 轮换变成控制面的一等产品流程，而不是运维手工知识。
- `Nodes` / `Jobs` / `Connectors` 的动作体验保持一致。

### 代价

- 后端 schema 现在覆盖了更多 UI 细节，接口变更必须一并验证前端渲染路径。
- 一次性 node token 展示需要严格处理，避免 UI 误导操作者以为该 secret 后续还能重新获取。
- 共享 dialog 行为本身也成为控制面合同面的一部分，必须保持向后兼容。

## 4. 后续约束

未来任何变更只要发生以下任一情况，都必须更新或替代本 ADR：

- 重新为默认控制面资源引入硬编码 create/provision 表单
- 重新引入按页面各自实现的 prompt 式动作参数收集
- 通过 list/detail 接口暴露可复用机器 secret，而不是仅在 provision/rotate 响应中一次性返回
- 新增默认控制面页面，但其写模型不受后端 schema/actions 驱动
