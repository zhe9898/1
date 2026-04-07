# Health Android Client Skeleton

## 目标

`health-android` 是 `health-pack` 的最小可交付骨架，不是完整 App。本目录用于固定 Android 原生健康采集与 `Gateway Identity` 的接入边界。

## 当前交付

- `client.yaml`：客户端交付元数据与身份合同
- `settings.gradle.kts` / `build.gradle.kts`：最小 Gradle 骨架
- `src/main/kotlin/io/zen70/healthgateway/`：bootstrap、身份上下文、上报 envelope 的最小模型

## 身份边界

- 主认证体系由 Gateway 提供
- Android 客户端只消费 Gateway 下发的：
  - `tenant_id`
  - `node_id`
  - `node_token`
  - `gateway_base_url`
  - `gateway_ca_file`
  - bootstrap receipts
- Android 客户端不得自行建立独立主认证体系

## 非目标

- 不在 Python 默认 gateway 运行时中直接接入 Health Connect
- 不在本目录中声明完整 Android App UI、通知或业务数据模型
