# ADR 0021: Frontend API Path Constant Registry (Single Source of Truth)

- **状态**: 已接受 (Accepted 2026-03-24) — _部分废弃 (v3.43: MEDIA / ASSETS / SCHEDULER / SCENES / ENERGY 域已下架)_
- **前置依赖**: ADR 0009 (Contracts-driven Types), ADR 0010 (Unified Envelope), ADR 0015 (HTTP Client Enforcement)

## 1. 背景上下文

在 ADR 0015 中已确立"所有业务 API 调用必须通过全局 `http` 实例"的红线。但审计发现，前端代码在遵守 `http` 实例的前提下，**API 路径仍以裸字符串硬编码散落在 20+ 个 Vue/TS 文件中**：

```typescript
// 典型违规模式 — 散落在各 store/view 中
const { data } = await http.get("/v1/scheduler/jobs");
await http.post(`/v1/scenes/${id}/execute`);
await http.delete(`/v1/auth/credentials/${credId}`);
```

此模式导致以下架构风险：

1. **路径漂移 (Path Drift)**：后端修改 REST 挂载路径后，前端需逐文件搜索替换，遗漏即产生 404 事故。
2. **契约不可验**：无法通过 CI 自动验证前端消费的 API 路径是否与 OpenAPI spec 一致。
3. **重复字符串 (DRY 违反)**：同一路径在多个 store/view 中重复出现，修改一处忘改另一处。
4. **审计困难**：无法一眼看清前端依赖了后端哪些端点。

## 2. 决策选项

1. **方案 A**: 从 OpenAPI 自动生成 TS 客户端（如 `openapi-typescript-codegen`）
2. **方案 B**: 手工维护中央 API 常量注册表 (`utils/api.ts`)
3. **方案 C**: 保持现状，靠 Code Review 人工把关

### 方案 A
- **优势**: 全自动、零漂移、类型安全
- **劣势**: 需要工具链维护成本；生成代码与手写 Axios 拦截器（envelope 解包、熔断器）集成有摩擦

### 方案 B
- **优势**: 零依赖、与现有 Axios 拦截器零摩擦、可即时落地、审计清晰
- **劣势**: 需手工同步维护（可通过 CI 门禁弥补）

### 方案 C
- **优势**: 零改动
- **劣势**: 无法保证一致性，高风险

## 3. 评估对比

方案 A 为终极目标（配合 ADR 0009 Contracts），但当前工具链尚未成熟。方案 B 是**即可落地的中间策略**，且与方案 A 不冲突——未来 codegen 可直接生成 `api.ts` 内容替代手工维护。

## 4. 最终决定

**采纳方案 B**：在 `frontend/src/utils/api.ts` 建立统一 API 路径常量注册表，作为前端 API 路径的**唯一事实源**。

### 4.1 注册表结构

按业务域分组，每个域导出 `as const` 对象：

```typescript
export const SCHEDULER = {
  jobs: "/v1/scheduler/jobs",
  trigger: (id: number | string) => `/v1/scheduler/jobs/${id}/trigger`,
  update: (id: number | string) => `/v1/scheduler/jobs/${id}`,
} as const;
```

### 4.2 路径拼接契约

```
http.ts baseURL = "/api"  (来自 VITE_API_BASE_URL 或默认值)
api.ts constant  = "/v1/scheduler/jobs"
─────────────────────────────────────────
Axios 最终 URL   = "/api" + "/v1/scheduler/jobs" = "/api/v1/scheduler/jobs"
后端 router      = APIRouter(prefix="/api/v1/scheduler") + @router.get("/jobs")
```

### 4.3 强制红线

- **禁止**：业务代码（`views/`, `components/`, `stores/`）中出现 `/v1/` 硬编码字符串。
- **允许**：`api.ts` 内部定义路径、`http.ts` 内部拦截器逻辑判断、`sse.ts` SSE 白名单。
- **CI 门禁**（后续 CI-GUARDRAIL-006）：Lint 规则阻止非白名单文件出现 `/v1/` 字符串。

### 4.4 已覆盖域

| 域名 | 常量命名空间 | 端点数 |
|:---|:---|:---:|
| Auth | `AUTH` | 12 |
| ~~Board~~ | ~~`BOARD`~~ | ~~3~~ — _下架 v3.43_ |
| ~~Media~~ | ~~`MEDIA`~~ | ~~3~~ — _下架 v3.43_ |
| ~~Assets / Gallery~~ | ~~`ASSETS`~~ | ~~5~~ — _下架 v3.43_ |
| Settings | `SETTINGS` | 11 |
| Nodes | `NODES` | 8 |
| Jobs | `JOBS` | 11 |
| Connectors | `CONNECTORS` | 5 |
| Switches / IoT | `SWITCHES`, `IOT` | 3 |
| Agent / Memory | `AGENT` | 7 |
| ~~Scheduler~~ | ~~`SCHEDULER`~~ | ~~3~~ — _下架 v3.43_ |
| ~~Scenes~~ | ~~`SCENES`~~ | ~~4~~ — _下架 v3.43_ |
| ~~Energy~~ | ~~`ENERGY`~~ | ~~3~~ — _下架 v3.43_ |
| System | `SYSTEM` | 1 |
| Observability | `OBS` | 3 |
| Portability | `PORTABILITY` | 2 |
| SSE | `SSE` | 2 |

## 5. 影响范围

- **已完成迁移的消费者**（本次工单 FE-API-CONTRACT-003）：
  - `Login.vue`, `AgentConsole.vue`, `VoiceButton.vue`, `UserManagementCard.vue`, `InviteView.vue`, `FamilyGallery.vue`
  - `stores/auth.ts`, `stores/switch.ts`, `stores/scheduler.ts`, `stores/scenes.ts`, `stores/energy.ts`, `stores/capabilities.ts`
  - `utils/sse.ts`
- **CI 影响**：后续需增加 Lint 规则阻断路径裸字符串回潮（工单 CI-GUARDRAIL-006）。
- **向 ADR 0009 (Contracts) 收敛**：未来 OpenAPI codegen 可直接替换 `api.ts` 中的手写常量，实现完全自动化。
- **无破坏性变更**：路径值未改变，仅将散落的字符串收口至常量引用，运行时行为完全等价。
