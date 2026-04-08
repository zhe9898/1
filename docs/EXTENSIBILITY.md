# ZEN70 Extensibility Guide

## 结论先行

- 扩展的正式入口固定为 `capability -> surface -> policy -> service contract -> execution contract`。
- Pack、Extension SDK、Runner job kind、控制台协议都可以扩展，但都必须回到受控合同。
- 文档不能替代代码事实源；实现和测试优先于本文。

## 当前稳定扩展面

### 1. Kernel capability 与 surface

- 公开能力必须来自 kernel capability registry。
- 控制面 surface 必须带 capability、scope、pack 和 policy 元数据。
- kernel 只定义“有哪些面”，不负责“此刻给谁看”。

### 2. Pack boundary

- Pack 表达能力合同、交付阶段和部署边界。
- 运行时只接受显式 canonical pack keys。
- Pack 可以扩展 router、service 和 runtime owner，但不能变成新的产品 surface。

### 3. Runtime policy

- 运行时策略统一由 `PolicyStore + RuntimePolicyResolver` 消费。
- `system.yaml` 仍是 bootstrap 事实源，但运行时模块不得各自直接读 YAML 判定策略。
- 新调度配置必须先进入 PolicyStore，再暴露只读视图。

### 4. Runner job kind

- Go runner 通过 `AcceptedKinds` 声明可接任务类型。
- 新 kind 必须同时补执行器、错误分类、注册/心跳合同和测试。
- job kind 是正式扩展边界，比“猜执行器”稳定。

### 5. 控制台协议消费

- 前端是 backend-driven consumer，不是独立事实源。
- 新页面、新 schema、新 action dialog 必须由后端协议驱动。
- 控制台认证默认走 cookie；前端不持久化 bearer token。

## 推荐扩展路径

### 新增 capability / surface

1. 先定义 capability 与所需 scope。
2. 在 kernel surface registry 中声明 surface 合同。
3. 通过 control plane service 加可见性过滤。
4. 最后让前端消费该合同。

### 新增 pack

1. 在 pack registry 中声明 `key / routers / services / capability_keys / delivery_stage / deployment_boundary`。
2. 明确 runtime owner 和 selector hints。
3. 更新控制台展示、IaC 和测试。
4. 保证 pack 不回流默认 kernel path。

### 新增调度策略

1. 先扩展 PolicyStore 写入能力。
2. 通过 RuntimePolicyResolver 暴露只读结果。
3. 在决策、执行回报、重试和补偿链路上记录版本快照。

## 明确禁止

- 禁止在运行时代码里直接解析 `system.yaml` 做策略判断。
- 禁止 route、worker、sentinel 直接写核心聚合状态字段。
- 禁止扩展绕过 service guard 或 registry 直接写对象。
- 禁止在前端持久化 bearer token 到 `localStorage` 或 `sessionStorage`。
- 禁止把 secret、connector 明文配置或后端原始错误详情直接回显给 UI。
