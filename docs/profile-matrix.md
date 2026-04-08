# Gateway Profile / Pack Matrix

## Runtime Surface

| 项目 | 当前值 | 说明 |
| --- | --- | --- |
| 正式 runtime profile | `gateway-kernel` | 唯一正式 runtime surface |
| 正式产品名 | `ZEN70 Gateway Kernel` | 对外叙事固定 |
| 默认 build target | `gateway-kernel` | kernel-only 依赖集 |
| 默认 pack 选择 | `[]` | 可选能力默认关闭 |
| 正式控制台 surface | `dashboard` `nodes` `jobs` `connectors` `settings(admin)` | backend-driven control plane |

## Explicit Pack Activation

| Pack | Gateway Build Target | Services | Routers | Runtime Owner |
| --- | --- | --- | --- | --- |
| `iot-pack` | `gateway-iot` | `mosquitto` | `iot` `scenes` `scheduler` | `edge-service` |
| `ops-pack` | `gateway-kernel` | `watchdog` `victoriametrics` `grafana` `categraf` `loki` `promtail` `alertmanager` `vmalert` | `observability` `energy` | `ops-stack` |
| `media-pack` | `gateway-kernel` | media-related workers | `media` | `worker-service` |
| `health-pack` | `gateway-kernel` | native clients / SDK boundary | no default router | `native-client` |
| `vector-pack` | `gateway-kernel` | search / vector workers | `search` | `worker-service` |

## Notes

- `deployment.profile`、`GATEWAY_PROFILE` 只允许表达 `gateway-kernel`。
- Pack 选择来自 `deployment.packs`、`GATEWAY_PACKS` 和编译产物中的 `requested_packs` / `resolved_packs`。
- `gateway-iot` 是 `iot-pack` 的镜像目标，不是正式 runtime profile。
- 文档、控制台、OpenAPI 和 IaC 产物必须共同保持这套口径。
