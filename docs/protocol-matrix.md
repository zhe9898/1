# 控制面协议矩阵

## Profile

| 项目 | 内容 |
| --- | --- |
| Endpoint | `GET /api/v1/profile` |
| 请求 schema | 无 |
| 响应 schema | `product`、`profile`、`runtime_profile`、`router_names`、`console_route_names`、`capability_keys`、`requested_pack_keys`、`resolved_pack_keys`、`packs[]`、`cluster_enabled`；`packs[]` 额外包含 `pack_key`、`label`、`category`、`description`、`selected`、`inherited`、`services[]`、`router_names[]`、`capability_keys[]`、`selector_hints[]`、`deployment_boundary`、`runtime_owner`、`status_view` |
| 状态机 | 运行时 profile 固定归一到 `gateway-kernel`；legacy profile alias 只作为 preset 展开出 `requested_pack_keys`，再进一步展开为 `resolved_pack_keys` |
| 修订 / 事件模型 | 快照型，客户端主动拉取刷新 |

## Capability

| 项目 | 内容 |
| --- | --- |
| Endpoint | `GET /api/v1/capabilities` |
| 请求 schema | 仅可选认证 |
| 响应 schema | capability key -> `status`、`enabled`、`endpoint`、`models`、`reason` |
| 状态机 | 只有 profile + 后端 router + 前端 route + menu 同时存在时才从隐藏变为暴露 |
| 修订 / 事件模型 | 快照型，客户端主动拉取刷新 |

## Console Menu

| 项目 | 内容 |
| --- | --- |
| Endpoint | `GET /api/v1/console/menu` |
| 请求 schema | 仅可选认证 |
| 响应 schema | `product`、`profile`、`runtime_profile`、`items[]`，其中包含 `route_name`、`route_path`、`label`、`endpoint`、`enabled`、`requires_admin`、`reason` |
| 状态机 | surface 只有在注册表 gate 通过后才从隐藏变为可见 |
| 修订 / 事件模型 | 快照型，客户端主动拉取刷新 |

## Console Overview

| 项目 | 内容 |
| --- | --- |
| Endpoint | `GET /api/v1/console/overview` |
| 请求 schema | 仅可选认证 |
| 响应 schema | `product`、`profile`、`runtime_profile`、`nodes`、`jobs`、`connectors`、`summary_cards[]`、`attention[]`、`generated_at`；summary cards 额外包含 `tone_view`；attention 项额外包含 `severity_view`；cards 和 attention 项都包含后端拥有的 route/filter target |
| 状态机 | 运行时聚合：节点健康 + 任务 backlog + 连接器健康 -> summary cards + attention queue |
| 修订 / 事件模型 | 快照型，可由客户端拉取或手工刷新；dashboard drill-down route 是后端合同的一部分 |

## Console Diagnostics

| 项目 | 内容 |
| --- | --- |
| Endpoint | `GET /api/v1/console/diagnostics` |
| 请求 schema | 仅可选认证 |
| 响应 schema | `product`、`profile`、`runtime_profile`、`node_health[]`、`connector_health[]`、`stale_jobs[]`、`unschedulable_jobs[]`、`backlog_by_zone[]`、`backlog_by_capability[]`、`backlog_by_executor[]`、`generated_at`；`node_health[]` 额外包含 `node_type`、`executor`、`os`、`arch`、`zone`、`cpu_cores`、`memory_mb`、`gpu_vram_mb`、`storage_mb`；node/connector/stale diagnostics 额外包含 `status_view`、`drain_status_view`、`heartbeat_state_view`、`capacity_state_view`、`lease_state_view` 等后端拥有的展示合同；diagnostics 包含后端拥有的 route/filter target，且在适用时包含推荐 `actions[]` |
| 状态机 | 运行时聚合：节点可靠性 + lease 过期 + 不可调度 backlog -> 运维诊断面板 |
| 修订 / 事件模型 | 快照型，可由客户端拉取或手工刷新；diagnostics 会钻取到带过滤条件的 fleet/job/connector 视图，并暴露后端签发的推荐动作 |

## Pack

