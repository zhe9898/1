# ZEN70 Gateway Kernel 发版清单

## 身份定义

- 默认产品：`ZEN70 Gateway Kernel`
- 默认运行时 profile：`gateway-kernel`
- 默认 build target：`gateway-kernel`
- 默认 pack 选择：`deployment.packs = []`
- IaC 唯一事实源：`system.yaml` -> `scripts/compiler.py` -> `render-manifest.json`
- legacy profile 只保留兼容语义：`gateway-iot`、`gateway-ops`、`gateway-full` 最终都会投影成 `gateway-kernel + packs`

## 默认服务

- `caddy`
- `docker-proxy`
- `gateway`
- `postgres`
- `redis`
- `runner-agent`
- `sentinel`

`sentinel` 是默认控制面 sidecar，负责监督拓扑监控、routing operator 和进程外控制 worker；`gateway` 进程本身不再在生命周期内启动 probe 或 bitrot worker。

可选业务 pack 不得回流默认 kernel：IoT、Ops、Health、Vector 只能通过显式 pack 选择进入部署与运行时。

## 默认 IaC 基线

- 默认 kernel 服务必须在 `system.yaml` 显式声明 `restart`
- 默认 kernel 服务必须在 `system.yaml` 显式声明 `logging`
- 默认 kernel 服务必须在 `system.yaml` 显式声明 `stop_grace_period`
- 默认 kernel 的 `caddy/postgres/redis/gateway/sentinel/docker-proxy` 必须显式声明 `healthcheck`
- 默认 kernel 的 `gateway/redis` 必须显式声明 `ulimits.nofile >= 65536`
- 默认 kernel 的 `gateway/redis/sentinel/docker-proxy` 必须显式声明 `oom_score_adj = -999`
- 默认 `render-manifest.json` 中 `policy_injections` 与 `tier3_warnings` 必须为空数组
- `scripts/compiler.py --dry-run` 允许出现“本机未安装 docker/docker-compose，跳过预检”提示，但不允许再出现默认 kernel 兜底注入告警

## 默认控制台

- 菜单：`dashboard`、`nodes`、`jobs`、`connectors`
- 仅管理员可见菜单：`settings`
- 公开 capability：`gateway.dashboard`、`gateway.nodes`、`gateway.jobs`、`gateway.connectors`
- 仅管理员 capability：`gateway.settings`

## 默认 API

- `/api/v1/profile`
- `/api/v1/capabilities`
- `/api/v1/console/menu`
- `/api/v1/console/overview`
- `/api/v1/console/diagnostics`
- `/api/v1/nodes`
- `/api/v1/nodes/schema`
- `/api/v1/jobs`
- `/api/v1/jobs/schema`
- `/api/v1/connectors`
- `/api/v1/connectors/schema`
- `/api/v1/settings/schema`
- `/api/v1/events`

## Pack 合同

- `/api/v1/profile` 会明确返回 `requested_pack_keys`、`resolved_pack_keys` 与全部 pack 合同
- `/api/v1/settings/schema` 的 `profile` section 会明确展示当前 pack 选择和可用 pack 清单
- `render-manifest.json` 会明确记录 `requested_packs`、`resolved_packs`
- `GATEWAY_PROFILE` 只表达 kernel 身份；`GATEWAY_PACKS` 只表达 pack 选择
- Dashboard 会直接展示 pack 卡片，包含服务、router、能力、selector 提示、部署边界和运行归属
- `IoT Pack` 对应 `mosquitto + iot/scenes/scheduler`
- `Ops Pack` 对应观测与能耗相关 stack
- `Health Pack` 对应原生健康客户端和 connector 注入边界
- `Vector/AI Pack` 对应 embedding、indexing、search、rerank 边界
- `Health Pack` selector 提示必须包含 `target_executor=swift-native|kotlin-native`
- `Vector/AI Pack` selector 提示必须包含 `target_executor=vector-worker|search-service`
- Pack 合同必须显式返回 `delivery_stage`，并与文档、控制台展示保持一致
- `Health Pack` 当前发布口径必须明确为 `mvp-skeleton`，且交付物至少包含 iOS/Android 原生客户端骨架与 `client.yaml`

