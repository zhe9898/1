# ZEN70 Backend 五域拆分蓝图（2026-04-08）

- `blueprint_id`: `AUDIT-BACKEND-DOMAINS-2026-04-08`
- `status`: approved-target-architecture
- `scope`: `backend/` 结构重划分
- `code_spec`: `backend/kernel/governance/domain_blueprint.py`

## Batch 2 Completed

- `backend/core/pack_registry.py` has been replaced by `backend/kernel/packs/registry.py`, `backend/kernel/packs/presets.py`, and `backend/kernel/topology/pack_selection.py`.
- `backend/core/gateway_profile.py` has been replaced by `backend/kernel/profiles/public_profile.py` and `backend/kernel/topology/profile_selection.py`.
- Development mode no longer keeps compatibility inputs. The only runtime profile is `gateway-kernel`, and optional capability domains must be enabled through explicit canonical pack keys.
- `scripts/iac_core/profiles.py` now consumes the new kernel/runtime fact sources directly instead of depending on legacy `backend/core` profile or pack modules.

## 本批次已完成

本批次已经完成第一刀真实迁移：

- `backend/core/control_plane.py` 已拆为：
  - `backend/kernel/surfaces/contracts.py`
  - `backend/kernel/surfaces/registry.py`
  - `backend/control_plane/console/surfaces_service.py`
- `backend/core/kernel_capabilities.py` 已迁入：
  - `backend/kernel/capabilities/registry.py`
- `console / profile / capabilities / architecture_governance` 等调用方已切到新源头。

剩余拆分仍按本蓝图继续推进。

## 对外不变量

对外仍然只保留两件事：

1. `gateway-kernel` 是唯一正式 runtime surface。
2. control plane 是 backend-driven 的管理与编排入口。

同时保留三条内部硬约束：

- pack 仍然只是 capability contract 与 runtime boundary，不是新产品层。
- runtime policy 仍然统一经 `PolicyStore + RuntimePolicyResolver` 进入运行时判定。
- 扩展入口仍然是 `capability -> surface -> policy -> service contract -> execution contract`。

这些不变量已经被代码化记录在 [`domain_blueprint.py`](/E:/1.0/1/backend/kernel/governance/domain_blueprint.py)。

## 为什么不能继续养 `backend/core`

现状已经证明 `backend/core` 不是一个健康的长期边界：

- [`backend/core/control_plane.py`](/E:/1.0/1/backend/core/control_plane.py) 同时承担了 surface 合同、surface registry、profile 过滤、policy gate、admin gate。
  - `ControlPlaneSurface` 本身是内核语义。
  - `iter_control_plane_surfaces()` 却又直接调用 `RuntimePolicyResolver` 和 `get_enabled_router_names()` 做运行时准入。
- [`backend/core/pack_registry.py`](/E:/1.0/1/backend/core/pack_registry.py) 同时装了 pack 合同事实、profile alias、preset 兼容输入、router selection、gateway image target 解析。
- [`backend/core/gateway_profile.py`](/E:/1.0/1/backend/core/gateway_profile.py) 既暴露 public profile facts，又继续计算 enabled routers 和 runtime pack resolution。
- [`backend/kernel/governance/architecture_rules.py`](/E:/1.0/1/backend/kernel/governance/architecture_rules.py) 现在已经把 surface traceability、runtime policy single-source、LeaseService single-writer、fault isolation、extension safety、aggregate ownership 写成硬规则，这反而进一步说明“内核治理”应该有独立 home。

结论不是再给 `backend/core` 分类打标签，而是要把它拆掉。

## 正确拆法

目标结构固定为五域：

```text
backend/
  kernel/
    capabilities/
    surfaces/
    packs/
    profiles/
    governance/
    contracts/

  control_plane/
    app/
    auth/
    console/
    admin/
    routers/

  runtime/
    policy/
    topology/
    scheduling/
    jobs/
    lease/

  extensions/
    connectors/
    triggers/
    workflows/
    runner_contracts/

  platform/
    db/
    redis/
    logging/
    telemetry/
    security/
```

这五域和 `M1-M8` 审查模块不是互斥关系：

- `M1-M8` 是审查切片，回答“先审什么”。
- `kernel/control_plane/runtime/extensions/platform` 是代码归属，回答“代码最终住哪”。

## `kernel/` 怎么拆

`kernel/` 只放事实源、注册表、契约、治理规则，不放 HTTP、不做运行时 visibility 判定、不直接读取 `system.yaml` 做运行时决策。

建议结构：

```text
backend/kernel/
  capabilities/
    registry.py
  surfaces/
    contracts.py
    registry.py
  packs/
    registry.py
    presets.py
  profiles/
    public_profile.py
  governance/
    architecture_rules.py
    aggregate_owner_registry.py
    compatibility.py
    fault_isolation_contract.py
    domain_blueprint.py
  contracts/
    permissions.py
    status.py
    errors.py
```

### 最关键的一刀：拆 `backend/core/control_plane.py`

当前文件把“定义有哪些 surface”和“此刻给谁看哪些 surface”耦在一起。应拆成三份：

1. `backend/kernel/surfaces/contracts.py`
   - 只保留 `ControlPlaneSurface` 的语义合同。
2. `backend/kernel/surfaces/registry.py`
   - 只保留 surface 定义、校验、导出。
3. `backend/control_plane/console/surfaces_service.py`
   - 负责按 `profile`、`admin`、`policy` 做可见性过滤。

原因很直接：kernel 负责“what exists”，control plane 负责“what is visible now”。

### `pack_registry.py` 也要拆成“事实”和“消费”

