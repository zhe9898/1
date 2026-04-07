# ZEN70 扩展性指南 (Extensibility)

> 本架构为 **后端驱动、协议闭环**（无独立前端产品）。本文档说明如何扩展能力矩阵、协议消费端、后端、IaC 与文档，便于后续迭代时按同一套路扩展。

---

## 当前架构扩展性评估（结论先行）

| 维度 | 扩展性 | 说明 |
|------|--------|------|
| **能力矩阵 / 协议闭环** | 较好 | 后端 `backend/capabilities.py` 中的 `build_matrix()` / `get_capabilities_matrix()` 集中维护，消费端按契约 v-for 渲染；**新增能力 = 在后端矩阵里加一项**即可，契约测试自动覆盖。不足：能力项仍集中在单模块，若条目暴增可继续按域拆分。 |
| **后端 API / 领域** | 好 | 按领域拆成独立 router（auth、settings、nodes、jobs、connectors、assets、media、iot、board 等），由 `backend/api/main.py` 挂载；**新领域 = 新 `api/xxx.py` + 一行 include**。统一 deps、ErrorResponse、Pydantic v2，风格一致。 |
| **协议消费端（如仓库内 Vue）** | 好 | 无独立前端产品；消费端仅消费协议。路由与 views 按契约组织；`types/` 与后端 API 契约对齐；**新视图 = 新组件 + 路由 + 可选 store**，SSE 统一订阅，新事件类型加分支即可。 |
| **配置与 IaC** | 好 | `system.yaml` 为唯一事实来源，compiler 生成 .env/compose；**新配置 = 新 YAML 节点 + compiler 中解析**。密钥占位、路径 pathlib 已规范，扩展新服务/新能力配置有清晰套路。 |
| **事件与实时** | 较好 | Redis Pub/Sub + SSE 单一事件流；当前活跃通道 `hardware:events` / `switch:events` / `node:events`、`job:events`、`connector:events`。新 channel 需在网关订阅并转发、消费端 `createSSE` 加类型分支。 |
| **整体结构** | 中等偏上 | 单进程网关（`backend/api/main.py`）承载能力矩阵、SSE、路由聚合，**无插件/注册表机制**；能力项与核心逻辑仍在主模块。适合「按领域加 router、按能力加矩阵项、按配置加 YAML」的线性扩展，不适合需要第三方无侵入插件的场景。 |

**一句话**：**后端驱动、协议闭环**，线性扩展以后端能力矩阵与 API 为主；消费端仅随契约扩展视图即可。能力矩阵若未来条目很多，可再拆成「按域组装的矩阵工厂」以提升可维护性。

---

## 多语言余地（Go / TypeScript / Python）

本架构 **契约优先**，与实现语言解耦，**留有向 Go、TypeScript、Python 迁移或混用的余地**：

| 层级 | 当前实现 | 可替换为 | 契约依据 |
|------|----------|----------|----------|
| **网关** | Python (FastAPI) | Go / 其它 | OpenAPI、REST `/api/v1/*`、SSE 流与心跳、ZEN-xxx 错误码、Redis/Postgres 使用方式 |
| **协议消费端** | TypeScript (Vue 3) | 任意 TS/JS 框架、Go 后端渲染、Flutter 等 | OpenAPI、SSE 事件类型与 payload、JWT/X-New-Token |
| **探针 / Worker** | Python | Go / 其它 | Redis 通道与 payload（`events_schema`）、topology 键、锁键 |

- **迁移时**：以 `docs/openapi.json` 与 `backend/core/events_schema.py`（及 Redis 键/频道约定）为单一事实来源，新实现满足契约即可替换，无需改其它层。
- **混用**：例如网关用 Go、探针用 Python、消费端用 TS，只要均遵守同一契约即可共存。

---

## 1. 协议驱动 UI 扩展（新增能力/控制项）

