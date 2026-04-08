# Multi-Language Layered Architecture

本文描述当前仓库按语言与领域划分的正式边界，而不是历史产品线。

## Language Map

- Python: `backend/kernel`、`backend/control_plane`、`backend/runtime`、`backend/extensions`、`backend/platform`，以及 IaC 编译和运维脚本
- Go: `runner-agent` 执行面、任务轮询、续租与结果上报
- TypeScript: Vue 控制台，消费后端协议与 cookie 会话
- Swift: `health-pack` iOS skeleton
- Kotlin: `health-pack` Android skeleton
- YAML: `system.yaml` bootstrap 输入

## Domain Map

### Kernel

- 事实源、注册表、合同、治理规则
- 不放 HTTP，不直接做运行时准入判定

### Control Plane

- backend-driven 管理、编排、认证、控制台协议
- 可见性和操作入口都由后端持有

### Runtime

- policy、topology、scheduling、jobs、lease
- topology 是 runtime 内第一等子域

### Extensions

- connectors、triggers、workflows、runner contracts
- 通过合同与 runtime / control plane 对接

### Platform

- db、redis、logging、telemetry、security 等基础设施

## Runtime Surface

- 正式公开 runtime profile 只有 `gateway-kernel`
- 可选能力通过显式 pack keys 打开
- `gateway-iot` 仅作为 `iot-pack` 的镜像目标存在，不是产品 surface

## Related Documents

- [Profile / Pack Matrix](profile-matrix.md)
- [Control Plane Protocol Matrix](protocol-matrix.md)
- [ADR 0024](adr/0024-gateway-kernel-default-and-backend-driven-control-plane.md)
- [ADR 0035](adr/0035-pack-registry-and-kernel-pack-boundary.md)
- [ADR 0046](adr/0046-kernel-only-runtime-surface-and-compatibility-retirement.md)