| 项目 | 内容 |
| --- | --- |
| Endpoint | `GET /api/v1/profile`、`GET /api/v1/settings/schema`、`render-manifest.json` |
| 请求 schema | `profile` 为 kernel 身份；pack 选择来自 `deployment.packs` 或 legacy preset |
| 响应 schema | `/api/v1/profile` 返回 pack 合同快照；`/api/v1/settings/schema` 在 `profile` section 暴露 `requested_packs`、`resolved_packs`、`available_packs`；`render-manifest.json` 记录 `requested_packs`、`resolved_packs` |
| 状态机 | `deployment.profile` 只决定控制面身份，`deployment.packs` 决定可选业务边界；~~`full-pack` 作为 bundle 展开为 `iot/ops/health/vector`~~ **v3.43 已下架** |
| 修订 / 事件模型 | 快照型；dashboard 和 settings 都消费同一份 pack 合同，不再靠前端硬编码或文档记忆 |

## Node

| 项目 | 内容 |
| --- | --- |
| Endpoint | `GET /api/v1/nodes/schema`、`POST /api/v1/nodes`、`POST /api/v1/nodes/{id}/token`、`POST /api/v1/nodes/{id}/revoke`、`POST /api/v1/nodes/{id}/drain`、`POST /api/v1/nodes/{id}/undrain`、`POST /api/v1/nodes/register`、`POST /api/v1/nodes/heartbeat`、`GET /api/v1/nodes`、`GET /api/v1/nodes/{id}` |
| 请求 schema | schema: 无；list query: `node_id`、`node_type`、`executor`、`os`、`zone`、`enrollment_status`、`drain_status`、`heartbeat_state`、`capacity_state`、`attention`；provision: 仅管理员可提交节点合同并获取一次性 token；rotate: 仅管理员、无请求体；revoke: 仅管理员、无请求体；drain/undrain: 仅管理员，可选 `reason`；register: `Authorization: Bearer <node_token>` + `node_id`、`name`、`node_type`、`address`、`profile`、`executor`、`os`、`arch`、`zone`、`protocol_version`、`lease_version`、`capabilities`、`metadata`、`agent_version`、`max_concurrency`、`cpu_cores`、`memory_mb`、`gpu_vram_mb`、`storage_mb`；heartbeat: 同机器鉴权合同，另带 `status` 和可选 `health_reason` |
| 响应 schema | schema: `product`、`profile`、`runtime_profile`、`resource`、`title`、`description`、`empty_state`、`policies`、`submit_action`、`sections[]`；`policies.list_query_filters` 明确服务端支持的列表过滤合同；node snapshot: `node_id`、`name`、`node_type`、`address`、`profile`、`executor`、`os`、`arch`、`zone`、`protocol_version`、`lease_version`、`enrollment_status`、`enrollment_status_view`、`status`、`status_view`、`capabilities`、`metadata`、`agent_version`、`max_concurrency`、`active_lease_count`、`cpu_cores`、`memory_mb`、`gpu_vram_mb`、`storage_mb`、`drain_status`、`drain_status_view`、`health_reason`、`heartbeat_state`、`heartbeat_state_view`、`capacity_state`、`capacity_state_view`、`attention_reason`、`actions[]`、`registered_at`、`last_seen_at`；provision/rotate 还返回一次性 `node_token`、`auth_token_version`、后端签发的 `bootstrap_commands`、`bootstrap_notes` 与 `bootstrap_receipts[]` |
| 状态机 | 管理员 provision 或 rotate -> `pending` enrollment -> 机器鉴权 register/heartbeat -> `active`；管理员可在 `active` 与 `draining` 间切换；revoke 会清空 token hash，并强制进入 `revoked/offline` |
| 修订 / 事件模型 | SSE `node:events` 包含 `registered`、`updated`、`heartbeat`、`drain`、`undrain`；运维 UI 的发证、舰队动作、dashboard drill-down filter 都来自后端合同 |

## Job