- **契约**：`/api/v1/capabilities` 由后端返回 `Dict[str, CapabilityItem]`，协议消费端用 **v-for** 渲染，禁止在消费端硬编码面板能力列表。
- **扩展步骤**：
  1. **后端**：在 `backend/capabilities.py` 的 `build_matrix()` 中增加新键（如 `"新服务名"`），赋值为 `CapabilityItem(status=..., enabled=..., endpoint=..., reason=...)`。若依赖 Redis/探针，从现有 `topology`、feature flags 或 Redis 状态读入。
  2. **契约测试**：`tests/test_contract_capabilities.py` 已断言「每项含 status/enabled、可选 endpoint/models/reason」，新增键会自动被覆盖。
  3. **协议消费端**：若已按契约 v-for 遍历 capabilities，**无需改组件**即可出现新卡片；若需新交互（如专用表单），在消费端 capabilities store 与对应视图里按现有模式加逻辑。
  4. **控制面事件**：若该能力需要实时状态闭环，补对应 REST router，并把事件 channel 接到 `backend/api/routes.py` 的 SSE 订阅列表与 `frontend/src/utils/sse.ts` 的事件类型分支。
- **注意**：能力键建议用**能力语义**（如 `media`、`backup`），禁止写死硬件型号；开关状态由探针/Redis 驱动，不在消费端写死。

**可选 Local LLM Agent**（见 ADR 0008）：当 `capabilities.agent.enabled` 在 system.yaml 中为 true 时，compiler 注入 `ZEN70_AGENT_ENABLED=true`，能力矩阵中会出现「Local LLM Agent」项（仅 admin/geek 可见）。Agent 不直连 Docker，仅通过现有契约：GET `/api/v1/agent/capabilities`、POST `/api/v1/agent/plan`（意图→建议动作）、POST `/api/v1/agent/act`（执行动作并发布 `switch:events`）。白名单由 `SWITCH_CONTAINER_MAP` 决定，新增开关在 `sentinel.switch_container_map` 中配置即可扩展。

**网页可开可关（Feature Flags）**：为避免“敲命令行”启停，可将可选能力注册为 `FeatureFlag`（示例：`local_llm_agent`、`memory_rumination`、`memory_daily_summary`），在消费端的 `系统设置 → 功能开关` 中切换。后端将开关持久化到 PostgreSQL，并写入 Redis 热缓存键 `zen70:ff:<key>` 供网关与 worker 秒级感知，从而实现“功能可选/可关闭”的无感切换。

---

## 2. 协议消费端扩展（新视图、新类型、新 Store）

- **定位**：消费端无业务事实来源，仅消费后端协议；仓库内以 Vue 实现为例。
- **类型**：在 `frontend/src/types/` 下新增 `*.ts`（如 `device.ts`、`user.ts`、`asset.ts`、`media.ts`），与后端 API 契约对齐；禁止 `any`，用 `unknown` + 类型收窄处理异常。
- **视图/组件**：在 `frontend/src/views/` 或 `components/` 新增组件，路由在 `router/index.ts` 注册；鉴权与 RBAC 视界折叠（长辈/极客）按 JWT 与后端约定渲染。
- **Store**：Pinia store 与 SSE/能力矩阵/开关状态等现有风格一致；请求统一带 `Authorization`、处理 `X-New-Token` 轮转。
- **SSE**：新事件类型由后端在 Redis 发布新 channel、网关 SSE 转发；消费端在 `utils/sse.ts` 或 App.vue 的 `createSSE` 回调里增加 `ev.type === "新事件"` 的分支即可。

---

## 3. 后端 API 扩展

- **路由**：在 FastAPI 下挂 `/api/v1/...`，统一依赖 `get_current_user_optional` 或 `get_current_user`；返回 Pydantic 模型，错误用 `ErrorResponse` 与 ZEN-xxx 错误码。
- **模型**：在对应 `backend/api/*.py` 或 `backend/api/models/` 中定义 Pydantic v2 模型；与消费端 `types/*.ts`（或 OpenAPI）保持字段一致（可参考 OpenAPI 生成或手写）。
- **调度策略**（ADR 0049）：所有调度配置通过 `get_policy_store()` 消费。新增调度模块时，从 PolicyStore 的只读 property（如 `tenant_quotas_config`、`executor_contracts_config`）读取配置，禁止直接解析 system.yaml。策略变更通过 `apply()` / `rollback()` 管理，支持冻结（`freeze()`）和审计日志。
- **Redis/Postgres**：新状态写 Redis 时键名带业务前缀（如 `switch_expected:*`）；新表用 Alembic 迁移，**禁止**业务容器并发执行 migration，须抢 `DB_MIGRATION_LOCK`。
- **事件**：若需新 SSE 事件类型，在网关订阅新 channel 并注入到 SSE 流；当前控制面事件建议沿用 `node:events`、`job:events`、`connector:events` 这类按领域分流的模式。探针或 worker 发布时使用 `backend/core/events_schema.py` 或对应 router 的 schema，保证消费端解析一致。