[`backend/core/pack_registry.py`](/E:/1.0/1/backend/core/pack_registry.py) 里的 `PackDefinition`、`PACK_DEFINITIONS`、`delivery_stage`、`deployment_boundary`、`runtime_owner` 都是 pack 合同事实，应该保留在 `kernel/packs/registry.py`。

但这些运行时消费逻辑不该继续留在 registry 里：

- `selected_router_names()`
- `resolve_gateway_image_target()`
- `resolve_pack_keys()`

它们应该下沉到 `runtime/topology/pack_selection.py`。原因不是 pack 不重要，而是它仍然只是合同与运行边界；真正消费 selector hints、路由开放和 image target 的地方，是 runtime topology。

### `gateway_profile.py` 应拆成两半

[`backend/core/gateway_profile.py`](/E:/1.0/1/backend/core/gateway_profile.py) 现在既转发 public profile facts，又在算 enabled routers，这正好踩在 kernel/runtime 分界线上。

建议拆成：

- `backend/kernel/profiles/public_profile.py`
  - `PROFILE_ALIASES`
  - `PUBLIC_PROFILE_SURFACE`
  - public profile normalization
- `backend/kernel/topology/profile_selection.py`
  - runtime pack resolution
  - enabled router calculation
  - topology-level profile selection

这里最重要的是：`topology` 是 runtime 的第一等子域，不是新产品层。

## 当前文件到目标域的关键映射

| 当前文件 | 目标位置 |
| --- | --- |
| `backend/core/kernel_capabilities.py` | `backend/kernel/capabilities/registry.py` |
| `backend/core/control_plane.py` | `backend/kernel/surfaces/contracts.py` + `backend/kernel/surfaces/registry.py` + `backend/control_plane/console/surfaces_service.py` |
| `backend/core/pack_registry.py` | `backend/kernel/packs/registry.py` + `backend/kernel/packs/presets.py` + `backend/kernel/topology/pack_selection.py` |
| `backend/core/gateway_profile.py` | `backend/kernel/profiles/public_profile.py` + `backend/kernel/topology/profile_selection.py` |
| `backend/core/architecture_governance.py` | `backend/kernel/governance/architecture_rules.py` |
| `backend/core/aggregate_owner_registry.py` | `backend/kernel/governance/aggregate_owner_registry.py` |
| `backend/core/runtime_policy_resolver.py` | `backend/kernel/policy/runtime_policy_resolver.py` |
| `backend/core/scheduling_policy_store.py` | `backend/kernel/policy/policy_store.py` |
| `backend/kernel/execution/lease_service.py` | `backend/runtime/lease/lease_service.py` |
| `backend/kernel/execution/job_lifecycle_service.py` | `backend/runtime/jobs/job_lifecycle_service.py` |
| `backend/kernel/scheduling/job_scheduler.py` | `backend/runtime/scheduling/job_scheduler.py` |
| `backend/kernel/extensions/connector_service.py` | `backend/extensions/connectors/service.py` |
| `backend/kernel/extensions/trigger_command_service.py` | `backend/extensions/triggers/command_service.py` |
| `backend/kernel/extensions/workflow_command_service.py` | `backend/extensions/workflows/command_service.py` |
| `backend/core/redis_client.py` | `backend/platform/redis/client.py` |
| `backend/core/structured_logging.py` | `backend/platform/logging/structured.py` |
| `backend/core/telemetry.py` | `backend/platform/telemetry/service.py` |

## 我补充的建议

### 1. 先抽“事实源”，再抽“执行源”

不要一开始同时重写 registry 和 router。优先把纯事实层拿出来：

- capability registry
- surface contracts and registry
- pack registry
- profile facts
- governance registry

这些搬完以后，`control_plane` 和 `runtime` 的过滤逻辑才有清晰的依赖方向。

### 2. 给五域加 import fence，不要只靠约定

建议后续加静态规则，至少保证：

- `kernel` 不能 import `control_plane`、`runtime`、`extensions`
- `runtime` 不能 import `control_plane/routers`
- `extensions` 不能回写 kernel registry
- `platform` 不能拥有业务编排

否则目录拆完还会继续长成新的“大熔炉”。

### 3. topology 要显式拥有四类职责

`runtime/topology` 不只是 placement，它至少该拥有：

- profile -> pack resolution
- pack -> router admission
- selector hints consumption
- image target / deployment target resolution

如果这四件事继续散在 `pack_registry.py`、`gateway_profile.py`、`main.py` 里，topology 就只是名义上的子域。

### 4. platform 只收技术能力，不收业务语义

`db`、`redis`、`logging`、`telemetry`、`security` 可以在 `platform/`。
但 job lifecycle、lease、surface visibility、connector orchestration 这类都不该再借“基础设施”名义回流过去。

### 5. compatibility 只能短命，不能长期保留

如果迁移阶段需要 import bridge，可以短时间用兼容导出，但必须满足两条：

- bridge 只转发，不增行为。
- bridge 要有删除顺序和截止批次，不能形成第二个 `backend/core`。

### 6. 把“能力链”做成统一模板

你定的链路 `capability -> surface -> policy -> service contract -> execution contract` 很对，建议后续每个扩展域都按同一模板建目录和命名。这样 connectors、triggers、workflows、runner contracts 不会再各长各的。

## 这次已落地的实现

本次除了文档，我还把这套蓝图做成了代码规格：

- [`domain_blueprint.py`](/E:/1.0/1/backend/kernel/governance/domain_blueprint.py)
- [`test_backend_domain_blueprint.py`](/E:/1.0/1/backend/tests/unit/test_backend_domain_blueprint.py)

这两个文件至少先把五域边界、外部不变量和关键拆分动作固定了，后面继续动代码时不会再退回“边拆边猜”。