| 项目 | 内容 |
| --- | --- |
| Endpoint | `GET /api/v1/jobs/schema`、`POST /api/v1/jobs`、`POST /api/v1/jobs/pull`、`POST /api/v1/jobs/{id}/progress`、`POST /api/v1/jobs/{id}/renew`、`POST /api/v1/jobs/{id}/result`、`POST /api/v1/jobs/{id}/fail`、`POST /api/v1/jobs/{id}/cancel`、`POST /api/v1/jobs/{id}/retry`、`GET /api/v1/jobs`、`GET /api/v1/jobs/{id}`、`GET /api/v1/jobs/{id}/attempts`、`GET /api/v1/jobs/{id}/explain` |
| 请求 schema | list query: `job_id`、`status`、`lease_state`、`priority_bucket`、`target_executor`、`target_zone`、`required_capability`；create: `kind`、`payload`、`connector_id`、`lease_seconds`、可选 `idempotency_key`、`priority`、`target_os`、`target_arch`、`target_executor`、`required_capabilities`、`target_zone`、`required_cpu_cores`、`required_memory_mb`、`required_gpu_vram_mb`、`required_storage_mb`、`timeout_seconds`、`max_retries`、`estimated_duration_s`、`source`；pull/progress/renew/result/fail: 机器鉴权 `Authorization: Bearer <node_token>` 且 body 必带 `node_id`；pull: `limit`、`accepted_kinds`；progress: `lease_token`、`attempt`、`progress`、可选 `message`；renew: `lease_token`、`attempt`、`extend_seconds`；result: `lease_token`、`attempt`、`result`、`log`；fail: `lease_token`、`attempt`、`error`、`log`；cancel/retry: 仅管理员，可选 `reason`；explain: 管理员/用户只读 |
| 响应 schema | schema: `product`、`profile`、`runtime_profile`、`resource`、`title`、`description`、`empty_state`、`policies`、`submit_action`、`sections[]`；`policies.list_query_filters` 明确服务端支持的列表过滤合同；create/list/get: `job_id`、`kind`、`status`、`status_view`、`node_id`、`connector_id`、`idempotency_key`、`priority`、selector 字段、资源选择器字段、retry 字段、`attempt`、`payload`、`result`、`error_message`、`lease_seconds`、`leased_until`、`lease_state`、`lease_state_view`、`attention_reason`、`actions[]`、`created_at`、`started_at`、`completed_at`；pull 额外返回 `lease_token`；attempts 返回 `attempt_id`、`attempt_no`、`node_id`、`lease_token`、`status`、`status_view`、`score`、`error_message`、`result_summary`、时间戳；explain 返回 `job` 与逐节点 `eligible`、`eligibility_view`、`blockers`、`score`、`active_lease_count`、`max_concurrency`、`executor`、`os`、`arch`、`zone`、`cpu_cores`、`memory_mb`、`gpu_vram_mb`、`storage_mb`、`status_view`、`drain_status_view`、可靠性摘要 |
| 状态机 | `pending` -> `leased`（服务端分配 `attempt + lease_token`）-> `completed` / `failed` / `canceled`；lease 内允许继续上报 `progress` 并 renew `leased_until`；失败且预算未耗尽时回到 `pending` 并增加 `retry_count`；管理员 retry 会把终态任务重新打回 `pending`；只有携带正确 token 且匹配 `node_id + attempt + lease_token` 的 `active` 节点才能拥有 lease 回调权 |
| 修订 / 事件模型 | SSE `job:events` 包含 `created`、`leased`、`progress`、`renewed`、`completed`、`failed`、`requeued`、`canceled`、`manual-retry`；每次 lease 都会落 attempt 历史，dashboard diagnostics 可直接钻取到带过滤条件的任务运营视图 |

## Connector

| 项目 | 内容 |
| --- | --- |
| Endpoint | `GET /api/v1/connectors/schema`、`POST /api/v1/connectors`、`GET /api/v1/connectors`、`POST /api/v1/connectors/{id}/test`、`POST /api/v1/connectors/{id}/invoke` |
| 请求 schema | schema: 无；list query: `connector_id`、`status`、`attention`；upsert: `connector_id`、`name`、`kind`、`status`、`endpoint`、`profile`、`config`；test: `timeout_ms`；invoke: `action`、`payload`、`lease_seconds` |
| 响应 schema | schema: `product`、`profile`、`runtime_profile`、`resource`、`title`、`description`、`empty_state`、`policies`、`submit_action`、`sections[]`；`policies.list_query_filters` 明确服务端支持的列表过滤合同；connector: `connector_id`、`name`、`kind`、`status`、`status_view`、`endpoint`、`profile`、`config`、`last_test_ok`、`last_test_status`、`last_test_message`、`last_test_at`、`last_invoke_status`、`last_invoke_message`、`last_invoke_job_id`、`last_invoke_at`、`attention_reason`、`actions[]`、`created_at`、`updated_at`；test: `ok`、`status`、`message`、`checked_at`；invoke: `accepted`、`job_id`、`status`、`message` |
| 状态机 | `configured` -> `online/healthy` 或 `error` |
| 修订 / 事件模型 | SSE `connector:events` 包含 `upserted`、`tested`、`invoked`；最近一次 test/invoke 摘要会持久化到 connector 记录，dashboard attention 可直接钻取到带过滤条件的集成视图 |

