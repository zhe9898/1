# Health iOS Client Skeleton

## 目标

`health-ios` 是 `health-pack` 的最小可交付骨架，不是完整 App。本目录的职责是把 iOS 原生健康采集与 `Gateway Identity` 的接入边界固定下来。

## 当前交付

- `client.yaml`：客户端交付元数据与身份合同
- `Package.swift`：最小 Swift Package 清单
- `Sources/HealthGatewayClient/`：bootstrap、身份上下文、上报 envelope 的最小模型

## 身份边界

- 主认证体系由 Gateway 提供
- iOS 客户端只消费 Gateway 下发的：
  - `tenant_id`
  - `node_id`
  - `node_token`
  - `gateway_base_url`
  - `gateway_ca_file`
  - bootstrap receipts
- iOS 客户端不得自行建立独立主认证体系

## 非目标

- 不在 Python 默认 gateway 运行时中直接接入 HealthKit
- 不在本目录中声明完整 iOS App UI、通知或业务数据模型
