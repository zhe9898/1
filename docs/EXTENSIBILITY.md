# ZEN70 Extensibility Guide

本文档描述当前仓库允许的扩展边界，以及哪些做法会直接破坏架构收口。

## 先看结论

- 扩展的正式入口是 `capability -> surface -> policy -> service contract -> execution contract`。
- Pack、Extension SDK、Runner job kind、控制台协议都可以扩展，但都必须回到受控契约。
- 文档不能替代代码事实源；实现和测试优先于本文件。

## 当前稳定扩展面

### 1. Kernel capability 与 surface

- 公开能力必须来自 kernel capability 注册，而不是前端自行拼装菜单或路由。
- 控制面 surface 必须能追溯到 capability、scope、pack 和 policy gate。
- 相关锚点：
  - [adr/0024-gateway-kernel-default-and-backend-driven-control-plane.md](adr/0024-gateway-kernel-default-and-backend-driven-control-plane.md)
  - [adr/0052-code-backed-architecture-governance-registry.md](adr/0052-code-backed-architecture-governance-registry.md)

### 2. Pack boundary

- Pack 表达能力合同和运行边界，不等于默认 kernel 自动启用。
- 当前 pack registry 由后端注册表导出，legacy profile 只负责把历史输入展开成 pack 选择。
- `gateway-full` / `full-pack` 已不再是正式产品面。
- 相关文档：
  - [pack-matrix.md](pack-matrix.md)
  - [profile-matrix.md](profile-matrix.md)
  - [adr/0035-pack-registry-and-kernel-pack-boundary.md](adr/0035-pack-registry-and-kernel-pack-boundary.md)

### 3. Runtime policy

- 运行时调度与治理配置统一经由 `PolicyStore + RuntimePolicyResolver` 消费。
- `system.yaml` 仍是 bootstrap 事实源，但运行时模块不得各自直读 YAML 做策略判断。
- 新调度配置段应先进入 PolicyStore，再由消费方读取只读视图。
- 相关文档：
  - [adr/0049-scheduling-policy-store-single-source-of-truth.md](adr/0049-scheduling-policy-store-single-source-of-truth.md)
  - [protocol-matrix.md](protocol-matrix.md)

### 4. Runner job kind

- Go runner 通过 `AcceptedKinds` 声明可接任务类型。
- 新 kind 需要同时补执行器处理器、错误分类、注册/心跳声明和测试。
- job kind 是正式扩展边界，比“能力标签猜执行器”更稳定。
- 相关文档：
  - [FULL_CHAIN_IMPLEMENTATION.md](FULL_CHAIN_IMPLEMENTATION.md)
  - [adr/0050-runner-extended-job-kinds-and-accepted-kinds-dispatch.md](adr/0050-runner-extended-job-kinds-and-accepted-kinds-dispatch.md)

### 5. 控制台协议消费

- 前端是 backend-driven consumer，不是独立事实源。
- 新页面、新 schema、新 action dialog 必须由后端协议驱动。
- 当前认证边界是 cookie-primary：HTTP 与 SSE 默认走 cookie，前端只保留会话 claims；若后端短时返回旋转后的 access token，前端只用于即时 claims 对齐，不做持久 bearer 存储。
- UI 只展示安全错误码和用户级提示，不直接渲染后端原始错误详情。

## 推荐扩展路径

### 新增 capability / surface

1. 先定义 capability 和所需 scope。
2. 让 surface resolver 基于 capability、pack、policy 暴露受控 surface。
3. 补协议测试和架构门禁。
4. 最后再让前端消费该 surface。

### 新增 pack

1. 在 pack registry 中声明 `key / routers / services / capability_keys / delivery_stage / deployment_boundary`。
2. 确定它是 `runtime-present`、`mvp-skeleton` 还是 `contract-only`。
3. 不要把 pack 路由偷塞回默认 kernel。
4. 同步更新 pack/profile 文档与测试。

### 新增 runner kind

1. 在 Go executor 中新增 kind 处理器。
2. 给出结构化错误分类。
3. 更新 `RUNNER_ACCEPTED_KINDS` 契约和注册/心跳上报。
4. 补对应控制面调度与失败路径测试。

### 新增调度策略

1. 先扩展 PolicyStore 缓存和版本化写入能力。
2. 通过 RuntimePolicyResolver 暴露给运行时。
3. 在决策、执行回报、重试或补偿链路里记录版本快照。
4. 禁止在新模块里直接 `yaml.safe_load(system.yaml)`。

### 新增控制台页面

1. 先定义后端 schema、列表、action 和安全错误投影。
2. 前端仅渲染协议，不自行创造新的业务状态机。
3. 鉴权默认走 cookie，会话信息只从 session/claims 获取。
4. 若页面涉及核心聚合写入，必须经过 owner service 或 command handler。

## 明确禁止

- 禁止在运行时代码里直接读取并缓存 `system.yaml` 做策略判断。
- 禁止 route / worker / sentinel 直接写核心聚合状态字段。
- 禁止扩展绕过 service guard 或核心 registry 直接写对象。
- 禁止在前端持久化 bearer token 到 `localStorage` 或 `sessionStorage`。
- 禁止把 secret、connector 明文配置或后端原始错误详情直接回显给 UI。

## 文档更新要求

- 扩展点如果影响正式边界，先更新 ADR，再更新本文件。
- 如果某项能力只是历史兼容或阶段性草案，必须明确标出，不得写成“当前默认行为”。
- 若实现与本文不一致，以代码和测试为准，并回写文档。