## 默认 Runner 协议

- 节点合同是强类型的：`executor`、`os`、`arch`、`zone`、`protocol_version`、`lease_version`、`agent_version`、`max_concurrency`、`cpu_cores`、`memory_mb`、`gpu_vram_mb`、`storage_mb`
- 节点舰队治理是一等公民：`drain_status`、`health_reason` 以及计算得出的 `active_lease_count` 都在默认节点快照里
- 机器通道鉴权是强制的：`nodes/register`、`nodes/heartbeat`、`jobs/pull`、`jobs/{id}/progress`、`jobs/{id}/renew`、`jobs/{id}/result`、`jobs/{id}/fail` 全部要求 `Authorization: Bearer <node_token>`
- 节点凭证由控制面签发并落 DB：`auth_token_hash`、`auth_token_version`、`enrollment_status`
- 节点发证是后端驱动的：`/api/v1/nodes/schema` 定义舰队表单合同，`/api/v1/nodes` 返回一次性机器 token
- 节点启动引导也是后端驱动的：发证/轮换返回 `bootstrap_commands`、`bootstrap_notes` 和 `bootstrap_receipts`
- 原生客户端是节点合同的一等公民：支持 `native-client`、`ios/android`、`swift-native/kotlin-native`
- 节点状态展示语义也是后端驱动的：节点快照返回 `status_view`、`enrollment_status_view`、`drain_status_view`、`heartbeat_state_view`、`capacity_state_view`
- 任务租约合同是强类型的：`idempotency_key`、`attempt`、`lease_token`
- 任务派发选择器是一等公民：`priority`、`target_os`、`target_arch`、`target_executor`、`required_capabilities`、`target_zone`、`required_cpu_cores`、`required_memory_mb`、`required_gpu_vram_mb`、`required_storage_mb`、`timeout_seconds`、`max_retries`、`estimated_duration_s`、`source`
- lease 生命周期是显式的：runner 可以继续通过机器鉴权通道上报 progress 和 renew
- `POST /api/v1/jobs/{id}/result` 与 `POST /api/v1/jobs/{id}/fail` 只接受当前 lease owner 的回调
- 相同 `node_id + attempt + lease_token` 的终态回放是幂等的
- 重试预算由控制面强制执行：失败任务会回收到 `pending`，直到 `retry_count == max_retries`
- 每次 lease 都能通过 `GET /api/v1/jobs/{id}/attempts` 审计
- 运维人员可以通过控制面治理任务和节点：`nodes/{id}/drain`、`nodes/{id}/undrain`、`jobs/{id}/cancel`、`jobs/{id}/retry`、`jobs/{id}/explain`

## 默认运维视图

- `/api/v1/console/overview` 驱动 dashboard，提供节点健康、队列积压、失败压力、连接器注意项
- `/api/v1/console/diagnostics` 驱动节点可靠性、陈旧租约、不可调度 backlog 面板
- Dashboard 的 attention 排序由后端控制，并按严重级别排序
- Dashboard 的 summary cards 和 diagnostics 跳转都由后端驱动，落点是带过滤条件的 Nodes / Jobs / Connectors 视图
- Dashboard diagnostics 可直接暴露后端拥有的推荐动作，用于节点、任务、连接器快速处置
- Dashboard 的 summary/attention tone 以及节点、任务、连接器状态标签都由后端合同驱动，前端只负责通用 tone -> 样式映射
- Jobs 控制台暴露 attempt 历史和 scheduler explain，保证放置决策在 UI 中可审计
- Nodes 控制台暴露 drain 状态、health reason、capacity、active lease 饱和度、executor、显式资源画像，且这些都由后端定义
- Dashboard diagnostics 暴露 `backlog_by_executor`，用于观察异构执行器阻塞面
- Nodes / Jobs / Connectors 的动作统一走一个共享的后端驱动 action dialog，不再各页面各自 prompt
- Nodes / Jobs / Connectors 的页面标题、说明、空状态、策略标签也由后端 schema 驱动
- Jobs / Nodes / Connectors 的状态分组和 badge 语义不再由前端各页各自折叠或推断
- Nodes / Jobs / Connectors 的列表过滤也由后端执行；dashboard drill-down 的 query 会直接命中服务端过滤结果
- 带过滤条件的列表页收到 SSE 更新时会重取当前 query，避免局部事件把不匹配的对象重新塞回结果集

