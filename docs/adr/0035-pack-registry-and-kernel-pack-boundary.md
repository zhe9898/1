# ADR 0035: Pack 注册表与 Kernel / Pack 边界收口

- Status: Accepted
- Date: 2026-03-27
- Scope: Pack 注册表与 Kernel / Pack 边界收口

> Source of truth: code and tests override ADR text. See ADR 0052 when documentation and implementation diverge.

## 背景

在 `Phase 1-3` 完成后，默认控制面已经稳定收口到 `ZEN70 Gateway Kernel`，但业务能力仍主要通过 `gateway-iot`、`gateway-ops`、`gateway-full` 这类 profile 变体表达。这样会带来三个问题：

1. `profile` 混入业务边界，导致 IaC、运行时、控制台和文档很容易继续把 IoT / Ops / Full 当成默认产品线，而不是可选 pack。
2. `system.yaml`、compiler、后端运行时和前端控制台没有共享的 pack 合同，后续极易再次出现“某处知道 pack，某处不知道”的漂移。
3. 健康、向量检索、IoT 等业务能力本应通过 `capability + zone + selector` 派发到 pack 边界，却仍容易被误塞回 gateway 进程或默认 kernel 运行时。

## 决策

我们将业务扩展能力正式收口为 **pack 注册表**，并规定：

1. `deployment.profile` 只表达 kernel 身份，当前公开 profile 固定为 `gateway-kernel`。
2. `deployment.packs` 与 `deployment.available_packs` 成为 pack 事实源。
3. `gateway-iot`、`gateway-ops`、`gateway-full` 保留为 **legacy preset**，只负责兼容地展开成 pack 选择，不再代表独立产品定义。
4. 运行时环境拆分为：
   - `GATEWAY_PROFILE`：kernel 身份
   - `GATEWAY_PACKS`：pack 选择
5. `render-manifest.json` 必须记录 `requested_packs` 与 `resolved_packs`。
6. `/api/v1/profile` 与 `/api/v1/settings/schema` 必须显式返回 pack 合同，包括：
   - pack 标识、分类、描述
   - 服务、router、能力键
   - selector 提示
   - 部署边界与运行归属
7. Dashboard 必须直接展示 pack 卡片，让操作者从控制台就能看到 pack 边界，而不是再依赖文档或聊天上下文。

## Pack 注册表

- `iot-pack`
  - 服务：`mosquitto`
  - router：`iot`、`scenes`、`scheduler`
  - 边界：边缘家庭网络执行，不进入默认 gateway 请求进程
- `ops-pack`
  - 服务：`watchdog`、`victoriametrics`、`grafana`、`categraf`、`loki`、`promtail`、`alertmanager`、`vmalert`
  - router：`observability`、`energy`
  - 边界：运维/观测 stack，不进入默认 gateway 请求进程
- `health-pack`
  - 无默认容器
  - 边界：原生 iOS/Android 客户端采集，不把健康 SDK 接进 Python gateway
- `vector-pack`
  - 无默认容器
  - router：`search`
  - 边界：embedding/index/search/rerank 由 worker/search 服务承担
- ~~`full-pack`~~
  - ~~兼容 bundle~~
  - ~~负责展开其它 pack，并承接历史 full surface 兼容 router~~
  - **_v3.43 已下架_**：`pack_registry.py` 中已移除；`system.yaml` `available_packs` 已移除；`gateway-full` preset 不再接受。

## 影响

### 正向影响

- kernel 身份和业务 pack 彻底分层，默认产品定义不会再被 IoT / Ops / Full 语义污染。
- IaC、运行时、前端、文档、manifest 全部共享同一套 pack 合同。
- 业务调度边界更清晰：业务工作只通过 `capability + zone + selector` 派发。
- dashboard/settings/profile 可以直接展示当前 pack 状态，降低运维误判。

### 代价与权衡

- 兼容期内仍需保留 legacy preset，以免历史脚本和离线包直接失效。
- ~~`gateway-full` 仍保留 compatibility bundle 逻辑，因此 full 兼容入口尚未被彻底删除。~~ **_v3.43: 已彻底删除，不再保留任何 full-pack 兼容入口。_**
- `Health Pack` 与 `Vector Pack` 当前主要是合同与边界收口，真正的业务服务和移动端接入仍属于 `Phase 5` 继续推进的范围。

## 不做的事

- 不把 IoT / Health / Vector 业务执行重新塞回默认 gateway 进程。
- 不把 pack 重新做成新的默认 profile 产品线。
- 不允许 pack 运行态状态回写 `system.yaml`、`docker-compose.yml` 或 `render-manifest.json` 以外的发布事实源。
