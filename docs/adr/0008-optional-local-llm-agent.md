# ADR 0008: 可选 Local LLM Agent（结构化意图 → switch:events）

- Status: Accepted
- Date: 2025-03-18
- Scope: 可选 Local LLM Agent（结构化意图 → switch:events）

> Source of truth: code and tests override ADR text. See ADR 0052 when documentation and implementation diverge.

## 2. 决策选项

1. **方案 A**：独立微服务 Agent，直连 Docker 或 Redis，自行发布事件。  
2. **方案 B**：网关内可选模块，仅通过现有契约（`/api/v1/capabilities`、`switch:events`、POST `/api/v1/switches`）与探针闭环；能力由 `ZEN70_AGENT_ENABLED` 与 `SWITCH_CONTAINER_MAP` 控制，RBAC 仅 admin/geek 可写。  
3. **方案 C**：纯前端逻辑，后端不提供 Agent 接口，仅提供 capabilities/switches API。

## 3. 评估对比

### 方案 A
- **优势**：可独立扩缩。  
- **劣势**：易越权直连 Docker，违背法典 ADR 0006；增加部署与密钥管理复杂度。

### 方案 B
- **优势**：与现有网关/探针/SSE 契约一致；可选开关（env/system.yaml）关闭即无新路由与能力项；白名单与 `SWITCH_CONTAINER_MAP` 一致，新开关在 YAML 中配置即可扩展；可对接现有 ai_router 或本地 LLM 作为扩展点。  
- **劣势**：Agent 逻辑与网关同进程，需注意资源与超时。

### 方案 C
- **优势**：实现简单。  
- **劣势**：无法做服务端意图解析与审计，扩展性差。

## 4. 最终决定

采用 **方案 B**：在网关上提供可选 Agent 模块。

- **可选性**：`ZEN70_AGENT_ENABLED`（由 compiler 从 `capabilities.agent.enabled` 注入 .env）为 false 时，不注册 Agent 写操作语义；GET `/api/v1/agent/capabilities` 仍可返回 `enabled: false`，便于前端条件渲染。  
- **匹配已有功能**：仅通过现有契约控制实体——将“用户/LLM 意图”转为 `switch` + `state`（+ `reason`），经网关发布 `switch:events` 或调用现有 set_switch 语义；动作白名单与 `SWITCH_CONTAINER_MAP`/capabilities 一致。  
- **扩展**：新增开关在 system.yaml `sentinel.switch_container_map` 中配置后，compiler 写入 `SWITCH_CONTAINER_MAP`，Agent 的 `allowed_switches` 与 `/plan`、`/act` 白名单自动扩展，无需改死代码。  
- **接口**：  
  - GET `/api/v1/agent/capabilities`：返回 `enabled` 与 `allowed_switches`。  
  - POST `/api/v1/agent/plan`：意图 → 建议动作（扩展点，可对接 LLM）；未启用返回 503。  
  - POST `/api/v1/agent/act`：执行结构化动作列表，发布 `switch:events`；需 admin/geek 角色，幂等键防重。  
- **能力矩阵**：当 Agent 启用时，在 `GET /api/v1/capabilities` 中增加“Local LLM Agent”项；仅 admin/geek 角色可见（与“容灾备份”“容器启停”一致）。

## 5. 影响范围

- **安全**：写操作需 JWT 且角色为 admin/geek；幂等与 X-Request-ID 与现有规范一致。  
- **部署**：compiler 增加 `zen70_agent_enabled` 输出；system.yaml 增加 `capabilities.agent.enabled`（默认 false）。  
- **前端**：可基于 capabilities 与 GET `/api/v1/agent/capabilities` 做 RBAC 与可选 Agent 入口；执行时调用 POST `/act` 或先 `/plan` 再 `/act`。  
- **探针**：无变更，仍只消费 `switch:events` 与 `switch_expected:*`。
