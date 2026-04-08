# Control Plane Protocol Matrix

## Profile

| 项目 | 内容 |
| --- | --- |
| Endpoint | `GET /api/v1/profile` |
| 请求 | 无 |
| 响应核心字段 | `product`, `profile`, `runtime_profile`, `router_names`, `console_route_names`, `capability_keys`, `requested_pack_keys`, `resolved_pack_keys`, `packs[]` |
| 状态机 | runtime profile 固定为 `gateway-kernel`；pack 由显式 pack keys 解析后形成 `requested_pack_keys` / `resolved_pack_keys` |

## Capabilities

| 项目 | 内容 |
| --- | --- |
| Endpoint | `GET /api/v1/capabilities` |
| 请求 | 已认证控制面请求 |
| 响应核心字段 | capability key -> `status`, `enabled`, `endpoint`, `models`, `reason` |
| 状态机 | capability 只有在 registry、surface、policy 和 service contract 同时成立时才暴露 |

## Console Menu

| 项目 | 内容 |
| --- | --- |
| Endpoint | `GET /api/v1/console/menu` |
| 请求 | 已认证控制面请求 |
| 响应核心字段 | `product`, `profile`, `runtime_profile`, `items[]` |
| 状态机 | 控制面 surface 由后端注册表与 policy gate 决定，前端只消费合同 |

## Nodes

| 项目 | 内容 |
| --- | --- |
| Endpoint | `/api/v1/nodes/*` |
| 请求核心字段 | `node_id`, `executor`, `os`, `arch`, `zone`, `capabilities`, `metadata`, `agent_version` |
| 响应核心字段 | `status_view`, `enrollment_status_view`, `drain_status_view`, `heartbeat_state_view`, `capacity_state_view`, `actions[]` |
| 状态机 | `pending -> active -> draining/revoked/offline`，机器通道统一使用 `Authorization: Bearer <node_token>` |

## Jobs

| 项目 | 内容 |
| --- | --- |
| Endpoint | `/api/v1/jobs/*` |
| 请求核心字段 | `idempotency_key`, `required_capabilities`, `target_executor`, `target_zone`, 资源选择器，`lease_token`, `attempt` |
| 响应核心字段 | `status_view`, `lease_state_view`, `actions[]`, `attempts[]`, `explain` |
| 状态机 | `pending -> leased -> completed/failed/canceled`；续租、进度和结果都绑定当前 lease owner |

## Connectors

| 项目 | 内容 |
| --- | --- |
| Endpoint | `/api/v1/connectors/*` |
| 请求核心字段 | `connector_id`, `kind`, `endpoint`, `config`, `action`, `payload` |
| 响应核心字段 | `status_view`, `last_test_*`, `last_invoke_*`, `actions[]` |
| 状态机 | `configured -> online/error`，测试与触发都通过后端合同持久化摘要 |

## Events

| 项目 | 内容 |
| --- | --- |
| Endpoint | `GET /api/v1/events`, `POST /api/v1/events/ping` |
| 协议 | Server-Sent Events |
| 事件面 | `node`, `job`, `connector` 等控制面事件 |
| 状态机 | `connected -> heartbeat -> disconnected/timeout` |

## Rules

- 前端是协议消费者，不是事实源。
- `gateway-kernel` 是唯一正式 runtime profile。
- 业务扩展只通过 pack 和 capability 合同进入协议面。
