# Multi-Language Layered Architecture

本文件描述当前仓库按语言划分的正式边界，而不是历史阶段的设想产品线。

## Final language map

- Python: 控制面 API、领域服务、治理门禁、IaC 编译与部署脚本
- Go: `runner-agent` 执行面、任务轮询、lease 续租、结果上报
- TypeScript: Vue 控制台，消费后端协议与 cookie 会话
- Swift: `health-pack` iOS skeleton
- Kotlin: `health-pack` Android skeleton
- YAML: `system.yaml` 声明式 bootstrap 输入

## Runtime surface

- 正式公开 runtime profile 只有 `gateway-kernel`
- `gateway`、`gateway-core`、`gateway-iot`、`gateway-ops` 只作为兼容输入或 pack preset 存在
- `gateway-full` 已退出正式运行时 surface

## Layer boundaries

### Control Plane

- Python 持有身份、授权、调度、审计、连接器、工作流与控制面路由
- 运行时策略通过 `PolicyStore + RuntimePolicyResolver` 暴露
- 核心状态写入必须经过 domain service / command handler

### Execution Plane

- Go runner 负责 `pull -> execute -> renew -> complete/fail`
- 节点能力通过 `AcceptedKinds` 和机器身份链表达
- 执行结果、失败分类和续租行为回到控制面协议

### Console Plane

- TypeScript 前端不拥有独立业务事实源
- HTTP 和 SSE 默认走 cookie 鉴权
- 前端只维护会话 claims 和协议投影，不持久化 bearer token

### Extension Plane

- Pack 表达能力合同、交付阶段和部署边界
- 当前 pack 集合包括 `iot-pack`、`ops-pack`、`media-pack`、`health-pack`、`vector-pack`
- pack 可以扩展 capability 和 runtime owner，但不能绕过 kernel/service contract 直接写核心状态

## Compatibility-only inputs

以下名称可以在迁移路径中被接受，但不应再出现在正式产品叙事、OpenAPI surface 或控制台公开能力目录中：

- `gateway`
- `gateway-core`
- `gateway-iot`
- `gateway-ops`

## Related documents

- [pack-matrix.md](pack-matrix.md)
- [profile-matrix.md](profile-matrix.md)
- [protocol-matrix.md](protocol-matrix.md)
- [adr/0046-kernel-only-runtime-surface-and-compatibility-retirement.md](adr/0046-kernel-only-runtime-surface-and-compatibility-retirement.md)
- [adr/0049-scheduling-policy-store-single-source-of-truth.md](adr/0049-scheduling-policy-store-single-source-of-truth.md)
