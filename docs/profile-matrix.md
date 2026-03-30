# Gateway Profile / Pack 矩阵

## Profile 入口矩阵

| 入口 | 实际公开 profile | 默认请求 pack | 有效 pack | Gateway Build Target | 默认服务集合 | 控制台表面 | 说明 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `gateway-kernel` | `gateway-kernel` | 无 | 无 | `gateway-kernel` | `caddy`、`docker-proxy`、`gateway`、`postgres`、`redis`、`runner-agent`、`sentinel` | `dashboard`、`nodes`、`jobs`、`connectors`、`settings(admin)` | 默认产品线，只承载控制面。 |
| `gateway-iot` | `gateway-kernel` | `iot-pack` | `iot-pack` | `gateway-iot` | kernel + `mosquitto` | 同 kernel 控制台 | 兼容 preset，不再代表独立产品身份。 |
| `gateway-ops` | `gateway-kernel` | `ops-pack` | `ops-pack` | `gateway-kernel` | kernel + ops 观测服务 | 同 kernel 控制台 | 兼容 preset，不再代表独立产品身份。 |
| ~~`gateway-full`~~ | — | — | — | — | — | — | **v3.43 已下架**，兼容 bundle preset 不再支持。 |

## Pack 合同矩阵

| Pack | 服务 | Router | 能力注册表 | 调度提示 | 部署边界 | 运行归属 |
| --- | --- | --- | --- | --- | --- | --- |
| `iot-pack` | `mosquitto` | `iot`、`scenes`、`scheduler` | `pack.iot`、`iot.adapter`、`iot.scene`、`iot.rule`、`iot.device.state` | `required_capabilities=iot.adapter`、`target_zone=home` | 边缘设备与家庭网络侧执行，不进入默认 gateway 请求进程 | `edge-service` |
| `ops-pack` | `watchdog`、`victoriametrics`、`grafana`、`categraf`、`loki`、`promtail`、`alertmanager`、`vmalert` | `observability`、`energy` | `pack.ops`、`ops.observe`、`ops.energy` | `required_capabilities=ops.observe`、`target_zone=ops` | 独立观测与运维 stack，不进入默认 gateway 请求进程 | `ops-stack` |
| `health-pack` | 无默认容器 | 无默认 router | `pack.health`、`health.ingest` | `required_capabilities=health.ingest`、`target_zone=mobile` | 原生 iOS/Android 客户端采集，健康 SDK 不进入 Python gateway 运行时 | `native-client` |
| `vector-pack` | 无默认容器 | `search` | `pack.vector`、`vector.embed`、`vector.index`、`vector.search`、`vector.rerank` | `required_capabilities=vector.search`、`target_zone=search` | 语义检索与重排由 worker/search 服务承载，不进入默认 gateway 请求进程 | `worker-service` |
| ~~`full-pack`~~ | — | — | — | — | **v3.43 已下架** | — |

## 说明

- `gateway`、`core`、`safe-kernel`、`gateway-core` 最终都归一到 `gateway-kernel`。
- `gateway-iot`、`gateway-ops` 现在只负责把 legacy preset 展开成 `deployment.packs`。`gateway-full` 已在 v3.43 下架。
- 真正的 pack 事实源是 `deployment.packs`、`deployment.available_packs`、`GATEWAY_PACKS`、`render-manifest.json`。
- 默认 kernel 控制台仍只暴露控制面 surface；pack 合同通过 Dashboard、Settings 和 `/api/v1/profile` 展示。
- 运行态路由协调仍限制在 `runtime/control-plane/routes.json` 与 `config/Caddyfile`，pack 不得回写 IaC 发布产物。
