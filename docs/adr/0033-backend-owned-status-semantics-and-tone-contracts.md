# ADR 0033: 后端拥有的状态语义与 tone 展示合同

- Status: Accepted
- Date: 2026-03-27
- Scope: 后端拥有的状态语义与 tone 展示合同

> Source of truth: code and tests override ADR text. See ADR 0052 when documentation and implementation diverge.

## 1. 背景

在 ADR 0030、0031、0032 之后，控制台的大部分表单、动作、路由意图和资源页文案已经收回后端，但仍有一个真实残口没有闭上：

- `Jobs` 页面仍在前端本地把 `leased` 折叠成 `running`，把 `failed/canceled/expired` 折叠成 `failed`
- `Nodes` 页面仍在本地决定 `online`、`draining`、`fresh/stale`、`available/saturated` 的 badge 样式
- `Connectors` 页面仍在本地决定 `healthy / configured / error` 的展示 tone
- `Dashboard` 的 summary、attention 和 diagnostics 仍然依赖前端页面各自维护 tone/badge 映射

这意味着前端虽然已经不再手写动作开关，但依然在“解释状态”。只要后端状态机再变一次，页面就会出现显示漂移。

## 2. 决策

### 2.1 所有资源状态都必须返回 `*_view`

控制面响应继续保留原始机读字段，例如：

- `status`
- `lease_state`
- `drain_status`
- `heartbeat_state`
- `capacity_state`

同时额外返回对应的展示合同：

- `status_view`
- `lease_state_view`
- `drain_status_view`
- `heartbeat_state_view`
- `capacity_state_view`
- `enrollment_status_view`
- `eligibility_view`

每个 view 统一包含：

- `key`
- `label`
- `tone`

前端必须消费这些 view，而不是在浏览器里重新折叠状态分组或推断 tone。

### 2.2 Dashboard 的 tone/severity 也属于后端合同

`/api/v1/console/overview` 和 `/api/v1/console/diagnostics` 现在也需要返回：

- `summary_cards[].tone_view`
- `attention[].severity_view`

这样 dashboard 卡片、attention 队列和 diagnostics 标签的语义来源都固定到后端。

### 2.3 前端只保留通用 theme 映射

前端仍然可以保留一层通用的 `tone -> CSS class` 映射，用于主题渲染；
但这层映射不能再掺杂资源级业务逻辑，例如：

- 不能再在某个页面里把 `leased` 特判成 `running`
- 不能再在某个页面里把 `configured`、`auth_required`、`error` 自己归类
- 不能再在 dashboard 里针对某个资源类型重写 severity/badge 决策

## 3. 影响

### 正向影响

- `Nodes / Jobs / Connectors / Dashboard` 的状态语义彻底回到后端。
- 前端不再为每个页面维护一份“近似状态机”。
- drill-down 过滤和资源状态展示终于共享同一套后端定义。
- 后续如果状态机演进，变更点集中在后端 helper 与合同测试。

### 代价

- 后端响应字段更多，合同面变厚。
- 状态 helper 现在是控制面 API 的一部分，必须被测试保护。
- 前端仍需保留一层通用 tone 样式映射，但这层映射必须保持资源无关。

## 4. 后续约束

未来任何变更只要出现以下任一情况，都必须更新或替代本 ADR：

- 前端页面重新按资源类型本地折叠状态分组
- 前端页面重新自己决定资源状态标签文案
- dashboard 重新在页面内部硬编码 tone/severity 业务语义
- 新增控制面资源响应，但没有配套的 `*_view` 展示合同
