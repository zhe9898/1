# ZEN70 控制面阶段路线图

- **最后更新**: 2026-03-27
- **适用范围**: `ZEN70 Gateway Kernel`、`Go Runner Agent`、后端驱动控制台
- **目的**: 将执行路线冻结到单一文档中，后续交付状态不再依赖聊天上下文记忆。

## 阶段总表

| 阶段 | 目标 | 范围 | 当前状态 | 出阶段标准 | 明确不包含 |
| --- | --- | --- | --- | --- | --- |
| `Phase 1` | 将 Gateway Kernel 冻结为默认控制面产品 | `profile/compose/build target/capability/menu` 闭环；默认 kernel 服务收口；Nodes/Jobs/Connectors 基线；后端驱动控制台基线；Go runner 拆分；健康客户端占位；pack 保留但不回流默认 kernel | `已完成` | `system.yaml -> manifest -> compose -> runtime profile -> capabilities -> menu` 全部一致；默认 compose 只包含 kernel 服务；前端默认页面全部是后端驱动的 kernel 控制面 | 高级调度、公有云舰队治理、IoT/Health/Vector 业务 pack |
| `Phase 2` | 让调度器和运维控制台达到生产可用 | 节点机器鉴权；任务选择器；attempt 历史；调度打分 v1；节点容量契约；drain/undrain；progress/renew；cancel/retry；explain；console overview；console diagnostics；Nodes/Jobs/Dashboard 消费后端动作合同 | `已完成` | 节点动作和任务动作都由后端拥有；任务放置可解释；陈旧租约 / 堵塞 backlog / 节点可靠性在 UI 中可见；backend、frontend、runner、contracts、IaC 验证全绿 | 多资源公平调度、批量舰队操作、业务 pack 执行 |
| `Phase 3` | 将控制台产品化，前端不再手工拼业务逻辑 | `schema/action` 驱动 UI；后端下发 `actions`、`policies`、`form schema`；Dashboard 成为运营首页；Nodes 成为舰队页；Jobs 成为任务运营台；Connectors 成为集成中心 | `已完成` | 前端读模型和写模型都由后端驱动；前端不再硬编码动作开关或状态机；Dashboard/Nodes/Jobs/Connectors 全部渲染后端拥有的合同；资源过滤由服务端合同执行 | IoT/Health/Vector pack 执行、移动端客户端、高阶多资源调度 |
| `Phase 4` | 将业务能力 pack 与 kernel 彻底分层 | `IoT Pack` 承载 MQTT/Matter/HomeKit/BLE/LAN、场景、规则、设备状态缓存；`Health Pack` 承载 iOS HealthKit / Android Health Connect 原生采集；`Vector/AI Pack` 承载 embedding/indexing/search/rerank；调度只通过 `capability + zone + selector` 派发，业务执行不进入 gateway 进程 | `已完成` | pack 注册表成为 IaC、运行时和控制台共享合同；默认 kernel 的 `profile` 固定为 `gateway-kernel` 且 `packs=[]`；legacy profile 只作为 pack preset 兼容层；dashboard/settings/profile/manifest 都能看见 pack 边界 | 移动优先 UX、高阶异构资源调度 |
| `Phase 5` | 引入移动端/原生客户端和更高阶异构算力 | 移动/原生客户端复用控制面合同；健康、通知和本地能力继续通过原生桥接；默认检索维持 `pgvector` 边界；Win/Mac/Linux/iOS/Android 节点按 `os/arch/executor/capability/zone/resources` 精准派发 | `已完成（控制面范围）` | 节点合同显式拥有 `executor + resources`；作业合同显式拥有 `target_executor + resource selectors`；scheduler / explain / diagnostics 能解释异构放置；原生客户端发证回执由后端拥有；pack selector 提示和 dashboard/Jobs/Nodes 展示同步更新 | iOS/Android 原生应用仓库本体、独立向量服务集群实现 |

## 各阶段固定范围

### Phase 1

- 冻结默认产品为 `ZEN70 Gateway Kernel`
- 保证 `profile`、`compose`、`build target`、`capability`、`console menu` 一致
- 将 Nodes / Jobs / Connectors 固定为默认控制面业务面
- 前端保持后端驱动控制台，不退回自由拼装 SPA

### Phase 2

- 用每节点机器 token 保护公网执行通道
- 将“队列行为”升级为“调度行为”，引入选择器和 attempt 历史
- 增加节点容量事实与 drain 状态
- 增加任务 `progress / renew / cancel / retry / explain` 合同
- 将节点/任务动作开关从前端启发式逻辑迁回后端响应合同
- 暴露 dashboard 运维诊断：陈旧租约、堵塞 backlog、节点压力

### Phase 3

- 让控制台真正做到端到端后端驱动
- 后端下发 `actions`、`policies`、`form schema`
- Dashboard 成为运营首页
- Nodes 成为舰队管理页
- Jobs 成为任务运营台
- Connectors 成为集成中心
- 前端不得硬编码动作开关和状态机
- 当前迁移进度：
  - `Jobs`：创建 schema 和动作合同已经由后端拥有
  - `Connectors`：upsert schema、test/invoke 持久化状态、动作合同已经由后端拥有
  - `Nodes`：发证 schema、一次性 token 展示、后端下发启动指引、舰队动作合同已经由后端拥有
