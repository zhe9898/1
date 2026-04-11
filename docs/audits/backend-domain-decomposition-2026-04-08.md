# ZEN70 Backend 五域拆分蓝图（2026-04-08）

- `blueprint_id`: `AUDIT-BACKEND-DOMAINS-2026-04-08`
- `status`: `code-backed-target-architecture`
- `scope`: `backend/`
- `code_spec`: `backend/kernel/governance/domain_blueprint.py`
- `import_fence_spec`: `backend/kernel/governance/domain_import_fence.py`

## 官方目标

仓库当前唯一官方后端拓扑是五域：

```text
backend/
  kernel/
    capabilities/
    surfaces/
    packs/
    profiles/
    policy/
    governance/
    contracts/

  control_plane/
    app/
    auth/
    console/
    admin/
    adapters/

  runtime/
    topology/
    scheduling/
    execution/

  extensions/

  platform/
    db/
    redis/
    http/
    logging/
    telemetry/
    security/
```

这不是“未来想法”，而是当前代码与门禁共同背书的目标结构。官方真相由以下入口维持：

- `backend/kernel/governance/domain_blueprint.py`
- `backend/kernel/governance/domain_import_fence.py`
- `tools/backend_domain_fence.py`
- `backend/tests/unit/test_architecture_governance_gates.py`
- `tests/test_repo_hardening.py`

## 已完成拆分

- `backend/core/control_plane.py` 已拆为：
  - `backend/kernel/surfaces/contracts.py`
  - `backend/kernel/surfaces/registry.py`
  - `backend/control_plane/console/manifest_service.py`
- `backend/core/kernel_capabilities.py` 已迁移到 `backend/kernel/capabilities/registry.py`
- `backend/core/pack_registry.py` 已拆为：
  - `backend/kernel/packs/registry.py`
  - `backend/kernel/packs/presets.py`
  - `backend/runtime/topology/pack_selection.py`
- `backend/core/gateway_profile.py` 已拆为：
  - `backend/kernel/profiles/public_profile.py`
  - `backend/runtime/topology/profile_selection.py`
- `backend/core/runtime_policy_resolver.py` 已迁移到 `backend/kernel/policy/runtime_policy_resolver.py`
- `backend/core/scheduling_policy_store.py` 已迁移到 `backend/kernel/policy/policy_store.py`
- `backend/core/architecture_governance.py` 已迁移到 `backend/kernel/governance/architecture_rules.py`
- `backend/core/aggregate_owner_registry.py` 已迁移到 `backend/kernel/governance/aggregate_owner_registry.py`
- `backend/kernel/execution/**` 已迁移到 `backend/runtime/execution/**`
- `backend/kernel/scheduling/**` 已迁移到 `backend/runtime/scheduling/**`
- `backend/kernel/topology/**` 已迁移到 `backend/runtime/topology/**`
- `backend/kernel/extensions/**` 已迁移到 `backend/extensions/**`

## 边界原则

- `kernel` 只承载系统真相、合同、注册表、策略与治理，不拥有 HTTP 入口，也不拥有运行时协调。
- `control_plane` 只承载 FastAPI、鉴权、控制台投影与 HTTP 适配，不拥有系统事实源。
- `runtime` 承载真正会动的行为：拓扑、调度、执行、租约与故障隔离。
- `extensions` 承载连接器、触发器、工作流及其安全边界，不反向拥有 kernel 事实。
- `platform` 只提供数据库、Redis、日志、遥测与安全底座，不拥有业务事实。

## 门禁原则

- 模块化 = 边界清楚。
- 系统化 = 边界被规则、测试、门禁持续维持。
- 五域目录本身不是门禁；`domain_import_fence` 扫描器和双层测试才是门禁。
- 如果文档与代码背书冲突，以代码背书和测试门禁为准，文档必须跟随修正。

## 当前下一步

- 继续推进 `module-catalog.yaml` 中的 `M2` 与 `M8`。
- 保持新的 import fence allowlist 最小化，新增例外必须先补治理说明和测试。
- 审计台账通过 `findings-ledger.yaml` 生命周期状态维护，不再让历史发现与当前实现混写。
