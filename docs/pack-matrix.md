# ZEN70 Pack Matrix

## Principles

- Pack 是能力合同与运行边界，不是新产品线。
- Kernel 只承载控制面、治理、合同和最小运行时。
- 业务能力只能通过显式 canonical pack keys 进入运行时。
- Pack 不能绕过 kernel contract、policy 或 owner service 直接写核心状态。

## Pack Catalog

| Pack | Category | Delivery Stage | Services / Routers | Selector Hints | Deployment Boundary | Runtime Owner |
| --- | --- | --- | --- | --- | --- | --- |
| `iot-pack` | IoT | `runtime-present` | `mosquitto`; `iot` `scenes` `scheduler` | `required_capabilities=iot.adapter`, `target_zone=home` | 家庭/边缘网络执行，不进入默认 kernel 请求进程 | `edge-service` |
| `ops-pack` | Ops | `runtime-present` | observability stack; `observability` `energy` | `required_capabilities=ops.observe`, `target_zone=ops` | 独立运维与观测 stack，不进入默认 kernel 请求进程 | `ops-stack` |
| `media-pack` | Media | `runtime-present` | media workers; `media` | media capability hints | 媒体处理下沉到独立 worker 边界 | `worker-service` |
| `health-pack` | Health | `mvp-skeleton` | native skeleton clients | `required_capabilities=health.ingest`, `target_zone=mobile`, `target_executor=swift-native|kotlin-native` | 原生客户端采集，不把健康 SDK 接回 Python gateway | `native-client` |
| `vector-pack` | AI / Search | `contract-only` | search / vector workers; `search` | `required_capabilities=vector.search`, `target_zone=search`, `target_executor=vector-worker|search-service` | 语义检索与重排由独立 worker/search 服务承载 | `worker-service` |

## Delivery Discipline

- 新 pack 必须先落合同，再落调度边界，再落运行体。
- `delivery_stage` 必须同时体现在代码、控制台展示和文档中。
- 任何 pack 都不得重新长成新的“默认 profile”。