## 默认安全与发布护栏

- 控制面表 `nodes`、`jobs`、`job_attempts`、`job_logs`、`connectors` 必须显式拥有 `tenant_id`
- RLS 白名单必须覆盖上述控制面表
- 人类控制面接口必须通过 `get_tenant_db()` 进入租户上下文
- 机器控制面请求必须同时满足：
  - `Authorization: Bearer <node_token>`
  - body 携带 `tenant_id`
- 节点 bootstrap 回执必须下发 `RUNNER_TENANT_ID`
- `/api/v1/console/overview` 与 `/api/v1/console/diagnostics` 不允许匿名访问
- Caddy 必须为机器通道启用独立 matcher，并通过 `MACHINE_API_ALLOWLIST` 默认限制为 `private_ranges`
- 离线包 workflow 必须：
  - 生成 `image-lock.txt`
  - 资产名带 commit SHA
  - 禁止 `gh release upload --clobber`

## 明确排除项

- Kernel compose 输出中不包含 IoT、Ops、Full pack 服务
- 默认 `render-manifest.json` 的 `requested_packs` / `resolved_packs` 必须为空数组
- `system.yaml` 中不允许明文 tunnel token
- 默认 kernel 配置中不允许残留 phantom sentinel switch/container 映射
- 默认控制面之外不再扩额外业务页面，范围固定为 Nodes / Jobs / Connectors / Settings

## 降级规则

- 缺少 Docker daemon 只会阻塞容器级验证，不影响合同生成
- 缺少 Redis 只会阻塞运行态 readiness，不影响 profile/menu/spec 生成
- 路由运行态协调仅限 `runtime/control-plane/routes.json` 与 `config/Caddyfile`，不会在运行时重写 `.env`、`docker-compose.yml`、`render-manifest.json`、`system.yaml` 或 ACL secrets
- 可选 pack 仍可安装，但不允许回流默认 kernel

## 租户边界与发布冻结

- `PIN` 登录签发的 JWT 必须携带真实 `tenant_id` 与真实 `role`，不允许落回默认租户。
- 登录前用户识别必须按 `(tenant_id, username)` 执行，不允许再假设用户名全局唯一。
- `jobs.idempotency_key` 的唯一性必须按 `(tenant_id, idempotency_key)` 生效。
- `nodes.node_id` 的唯一性必须按 `(tenant_id, node_id)` 生效。
- 用户管理接口默认是租户管理员作用域；只有显式 `superadmin` 才允许跨租户治理。
- RLS 初始化失败必须默认阻断启动；仅在显式软失败开关开启时允许降级返回。
- 离线发布 workflow 必须固定 GitHub Action SHA、固定 runner 镜像、输出 `image-lock.txt` 与 SHA256 校验文件，并禁止覆盖同名资产。
- `system.yaml` 与离线发布 workflow 不允许再出现漂移镜像标签。

## 最新加固补充