## Security / Release Guardrails

| 项目 | 内容 |
| --- | --- |
| Endpoint | `GET /api/v1/console/overview`、`GET /api/v1/console/diagnostics`、`POST /api/v1/nodes/register`、`POST /api/v1/nodes/heartbeat`、`POST /api/v1/jobs/pull`、`POST /api/v1/jobs/{id}/progress`、`POST /api/v1/jobs/{id}/renew`、`POST /api/v1/jobs/{id}/result`、`POST /api/v1/jobs/{id}/fail` |
| 请求 schema | 人类控制面接口必须带 JWT，并通过 `tenant_id` 绑定 `get_tenant_db()`；机器接口除了 `Authorization: Bearer <node_token>` 外，body 还必须带 `tenant_id` |
| 响应 schema | `overview/diagnostics` 仅对已认证用户返回，并继承租户过滤后的节点、作业、连接器聚合结果；bootstrap 回执必须包含 `RUNNER_TENANT_ID` |
| 状态机 | 控制面表 `nodes / jobs / job_attempts / job_logs / connectors` 默认处于租户隔离域；匿名访客不再读取高敏聚合接口 |
| 修订 / 事件模型 | 入口层通过 `MACHINE_API_ALLOWLIST` 对机器通道追加第二道防线，默认 `private_ranges`；离线发布通过 `image-lock.txt + commit SHA asset name` 冻结镜像事实，禁止 `--clobber` 覆盖同名资产 |

## Tenant Scope / Release Determinism

| 项目 | 内容 |
| --- | --- |
| Endpoint | `POST /api/v1/auth/pin/login`、`POST /api/v1/auth/users`、`DELETE /api/v1/auth/credentials/{credential_id}`、`POST /api/v1/auth/invites`、`POST /api/v1/jobs`、`POST /api/v1/nodes` |
| 请求 schema | `pin/login`、`password/login`、`webauthn/login*`、`webauthn/register/begin` 都必须显式携带 `tenant_id`；`jobs` 幂等查重必须带租户维度；`nodes` 发证与注册必须绑定租户作用域；租户管理员的人类管理接口必须继承自身 `tenant_id` |
| 响应 schema | JWT 中的 `tenant_id`、`role` 必须与真实用户一致；`users`、`jobs` 与 `nodes` 的冲突语义分别收口为 `(tenant_id, username)`、`(tenant_id, idempotency_key)` 与 `(tenant_id, node_id)` |
| 状态机 | 租户管理员默认仅管理自身租户；保留 `superadmin` 作为显式全局治理角色；RLS 初始化失败默认阻断启动，只有显式软失败开关才允许降级 |
| 修订 / 事件模型 | 离线构建输入固定为 commit SHA、固定 GitHub Action SHA、固定 runner 镜像和显式镜像版本；bundle 额外产出 SHA256 校验文件 |

## Event

| 项目 | 内容 |
| --- | --- |
| Endpoint | `GET /api/v1/events`、`POST /api/v1/events/ping` |
| 请求 schema | SSE 连接可带可选 `client_token`；ping body 为 `connection_id` |
| 响应 schema | Server-Sent Events 流；ping 返回 `{ok: true}` |
| 状态机 | connected -> heartbeat -> disconnected / timeout |
| 修订 / 事件模型 | 基于 channel 的 SSE，覆盖 `hardware`、`switch`、`node`、`job`、`connector` |

## 最新加固补充

- `get_tenant_db()` 与 `get_machine_tenant_db()` 都必须在进入业务查询前完成 `assert_rls_ready()`。
- `GET /api/v1/console/overview` 与 `GET /api/v1/console/diagnostics` 已提升为必须登录访问的高敏接口。
- 机器控制面通道除了 node token 外，还要求入口层 `MACHINE_API_ALLOWLIST` 第二道防线。
- Redis ACL 产物已迁移为外置状态文件，通过 `.env` 中的 `REDIS_ACL_FILE` 注入，不再允许仓库内 secrets 路径。
- 控制面与测试 compose 的外部镜像输入必须统一为 digest pin；CI 会同时检查 `system.yaml` 与 `tests/docker-compose.yml`。
