# Documentation Index

本目录记录当前代码库的实现说明、运行边界和运维文档。

## Source of Truth

文档不是第一事实源。请按以下顺序判断仓库真相：

1. 实现代码与导出的运行时契约
2. 测试、静态门禁与架构治理导出
3. ADR
4. 本目录下的说明文档

建议先读：

- [adr/0052-code-backed-architecture-governance-registry.md](adr/0052-code-backed-architecture-governance-registry.md)
- [adr/README.md](adr/README.md)

## Start Here

- [../README.md](../README.md) 仓库总览
- [adr/README.md](adr/README.md) ADR 索引与当前架构锚点
- [control-plane-phase-roadmap.md](control-plane-phase-roadmap.md) 控制面 / 执行面阶段路线
- [protocol-matrix.md](protocol-matrix.md) 协议矩阵
- [pack-matrix.md](pack-matrix.md) Pack 能力边界与交付阶段
- [profile-matrix.md](profile-matrix.md) Profile surface 与兼容输入

## Architecture and Runtime

- [MULTI_LANGUAGE_LAYERED_ARCHITECTURE.md](MULTI_LANGUAGE_LAYERED_ARCHITECTURE.md) 当前多语言分层边界
- [FULL_CHAIN_IMPLEMENTATION.md](FULL_CHAIN_IMPLEMENTATION.md) 调度与执行闭环实现记录
- [ADVANCED_SCHEDULING_ALGORITHMS.md](ADVANCED_SCHEDULING_ALGORITHMS.md) 调度算法补充说明
- [gateway-identity-architecture-constraints.md](gateway-identity-architecture-constraints.md) 身份与控制面约束
- [node-machine-auth-implementation-matrix.md](node-machine-auth-implementation-matrix.md) 节点机器认证矩阵

## Delivery and Governance

- [kernel-release-checklist.md](kernel-release-checklist.md) Kernel 发版清单
- [repo-hardening-checklist.md](repo-hardening-checklist.md) 仓库硬化检查
- [KERNEL_CLOSURE_CHECKLIST.md](KERNEL_CLOSURE_CHECKLIST.md) Kernel 收口核对
- [COMPLIANCE_CHANGES.md](COMPLIANCE_CHANGES.md) 合规变更记录
- [CHANGELOG.md](CHANGELOG.md) 变更摘要

## Extension and Operations

- [EXTENSIBILITY.md](EXTENSIBILITY.md) 扩展边界、SDK/pack/runner kind 扩展方式
- [FAQ_TROUBLESHOOTING.md](FAQ_TROUBLESHOOTING.md) 常见问题
- [CRITICAL-FIXES.md](CRITICAL-FIXES.md) 历史关键修复记录
- [SCHEDULER_AUDIT_REPORT.md](SCHEDULER_AUDIT_REPORT.md) 调度专项审计记录

## Generated Protocol Artifacts

- [openapi.json](openapi.json) 主 OpenAPI
- [openapi-kernel.json](openapi-kernel.json) Kernel surface OpenAPI
- [api/openapi_locked.json](api/openapi_locked.json) 锁定 OpenAPI 快照

## Maintenance Rules

- 如果文档声称某项能力“已完成”，必须能在代码和测试里找到对应依据。
- 如果文档只描述历史阶段或技术债，必须显式标成历史/兼容/待完成，而不是伪装成当前事实。
- 如果新增正式运行边界，请先补 ADR，再更新本索引。