- API 启动阶段必须显式执行 JWT 运行时就绪校验；默认弱密钥、空密钥或长度不足都必须拒绝启动。
- API 启动阶段必须显式执行 RLS readiness 校验；tenant 表缺失策略、未启用 `FORCE ROW LEVEL SECURITY` 或缺失 `tenant_id` 时必须拒绝启动。
- `ZEN70_RLS_ALLOW_SOFT_FAIL=true` 仅允许非 production 环境使用；production 一律 fail-fast。
- Redis ACL 不再允许写入仓库内 `runtime/secrets/`；默认产物路径必须是外置安全状态目录，并通过 `REDIS_ACL_FILE` 注入。
- `runtime/secrets/`、`runtime/tmp-compile/`、`config/users.acl` 必须被仓库忽略、预检扫描和 CI 门禁同时覆盖。
- `system.yaml` 与测试 compose 中所有外部镜像必须固定为 `@sha256:` digest，不再接受仅 tag 的可变引用。

## 3.4.1 发布补充

- 所有登录入口都必须在签发 JWT 前统一校验 `user.is_active`，包括密码、PIN、WebAuthn 和邀请降级链路。
- 默认 Runner 编排必须使用 `GATEWAY_BASE_URL=https://caddy` 与 `GATEWAY_CA_FILE=/caddy-data/caddy/pki/authorities/local/root.crt`，不再依赖 HTTP 明文链路。
- `config/Caddyfile` 必须显式生成内部机器 TLS 站点 `https://{$MACHINE_API_INTERNAL_HOST:caddy}` 且使用 `tls internal`。
- 前端认证令牌必须保存在 `sessionStorage`；旧 `localStorage` 令牌只允许做一次性迁移，不允许继续驻留。
- 正式离线包必须排除 `frontend/build_*.txt`、`frontend/eslint_*.txt`、`frontend/vuetsc_*.txt`、`frontend/full_build_*.txt`、`frontend/test_output.txt` 与 `config/system.yaml`。
- `deploy/config-compiler.py` 默认配置入口必须为根 `system.yaml`，避免正式安装包出现双真源错觉。
- `gateway-iot / gateway-ops / gateway-full` 和 `deploy/bootstrap.py` 只能作为迁移兼容层存在；正式发版材料只描述 `gateway-kernel` 与显式 packs。

## 2026-03-28 鉴权与业务边界增补

- 所有业务能力都必须挂接在 `Gateway Identity` 之下。
- 业务域不得自行建立独立主认证体系。
- 业务服务只能消费 Gateway 下发的身份声明、`tenant_id` 和授权范围，并在本域内执行资源级授权。
- 正式发布的 `Gateway Kernel` 只保留认证、授权、控制面状态、合同、审计和最小运行时；不得再把 `drive/media/iot adapter/health/大型 worker` 回流到默认 kernel。

## 2026-03-28 兼容层退场增补

- 正式发布物只允许暴露一个 profile：`gateway-kernel`。
- `gateway-iot / gateway-ops / gateway-full / gateway / full` 只允许作为 legacy 输入兼容，不得再出现在正式安装说明、控制台或 OpenAPI 中。
- 根 `system.yaml` 是唯一正式配置入口；旧 `config/system.yaml` 不得出现在正式仓库 surface 与离线包中。
- `deploy/config-compiler.py` 只能是对 `scripts/compiler.py` 的兼容 wrapper，不得再次演化为第二套编译器。

## 2026-03-28 发布一致性增补

- 正式离线包必须同时携带 `system.yaml`、`render-manifest.json`、`docker-compose.yml`、`config/Caddyfile`、`docs/openapi-kernel.json` 与 `contracts/openapi/zen70-gateway-kernel.openapi.json`。
- `render-manifest.json` 的 `product/profile/requested_packs` 必须与 `system.yaml deployment` 对齐。
- `docker-compose.yml` 的服务集合必须与 `render-manifest.json.services_rendered` 完全一致。
- `docs/openapi-kernel.json` 与 `contracts/openapi/zen70-gateway-kernel.openapi.json` 必须字节语义一致，且至少覆盖 `/api/v1/profile`、`/api/v1/console/overview`、`/api/v1/nodes`、`/api/v1/jobs`、`/api/v1/connectors`、`/api/v1/settings/schema`。
