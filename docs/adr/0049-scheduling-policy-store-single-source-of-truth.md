# 0049. 调度策略存储 (PolicyStore) 作为唯一配置消费入口

- 状态: 已采纳
- 日期: 2026-04-04

## 1. 背景上下文

在 v3.41 及之前，`executor_registry.py`、`placement_policy.py`、`queue_stratification.py`、`quota_aware_scheduling.py` 四个调度核心模块各自通过 `Path("system.yaml").read_text()` + `yaml.safe_load()` 读取配置。这带来三个问题：

1. **散落的解析逻辑**：每个模块独立处理 YAML 读取、错误处理和默认值填充，容易出现一处改配置格式而其他模块未同步的情况。
2. **无版本化与审计**：配置变更不可追踪，无法回滚到前一版策略。
3. **治理缺口**：无法在运维紧急场景下冻结策略变更，也无法统一验证策略合法性。

## 2. 决策选项

1. **方案 A — 每个模块独立缓存**：保持现状，各模块自行 `yaml.safe_load()`。
2. **方案 B — 统一 PolicyStore 单例**：新建 `scheduling_policy_store.py` 模块，作为所有调度配置的唯一消费入口。
3. **方案 C — 外部配置服务**：引入 etcd/Consul 等外部配置中心。

## 3. 评估对比

### 方案 A（保持现状）
- **优势**：零变更成本
- **劣势**：散落解析无法统一验证；无版本化；重复 YAML 读取性能浪费

### 方案 B（PolicyStore 单例）
- **优势**：唯一消费入口消除散落解析；内置版本化 + 审计日志 + 冻结/回滚；无需外部依赖
- **劣势**：需迁移所有消费方

### 方案 C（外部配置服务）
- **优势**：分布式场景下配置同步
- **劣势**：引入外部依赖；当前单节点架构不需要

## 4. 最终决定

采用 **方案 B**：在 `backend/core/scheduling_policy_store.py` 实现 `PolicyStore` 单例，通过模块级 `get_policy_store()` 提供懒加载访问。所有调度模块通过该入口消费配置，禁止直接解析 `system.yaml`。

### PolicyStore 核心 API

| 属性/方法 | 用途 |
|-----------|------|
| `active` | 当前生效的 `SchedulingPolicy` |
| `version` | 当前策略版本号 (int) |
| `frozen` / `freeze_reason` | 治理锁状态 |
| `tenant_quotas_config` | `scheduling.tenant_quotas` 原始配置 |
| `placement_policies_config` | `scheduling.placement_policies` 原始配置 |
| `default_service_class_override` | 顶层默认服务等级 |
| `resource_quotas_config` | `scheduling.resource_quotas` 原始配置 |
| `executor_contracts_config` | `scheduling.executor_contracts` 原始配置 |
| `apply(new_policy, operator, reason)` | 验证、差异对比、版本递增、审计记录 |
| `rollback(target_version, operator, reason)` | 从历史中恢复指定版本 |
| `freeze(reason)` / `unfreeze(operator)` | 冻结/解冻策略变更 |
| `snapshot()` | 完整诊断快照（用于管理 API） |

### 消费方迁移

| 模块 | 旧方式 | 新方式 |
|------|--------|--------|
| `executor_registry.py` | `yaml.safe_load(Path("system.yaml"))` | `get_policy_store().executor_contracts_config` |
| `placement_policy.py` | `yaml.safe_load(Path("system.yaml"))` | `get_policy_store().placement_policies_config` |
| `queue_stratification.py` | `yaml.safe_load(Path("system.yaml"))` | `get_policy_store().tenant_quotas_config` |
| `quota_aware_scheduling.py` | `yaml.safe_load(Path("system.yaml"))` | `get_policy_store().resource_quotas_config` |

### 生命周期

1. **启动**：`get_policy_store()` 首次调用时 → `PolicyStore()` + `load_from_yaml()` → 缓存 5 个 scheduling 配置段 + 验证策略
2. **运行时**：管理员可通过 `apply()` 热更新策略 → 版本递增 + 差异记录 + 审计日志
3. **紧急**：`freeze()` 冻结所有变更 → 所有 `apply()` / `rollback()` 被阻断
4. **回滚**：`rollback(target_version)` → 从 `deque[PolicyVersion]`（最多 200 条历史）中恢复

## 5. 影响范围

正面影响：

- 非测试后端代码中零 `Path("system.yaml").read_text()` 调用，消除散落的配置解析。
- 策略变更可追踪、可回滚、可冻结，满足运营治理需求。
- 所有 raw 配置通过 `dict.copy()` 返回，防止消费方意外修改全局状态。

成本：

- 消费方需从直接 YAML 读取迁移到 `get_policy_store()` 调用。
- PolicyStore 内存占用（历史 deque 最多 200 条 + 审计日志最多 200 条）。

## 落地

- `backend/core/scheduling_policy_store.py` — PolicyStore 实现
- `backend/core/executor_registry.py` — 消费方迁移
- `backend/core/placement_policy.py` — 消费方迁移
- `backend/core/queue_stratification.py` — 消费方迁移
- `backend/core/quota_aware_scheduling.py` — 消费方迁移
- `backend/tests/unit/test_tenant_fair_share.py` — 测试覆盖
