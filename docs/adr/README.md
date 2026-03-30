# ADR 索引

## 已采纳

- [0001](0001-gateway-first-kernel-foundation.md) Gateway First Kernel Foundation
- [0002](0002-compose-profile-capability-alignment.md) Compose Profile Capability Alignment
- [0003](0003-control-plane-surface-minimization.md) Control Plane Surface Minimization
- [0004](0004-backend-driven-console.md) Backend Driven Console
- [0005](0005-go-runner-agent-boundary.md) Go Runner Agent Boundary
- [0006](0006-native-health-client-placeholder.md) Native Health Client Placeholder
- [0007](0007-pack-boundary-and-non-default-business-surfaces.md) Pack Boundary and Non-Default Business Surfaces
- [0008](0008-iac-single-source-of-truth.md) IaC Single Source of Truth
- [0009](0009-runtime-contract-verification.md) Runtime Contract Verification
- [0010](0010-console-capability-closure.md) Console Capability Closure
- [0011](0011-iac-core-modularization-and-render-manifest.md) IaC Core Modularization and Render Manifest
- [0012](0012-service-tiering-multinode-topology.md) Service Tiering Multinode Topology
- [0013](0013-redis-spof-mitigation-and-state-decoupling.md) Redis SPOF Mitigation and State Decoupling
- [0014](0014-unified-transaction-and-tenant-isolation-enforcements.md) Unified Transaction and Tenant Isolation Enforcements
- [0015](0015-unified-http-client-interceptor-enforcement.md) Unified HTTP Client Interceptor Enforcement
- [0016](0016-sse-client-token-heartbeat-timeout.md) SSE Client Token Heartbeat Timeout
- [0017](0017-audit-technical-debt-governance.md) Audit Technical Debt Governance
- [0018](0018-zero-downtime-deployment.md) Zero-Downtime Deployment
- [0019](0019-mypy-type-ignore-comments-policy.md) mypy Type Ignore Comments Policy
- [0020](0020-auth-audit-stream.md) Auth Audit Stream
- [0021](0021-frontend-api-path-constant-registry.md) Frontend API Path Constant Registry
- [0022](0022-tiered-release-smoke-gate.md) Tiered Release Smoke Gate
- [0024](0024-gateway-kernel-default-and-backend-driven-control-plane.md) Gateway Kernel Default and Backend-Driven Control Plane
- [0025](0025-control-plane-node-job-contract-hardening.md) 控制面 Node / Job 合同加固
- [0026](0026-control-plane-worker-process-isolation.md) 控制面 Worker 进程隔离
- [0027](0027-node-machine-token-authentication.md) 节点机器令牌认证
- [0028](0028-scheduler-attempt-history-and-operational-overview.md) 调度 Attempt 历史与运营总览
- [0029](0029-scheduler-capacity-drain-and-lease-governance.md) 调度容量、Drain 与 Lease 治理
- [0030](0030-backend-driven-resource-forms-and-action-dialogs.md) 后端驱动资源表单与动作弹层
- [0031](0031-dashboard-route-intents-and-filtered-operations-views.md) Dashboard 路由意图与过滤运营视图
- [0032](0032-backend-owned-resource-chrome-and-node-bootstrap-receipts.md) 后端拥有资源页头与节点回执
- [0033](0033-backend-owned-status-semantics-and-tone-contracts.md) 后端拥有状态语义与 Tone 合同
- [0034](0034-server-owned-resource-list-filters.md) 服务端拥有资源列表过滤
- [0035](0035-pack-registry-and-kernel-pack-boundary.md) Pack 注册表与 Kernel / Pack 边界
- [0036](0036-kernel-iac-explicit-runtime-guards-and-windows-safe-compiler-writeback.md) Kernel IaC 显式运行守卫与 Windows 安全写回
- [0037](0037-phase5-native-client-contracts-and-resource-aware-dispatch.md) 第五阶段原生客户端合同与资源感知调度
- [0038](0038-control-plane-tenant-boundary-machine-channel-hardening-and-reproducible-offline-bundles.md) 控制面租户边界、机器通道加固与可复现离线包
- [0039](0039-tenant-scoped-admin-idempotency-and-reproducible-release-inputs.md) 租户作用域管理、幂等键与可复现发布输入
- [0040](0040-preauth-tenant-contract-tmp-compile-secret-governance-and-immutable-workflows.md) 登录前租户合同、临时编译产物治理与不可变工作流
- [0041](0041-rls-startup-attestation-external-acl-state-and-digest-pinned-images.md) RLS 启动证明、外置 ACL 状态与 digest 固定镜像
- [0042](0042-encrypted-backup-push-contract-and-admin-role-unification.md) 加密备份、Push 合同与管理权限统一
- [0043](0043-runner-https-default-immutable-offline-release-and-locked-ci-dependencies.md) Runner HTTPS 默认值、不可变离线发行与锁定式 CI 依赖
- [0044](0044-disabled-account-auth-gates-internal-machine-tls-and-clean-release-bundles.md) 停用账号登录阻断、内置机器 TLS 与纯净发布包
- [0045](0045-http-entry-redirect-push-tenant-scope-global-settings-superadmin-and-update-branch-tracking.md) HTTP 入口重定向、Push 租户作用域、全局设置超管化与更新分支跟踪
- [0046](0046-kernel-only-runtime-surface-and-compatibility-retirement.md) Kernel 唯一运行时 Surface 与兼容层退场
- [0047](0047-control-plane-ui-and-runner-regression-density.md) 控制台与 Runner 直接回归密度补齐
- [0048](0048-health-pack-mvp-skeleton-and-pack-maturity-contract.md) Health Pack MVP Skeleton 与 Pack 成熟度合同

## 待定

- [0023](0023-v4-architecture-evolution-roadmap.md) V4 Architecture Evolution Roadmap

## 模板

- [0000](0000-adr-template.md) ADR Template

## 规则

1. 新 ADR 必须基于 [0000](0000-adr-template.md) 创建，并使用 `NNNN-short-title.md` 命名。
2. 涉及协议、部署边界、控制面安全、租户隔离、供应链不可变性的变更，必须同时更新 ADR 与本索引。
3. ADR 正文以中文为主，必要时保留英文专有名词，但不能只留下聊天口头约定。