- `Dashboard`：summary cards、route/filter drill-down intent 已由后端拥有
- `Dashboard diagnostics`：节点、任务、连接器推荐动作已由后端拥有
- `Resource chrome`：Nodes / Jobs / Connectors 已消费后端下发的 `title`、`description`、`empty_state`、`policies`
- `Resource status semantics`：Nodes / Jobs / Connectors 以及 diagnostics 已消费后端下发的 `*_view` 展示合同，前端不再本地折叠状态分组、badge tone 或资源状态标签
- `Resource list filtering`：Nodes / Jobs / Connectors 的 query 过滤已由服务端执行；带过滤视图下收到 SSE 时会重取当前 query，避免本地事件污染结果集
- 所有共享动作执行统一收口到一个 action dialog，不再各页面各自 prompt
- Nodes / Jobs / Connectors 已消费 dashboard 下发的过滤条件，不再要求操作者手工二次筛选

### Phase 4

- 业务能力不能污染默认 kernel 运行时
- `IoT Pack`：MQTT/Matter/HomeKit/BLE/LAN 设备接入、场景、规则、设备状态缓存
- `Health Pack`：iOS HealthKit / Android Health Connect 原生采集
- `Vector/AI Pack`：embedding、indexing、search、rerank
- 调度方式固定为 `capability + zone + selector`
- 业务执行不进入 gateway 进程
- `system.yaml` 显式声明 `deployment.packs` 与 `deployment.available_packs`
- `render-manifest.json` 显式记录 `requested_packs` 与 `resolved_packs`
- `GATEWAY_PROFILE` 固定为 kernel 身份，`GATEWAY_PACKS` 负责运行时 pack 选择
- `gateway-iot`、`gateway-ops`、`gateway-full` 不再是产品本体，只是兼容 preset
- `/api/v1/profile` 与 `/api/v1/settings/schema` 都会下发 pack 合同：服务、router、能力、selector 提示、部署边界、运行归属
- Dashboard 直接展示 pack 卡片，操作者无需再靠文档记忆 pack 边界
- `Health Pack` 已从 placeholder 升级为最小原生客户端骨架，交付物包含 `client.yaml`、iOS Swift Package 骨架、Android Gradle/Kotlin 骨架
- Pack 合同新增 `delivery_stage`，明确区分 `runtime-present / mvp-skeleton / contract-only / compatibility-only`

### Phase 5

- 移动 App 作为用户入口
- 原生桥接处理健康、通知和本地能力接入
- 检索先用 `pgvector`，规模上来再拆独立向量服务
- Win/Mac/Linux 节点按 `os`、`arch`、`executor`、`capability`、`zone`、`resources` 调度
- 移动/原生客户端仍复用控制面合同，不另起一套侧协议
- 当前仓库交付范围：
  - 节点合同显式增加 `cpu_cores`、`memory_mb`、`gpu_vram_mb`、`storage_mb`
  - 节点合同支持 `native-client`、`ios/android`、`swift-native/kotlin-native/vector-worker/search-service`
  - 发证/轮换回执由后端下发原生客户端 bootstrap receipts
  - 任务合同显式增加 `target_executor` 与资源选择器
  - 调度器、explain、console diagnostics 能解释 executor/resource-aware 异构放置
  - dashboard / nodes / jobs 已展示 executor 与资源合同
- 不在本仓库内完成的内容：
  - iOS/Android 原生 App 产品实现
  - 独立向量服务集群拆分
  - 健康业务本体或 IoT 业务本体实现

## 当前交付快照

截至 `2026-03-27`：

- `Phase 1`：已完成
- `Phase 2`：已完成
- `Phase 3`：已完成
- `Phase 4`：已完成
- `Phase 5`：已完成（控制面范围）

## Phase 2 完成依据

Phase 2 之所以判定为完成，是因为以下条件全部成立：

- Nodes 暴露了后端拥有的容量事实和 drain 状态
- Jobs 暴露了后端拥有的动作合同和 lease 状态
- Runner 支持 progress 与 lease renewal
- Scheduler explain 可按任务读取
- Dashboard 暴露了 overview 与 diagnostics 面板
- Nodes / Jobs 页面消费后端动作合同，而不是本地动作启发式
- backend、frontend、runner、OpenAPI/contracts、IaC dry-run 全部验证为绿

## 护栏

- 默认 `Kernel` 仍然只做控制面
- `IaC` 仍然是唯一发布事实源
- 运行态控制状态不得修改发布产物
- 新业务功能必须落在 pack，不允许再回流默认 kernel

## 2026-03-28 鉴权边界增补

- 所有业务能力都必须挂接在 `Gateway Identity` 之下。
- 业务域不得自行建立独立主认证体系。
- 业务服务只消费 Gateway 下发的身份声明、`tenant_id` 和授权范围，并在本域内执行资源级授权。
- `Gateway Kernel` 保留 `authn/authz`、`node/job/connector/state`、`contract/audit` 与最小运行时，不再继续把业务执行面回流到默认 kernel。
- `Drive / Media / IoT Adapter / Health / 大型业务 Worker` 都属于 pack 或执行面，不属于默认 Gateway Kernel 运行时。

## 2026-03-28 兼容层退场增补

- 正式运行时 profile surface 固定为 `gateway-kernel`，不再把 `gateway-iot / gateway-ops / gateway-full / gateway / full` 作为产品定义暴露。
- 历史 profile 名只允许作为 legacy 输入存在，并在规范化阶段折叠为 `gateway-kernel + packs`。
- 根 `system.yaml` 是唯一正式配置入口；旧 `config/system.yaml` 已从正式 surface 中移除。
- `deploy/config-compiler.py` 与 `deploy/bootstrap.py` 只允许作为兼容 wrapper，正式文档、安装说明和发布物只指向 `scripts/compiler.py` 与 `scripts/bootstrap.py`。
- 离线包、硬化门禁和文档必须共同阻止 legacy profile 与旧配置入口重新回流正式交付面。