### 3.1 扩展 Runner 任务类型 (Job Kind)

Runner Agent 的 executor 已支持 8 种任务类型（ADR 0050）。新增 kind 的扩展步骤：

1. **Go 处理器**：在 `runner-agent/internal/exec/executor_extended.go` 中实现 `run<KindName>(ctx, payload) (Result, error)` 处理函数。
2. **Kind 路由**：在 `executor.go` 的 `Run()` 方法中增加 kind → 处理函数映射。
3. **错误分类**：返回 `ExecError{Category: "...", Details: map[string]any{...}}`，Category 必须是已定义的分类之一（timeout / resource_exhausted / invalid_payload / canceled / transient / execution_error / not_found），以便控制面做出正确的重试/隔离决策。
4. **节点声明**：在部署环境中通过 `RUNNER_ACCEPTED_KINDS` 环境变量声明节点支持的 kind（逗号分隔）。
5. **测试**：在 `runner-agent/internal/exec/` 下增加对应测试。

---

## 4. IaC 与配置扩展

- **唯一事实来源**：`system.yaml`；新增能力或服务时在 YAML 中增加节点（如 `capabilities.新模块`、`sentinel.新配置`），**禁止**在代码里写死路径/密钥。
- **编译器**：在 `scripts/compiler.py` 中解析新节点，输出到 `.env` 或生成的 compose/配置片段；密码/Token 仅用 `${ENV_VAR}` 占位，由点火或安装器注入。
- **三层字段解析**（ADR 0051）：`loader.py` 中 `_build_service_entry()` 对 `ulimits`、`oom_score_adj`、`networks` 等字段采用三层解析——**system.yaml 声明优先 → 服务级内置默认 → 全局兜底**。扩展新字段时需遵循同样的三层优先级模式（`svc.get("field")` → 服务名匹配默认 → 安全兜底）。
- **编排**：新服务在 compiler 生成的 compose 中声明；**必须**在 bootstrap/deployer 中使用 `docker compose up -d --remove-orphans`，避免孤儿容器。
- **调度配置消费**（ADR 0049）：所有调度相关配置通过 `get_policy_store()` 单例消费，禁止在非测试代码中直接 `yaml.safe_load(Path("system.yaml"))`。新增调度配置段时需在 `PolicyStore.load_from_yaml()` 中缓存并提供只读 property。
- **Profile**：默认 profile 应保持为 `gateway-kernel`（运行时兼容别名 `gateway` / `gateway-core`），`full` 只在显式扩展场景启用；任何把重业务重新塞回默认 profile 的变更，都应先补 ADR。

---

## 5. 文档与 ADR 扩展

- **架构决策**：任何影响拓扑、技术选型或核心接口的决策，在 `docs/adr/NNNN-标题.md` 留痕，并在 `docs/adr/README.md` 索引；像“默认改为 Gateway Kernel”“新增 Node/Job/Connector 控制面协议”这类默认形态变化，必须单独补 ADR。
- **总索引**：新文档在 `docs/INDEX.md` 中按分类加入表格；架构类指向 `ZEN70_Architecture_V2.md`，合规类指向 `ARCHITECTURE_CHECKPOINTS.md`。
- **本扩展性文档**：后续若新增扩展点（如新总线、新门禁），在本文件增加章节即可。

---

## 6. 后续计划（可自行补充）

- 可在此列出「我后续将……」类规划，便于与架构扩展点对应，例如：
  - [ ] 新增 XXX 能力项并接入 capabilities 矩阵
  - [ ] 新增 YYY 消费端视图与路由
  - [ ] 新增 ZZZ 后端 API 与 Alembic 迁移
  - [ ] 更新 system.yaml 与 compiler 支持新配置

---

**版本**：V2.0 · **合规**：.cursorrules 绝对零度版 · **入口**：[INDEX.md](INDEX.md)
