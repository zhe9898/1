# ZEN70 Pack 矩阵

## 原则

- 所有业务能力都必须挂接在 `Gateway Identity` 之下。
- 业务域不得自行建立独立主认证体系。
- 业务服务只消费 Gateway 下发的身份声明、`tenant_id` 和授权范围，并在本域内执行资源级授权。
- `Gateway Kernel` 只保留认证、授权、控制面状态、合同、审计与最小运行时。
- `pack` 承载业务执行面、原生采集面和重业务 worker，不允许回流默认 kernel。

## 成熟度说明

- `runtime-present`：已有运行体和默认回归护栏，可作为独立 pack 交付。
- `mvp-skeleton`：已有最小交付骨架、身份合同和目录结构，但还不是完整业务产品。
- `contract-only`：当前仅冻结合同、能力边界和调度提示，不声称已有运行体。
- `compatibility-only`：只用于历史兼容输入，不作为正式产品面暴露。

## Pack 总表

| Pack | 类别 | 成熟度 | 运行体 | 默认回流 Kernel | 身份与授权模型 | 调度提示 |
| --- | --- | --- | --- | --- | --- | --- |
| `iot-pack` | IoT | `runtime-present` | `mosquitto` + `iot/scenes/scheduler` | 否 | 只消费 Gateway Identity，在本域内做设备/场景资源授权 | `required_capabilities=iot.adapter`, `target_zone=home` |
| `ops-pack` | Ops | `runtime-present` | 可观测与运维 stack | 否 | 只消费 Gateway Identity，在本域内做告警/面板资源授权 | `required_capabilities=ops.observe`, `target_zone=ops` |
| `health-pack` | Health | `mvp-skeleton` | iOS / Android 原生客户端骨架 | 否 | 只消费 Gateway Identity、`tenant_id`、`node_token` 与 bootstrap receipts | `required_capabilities=health.ingest`, `target_zone=mobile`, `target_executor=swift-native|kotlin-native` |
| `vector-pack` | AI / Search | `contract-only` | 合同冻结，运行体待后续独立交付 | 否 | 只消费 Gateway Identity，在本域内做语义检索资源授权 | `required_capabilities=vector.search`, `target_zone=search`, `target_executor=vector-worker|search-service` |
| ~~`full-pack`~~ | ~~Bundle~~ | ~~`compatibility-only`~~ | — | — | **v3.43 已下架** | — |

## Health Pack 最小交付体

`health-pack` 当前已经不再是 placeholder，最小交付物包括：

- `clients/health-ios/client.yaml`
- `clients/health-ios/Package.swift`
- `clients/health-ios/Sources/HealthGatewayClient/*`
- `clients/health-android/client.yaml`
- `clients/health-android/settings.gradle.kts`
- `clients/health-android/build.gradle.kts`
- `clients/health-android/src/main/kotlin/io/zen70/healthgateway/*`

这些骨架的职责是：

- 明确原生客户端如何消费 Gateway bootstrap receipts。
- 明确 `tenant_id / node_id / node_token / gateway_base_url / ca_file` 的最小合同。
- 明确健康数据采集边界仍在原生端，Python 默认运行时不直接接触 HealthKit / Health Connect。

当前不声称已完成的内容：

- 完整 iOS App 产品实现
- 完整 Android App 产品实现
- 健康业务域的完整数据模型与可视化产品面

## 交付纪律

- 正式发布口径只允许暴露 `gateway-kernel + deployment.packs`。
- `gateway-iot / gateway-ops` 只允许作为 legacy 输入兼容。`gateway-full / full` 配置项已在 v3.43 永久下架，不得再出现在正式产品说明里。
- Pack 成熟度必须同时出现在代码合同、控制台展示和文档中，三者不得漂移。
