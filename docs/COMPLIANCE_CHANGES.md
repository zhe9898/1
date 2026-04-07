# ZEN70 法典合规变更总览

## Document Control

| 属性 | 值 |
|:---|:---|
| **文档编号** | ZEN70-DOC-CHANGES-001 |
| **法典版本** | V2.0 绝对零度版 |
| **最后更新** | 2026-03-21T22:20+08:00 |
| **变更条目总数** | 31 条 |
| **关联文档** | [CHANGELOG](CHANGELOG.md) · [合规矩阵](CANON_COMPLIANCE.md) · [架构检查点](ARCHITECTURE_CHECKPOINTS.md) |
| **追溯关系** | 变更 → 法典条款 → CANON_COMPLIANCE ID → CHANGELOG FIX/CHG ID |

本文档汇总为满足 `.cursorrules`（ZEN70 法典）所完成的**全部代码与配置变更**，便于审计与回溯。

## 变更影响分析摘要

| 影响域 | 变更数 | 风险等级 | 验证状态 |
|:---|:---|:---|:---|
| IaC 编译器 | 8 | P0-P2 | ✅ compiler --dry-run + 容器全 Up |
| Caddy 路由 | 3 | P0 | ✅ curl + 浏览器 E2E |
| 前端 PWA/SW | 2 | P1 | ✅ 新窗口 + 缓存清理 |
| 容器健康检查 | 2 | P2-P3 | ✅ docker ps 全 healthy |
| 后端 API | 4 | P1-P2 | ✅ 单元测试 + 集成 |
| 数据库/迁移 | 3 | P0-P1 | ✅ alembic current = head |
| 可观测性 | 2 | P2-P3 | ✅ 容器日志正常 |
| 核心 API / Auth | 4 | P0 | ✅ E2E 控制台调用 |

---

## 〇、IaC 管线永久回写 + P0 安全缺陷 + 全量代码质量清扫 (2026-03-21)

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `scripts/compiler.py` | 重构 | 新增 `stop_grace_period_block` 生成、修正全量 healthcheck 默认探针（VM `/health`、alertmanager `/-/healthy`、caddy `:2019/config/`）、`start_period` 注入、OOM 扩展至 sentinel/watchdog/docker-proxy、`depends_on` 自动升级为结构化条件 (`service_healthy`/`service_started`) |
| `scripts/templates/docker-compose.yml.j2` | 修改 | 新增 `{{ svc.stop_grace_period_block }}` 渲染插槽 |
| `system.yaml` | 修改 | 新增 `watchdog` 服务定义 (ADR 0006)；docker-proxy `POST=1`；mosquitto_passwd 卷挂载 |
| `backend/api/portability.py` | **P0 修复** | `secure_shred_file` fallback `"ba+"` → `"r+b"` 真覆写 + `os.fsync()` 每轮刷盘 + 1MB 分块防内存峰值 |
| `backend/api/portability.py` | **P1 修复** | 流式导出 `read_bytes()` → `ZipInfo.open("w")` 1MB 分块读取防 OOM |
| `backend/api/cluster.py` | **P1 修复** | `subprocess.run` 在 async 路由中阻塞 → 包裹 `asyncio.to_thread()` |
| `backend/api/routes.py` | **P1 修复** | Redis pubsub 返回 `bytes` 未 decode → 显式 `.decode("utf-8")` 防止 SSE `b'...'` 字面量 |
| `.github/workflows/ci.yml` | **P1 修复** | `--cov=.` 涵盖测试代码稀释覆盖率 → `--cov=api --cov=core --cov=models --cov=sentinel` 仅统计业务代码 |
| 9 个后端文件 | 清扫 | 删除 30+ 处标准库懒加载 import、5 个废 import (`platform`/`partial`/`get_current_user`/`Field`)，全部提至模块顶部 |
| `backend/sentinel/topology_sentinel.py` | **P0 修复** | 完全剥离对 `docker` 和 `docker compose` CLI 的依赖，引入 `tcp://docker-proxy:2375` HTTP API 从而修复不断崩溃/重启的顽疾 (符合 ADR 0006/法典 §7.2) |
| `installer/main.py` / `deployer.py` / `bootstrap.py` | **P0 修复** | 撤销暴力杀光容器的停机部署流程，落地 `Detect -> Adopt -> Retry` 实现基于原生 `compose` 的零停机更新 (ADR 0018) |

---

## 一、最新一轮高可用全栈加固变更 (HA Hardening)

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `scripts/watchdog.py` | 重构 | 全面由 Docker CLI 挂载升级为 `TCP proxy` 驱动 (ADR 0006)，新增 1h 5 次防风暴熔断机制 |
| `docker-compose.yml` | 修改 | 为全部 16 个服务补齐 `healthcheck`, `stop_grace_period`, `start_period` |
| `docker-compose.yml` | 修改 | 修正 Caddy `:2019/config/` 管理员探针；Loki/Promtail 等 Scratch 镜像改用 `service_started` |
| `docker-compose.yml` | 修改 | 为 Docker-Proxy 配置 `POST=1` 权限及 OOM 免死金牌，放行 Watchdog 重启权利 |
| `scripts/update.py` | 新增 | 实现零停机滚动更新：多源拉取 → DB 互斥锁迁移 → remove-orphans 重建 → 健康度 15 次查验 |
| `installer/main.py` | 修改 | 将 Docker 启动的检查时限提升至 15 次，覆盖所有新加探针冷启动耗时 |

---

## 二、架构首轮合规变更文件清单

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `config/Caddyfile` | 修改 | SSE 反缓冲、安全响应头、OpenAPI 反代 |
| `scripts/compiler.py` | 修改 | gateway/redis 增加 ulimits、oom_score_adj |
| `scripts/templates/docker-compose.yml.j2` | 修改 | 输出 ulimits_block、oom_score_adj_block |
| `scripts/bootstrap.py` | 修改 | NTP 预检（漂移 >1s 拒绝启动） |
| `docker-compose.yml` | 生成 | 由 compiler 重新生成（含 ulimits/oom_score_adj） |
| `docs/adr/0001-topology-sentinel-redis-client.md` | 删除 | 与 0001-implement-iac 编号冲突 |
| `docs/adr/0005-topology-sentinel-redis-client.md` | 新增 | 探针 redis-py 决策，编号改为 0005 |
| `docs/ops/docker-daemon.md` | 新增 | Docker 网段与句柄运维说明 |
| `scripts/export_openapi.py` | 新增 | 导出 OpenAPI 规范到 docs/openapi.json |
| `docs/openapi.json` | 生成 | 由 export_openapi.py 生成，纳入版本控制 |
| **scripts/compiler.py** | 修改 | gateway/redis 增加 healthcheck_block（法典 3.4） |
| **scripts/templates/docker-compose.yml.j2** | 修改 | 输出 healthcheck_block |
| **backend/main.py** | 修改 | 请求体大小限制中间件 MAX_REQUEST_BODY_BYTES（法典 7） |
| **backend/alembic/env.py** | 修改 | upgrade 前申请 Redis DB_MIGRATION_LOCK（法典 3.5） |
| **frontend/src/views/SystemSettings.vue** | 修改 | 删除所有硬编码控制阵列，改用获取后端拉取的动态标签渲染，强制执行无状态配置（法典 2.3） |
| **backend/api/routes.py** | 修改 | GET /switches 将解析 env (SWITCH_CONTAINER_MAP) 动态推流 `label` 赋能前端渲染（法典 2.3） |
| **backend/sentinel/topology_sentinel.py** | 修改 | get_uuid 改用 findmnt + blkid（法典 3.2） |
| **backend/api/routes.py** | 修改 | 移除 docker.sock 依赖，改用 Redis PubSub (switch:events) 发布状态（法典 1.1 与 5.2.1） |
| **backend/sentinel/topology_sentinel.py** | 修改 | 新增 _redis_listener_thread 监听并执行 docker pause，强制 3 秒硬超时与 SIGKILL（法典 1.3） |
| **docs/ops/three-step-meltdown.md** | 新增 | 三步熔断顺序及网络层摘除运维说明（法典 3.1） |
| **system.yaml** | 修改 | 新增 sentinel.mount_container_map、sentinel.watch_targets（路径解耦） |
| **scripts/compiler.py** | 修改 | prepare_env 输出 media_path、bitrot_scan_dirs、mount_container_map、watch_targets |
| **scripts/templates/.env.j2** | 修改 | 增加 MEDIA_PATH、BITROT_SCAN_DIRS、MOUNT_CONTAINER_MAP、WATCH_TARGETS |
| **backend/sentinel/topology_sentinel.py** | 修改 | CONTAINER_MAP 仅从 MOUNT_CONTAINER_MAP env 读取 |
| **backend/sentinel.py** | 修改 | WATCH_TARGETS 仅从 WATCH_TARGETS env 读取 |
| **backend/main.py** | 修改 | BITROT_SCAN_DIRS 仅从 env 读取 |
| **backend/api/assets.py** | 修改 | MEDIA_PATH 仅从 env，空时 503 |
| **backend/api/settings.py** | 修改 | media_path 仅从 env，空时 status not_configured |
| **backend/worker/mqtt_worker.py** | 修改 | get_media_path fallback 仅从 env，空时跳过保存 |
| **backend/models/feature_flag.py** | 修改 | 种子默认路径从 MEDIA_PATH env 读取 |
| **backend/main.py** | 修改 | 冷启动 All-OFF 矩阵 + X-ZEN70-Bus-Status（法典 3.2.5） |
| **tests/integration/test_hardware_failure.py** | 修改 | 新增 test_503_meltdown_when_capability_pending（法典 5.1.1） |
| **backend/worker/mqtt_worker.py** | 修改 | 目录日期改为 utcnow（法典 2.4.2） |
| **docs/ARCHITECTURE_CHECKPOINTS.md** | 修改 | 2.4.2 / 3.2.5 / 5.1.1 状态更新为 ✅ |
| **config/Caddyfile** | 修改 | 增加 Content-Security-Policy（法典 2.2.3） |
| **docs/ops/docker-daemon.md** | 修改 | 新增 §3 系统盘 95% 熔断运维说明（法典 4.5） |
| **.pre-commit-config.yaml** | 新增 | black/isort/flake8 门禁（法典 5.2.2） |
| **backend/requirements-dev.txt** | 修改 | 增加 factory_boy>=3.3.0（法典 5.1.2） |
| **backend/tests/factories.py** | 新增 | AlertPayloadFactory、MockUserFactory |
| **backend/tests/unit/test_alert_manager.py** | 修改 | 使用工厂生成 payload 与 mock_user |
| **.github/workflows/compliance.yml** | 新增 | CI 门禁：black/isort/flake8 + backend 单元测试（法典 5.2.1/5.2.2） |
| **docs/ops/cron-gc-restic.md** | 新增 | 3.7 全域 GC / Restic / 豁免运维示例 |
| **scripts/release.sh** | 修改 | 注释注明禁止人工打 Tag（法典 5.2.3） |

---

## 二、按法典条款的变更说明

### 2.1 SSE 代理层反缓冲（法典 2.1）

- **文件**：`config/Caddyfile`
- **内容**：为 `/api/v1/stream*`、`/api/v1/events*` 单独 `handle`，设置 `header X-Accel-Buffering no` 后 `reverse_proxy gateway:8000`，避免代理缓冲导致 SSE 延迟。

### 2.2 安全响应头与 OpenAPI（法典 2.2）

- **文件**：`config/Caddyfile`
- **内容**：
  - 全局响应头：`X-Content-Type-Options: nosniff`、`Strict-Transport-Security: max-age=31536000; includeSubDomains`、`Referrer-Policy: strict-origin-when-cross-origin`。
  - `handle /openapi.json` 反代到 gateway，保证 `/openapi.json` 可访问。
- **文件**：`scripts/export_openapi.py`、`docs/openapi.json`
- **内容**：脚本从网关应用导出 OpenAPI JSON，写入 `docs/openapi.json`，满足「纳入版本控制」。

### 2.4 NTP 同步预检（法典 2.4）

- **文件**：`scripts/bootstrap.py`
- **内容**：在 `run_precheck()` 中增加 `_run_ntp_precheck()`；使用 `ntplib` 向 `0.pool.ntp.org`、`time.cloudflare.com` 请求，漂移 >1s 则 `sys.exit(1)`；无 `ntplib` 或全部 NTP 不可达时仅 WARN 并继续（兼容离线）。

### 3.4 安全容器 healthcheck（法典 3.4）

- **文件**：`scripts/compiler.py`、`scripts/templates/docker-compose.yml.j2`
- **内容**：对 `gateway` 注入 healthcheck（`python -c "urllib.request.urlopen('http://127.0.0.1:8000/health')"`），对 `redis` 注入 `redis-cli ping`；支持 system.yaml 中自定义 `healthcheck.test` 等。

### 3.2 探针 UUID 核验使用原生命令（法典 3.2）

- **文件**：`backend/sentinel/topology_sentinel.py`
- **内容**：`MountPoint.get_uuid()` 改为先用 **findmnt -n -o SOURCE --target &lt;path&gt;** 取挂载点对应设备，再用 **blkid -s UUID -o value &lt;device&gt;** 取 UUID，满足「核验 UUID 必须调用 Linux 原生命令（blkid/findmnt）」。

### 3.3 核心容器 ulimits 与 OOM 豁免（法典 3.3）

- **文件**：`scripts/compiler.py`、`scripts/templates/docker-compose.yml.j2`
- **内容**：对 `gateway`、`redis` 注入 `ulimits: nofile: 65536:65536`、`oom_score_adj: -999`；由 compiler 生成 compose 时写入。

### 3.3 Docker 网段与宿主机句柄（法典 3.3）

- **文件**：`docs/ops/docker-daemon.md`
- **内容**：运维说明：宿主机 `daemon.json` 配置 `default-address-pools`、`/etc/security/limits.conf` 的 nofile 等，避免与局域网/VPN 碰撞及句柄耗尽。

### 3.5 Alembic 迁移锁（法典 3.5）

- **内容**：`run_migrations_online()` 执行前向 Redis 申请全局锁 `zen70:DB_MIGRATION_LOCK`，持有最多 3600s，阻塞最多 120s；执行完毕后释放。环境变量 `SKIP_DB_MIGRATION_LOCK=1` 时跳过（离线/单节点可选用）。

### 2.3 Schema-Driven UI 与无代码硬编码渲染（V2.1 架构升维）

- **文件**：`frontend/src/views/SystemSettings.vue`、`backend/api/routes.py`
- **内容**：严格执行“后端驱动一切（IaC）”的红线规范：
  - 前端：删除了原有的 `swLabels` 写死字典，UI 彻底降维为纯展示组件。
  - 后端：在 `GET /api/v1/switches` 接口中，反向解构由 `compiler.py` 透传的 `system.yaml` 编译环境变量 `SWITCH_CONTAINER_MAP`。并在给前端下发的响应体中自动拼接生成 `label`。
  - 核心增益：现在若运维在 `yaml` 添加新的硬件开关，系统编译后**无需前端介入改代码、打包**，控制面板就能自动长出新的管控开关。

### 7. API 请求体大小限制（法典 7）

- **文件**：`backend/main.py`
- **内容**：新增中间件 `limit_request_body`，对带 `Content-Length` 的 POST/PUT/PATCH 请求，超过 `MAX_REQUEST_BODY_BYTES`（默认 10MB，可由 `MAX_REQUEST_BODY_BYTES` 覆盖）返回 413，错误码 `ZEN-REQ-413`。

### 9. 三步熔断与网络层摘除（法典 3.1）

- **文件**：`docs/ops/three-step-meltdown.md`
- **内容**：说明 API 层 503 → 网络层摘除（Caddy 摘路由）→ 容器级降级的顺序；并给出 Caddy 摘除/恢复 `/api/*` 的运维示例（改 Caddyfile + reload），当前网络层摘除需手动或脚本执行。

### 路径解耦（法典 1.2：IaC 唯一事实来源，禁止代码硬编码路径）

- **system.yaml**：新增 `sentinel.mount_container_map`、`sentinel.watch_targets`；`capabilities.storage.media_path` 已存在。
- **compiler**：`prepare_env()` 从 config 读出上述路径与映射，写入 `.env`（MEDIA_PATH、BITROT_SCAN_DIRS、MOUNT_CONTAINER_MAP、WATCH_TARGETS）。
- **探针**：`topology_sentinel.CONTAINER_MAP`、`sentinel.WATCH_TARGETS` 仅从 env 加载，无默认硬编码。
- **网关/API**：BITROT_SCAN_DIRS、MEDIA_PATH 仅从 env；上传/磁盘信息在 MEDIA_PATH 未配置时返回 503 或 not_configured。
- **worker/feature_flag**：媒体路径 fallback 或种子默认值仅来自 env。

### 11. ADR 编号规范（法典 6）

- **文件**：`docs/adr/`
- **内容**：原 `0001-topology-sentinel-redis-client.md` 与 `0001-implement-iac-with-python-compiler.md` 编号冲突；将「探针 redis-py」ADR 重编号为 **0005**，删除旧 0001 副本，新增 `0005-topology-sentinel-redis-client.md`。

### 12. 冷启动 Redis 失联 All-OFF（法典 3.2.5）

- **文件**：`backend/main.py`
- **内容**：Redis 不可用且无 LRU 缓存时，`get_capabilities_matrix` 返回硬编码 **ALL_OFF_MATRIX**（ups/network/gpu 均为 offline，reason 为「总线未就绪」）；`/api/v1/capabilities` 在该情况下返回 200 且响应头 **X-ZEN70-Bus-Status: not-ready**，前端可据此展示「总线未就绪」告警。

### 13. 集成测试显式断言 503（法典 5.1.1）

- **文件**：`tests/integration/test_hardware_failure.py`
- **内容**：~~新增 **test_503_meltdown_when_capability_pending**：通过 Redis 设置 `zen70:topology:media_engine=PENDING_MAINTENANCE`，等待网关 LRU 缓存过期后请求 `GET /api/v1/media/status`，显式断言 **status_code == 503** 且 **code == ZEN-STOR-1001**。~~ _v3.43: media 路由已从 Kernel 下架，该测试已删除。503 熔断验证已由 `test_three_step_meltdown_api_first` 覆盖。_

### 14. 时区 UTC 统一（法典 2.4.2）

- **文件**：`backend/worker/mqtt_worker.py`、`docs/ARCHITECTURE_CHECKPOINTS.md`
- **内容**：mqtt_worker 中 Frigate 快照目录日期由 `datetime.now()` 改为 **datetime.utcnow()**；检查点 2.4.2、3.2.5、5.1.1 状态更新为 ✅。

### 15. 安全头 CSP 与检查点续查（法典 2.2、4.4、4.5、5.2.2）

- **config/Caddyfile**：增加 **Content-Security-Policy**（default-src 'self'；script/style/img/connect 适度放宽以兼容 PWA/流媒体）；检查点 2.2.3 已涵盖 CSP。
- **docs/ops/docker-daemon.md**：新增 **§3 系统盘 95% 熔断**：说明由监控/探针采集、告警后按三步熔断与 pause 下发的运维建议；检查点 4.5 说明中引用该节。
- **docs/ARCHITECTURE_CHECKPOINTS.md**：4.4 标为 ✅（8s 超时与 206 截断已实现）；4.5 说明补充运维文档引用；5.2.2 说明补充 .pre-commit-config.yaml。
- **.pre-commit-config.yaml**：新增 black、isort、flake8 门禁，限定 `backend/`，便于本地与 CI 执行。

### 16. factory_boy 测试数据工厂（法典 5.1.2）

- **backend/requirements-dev.txt**：增加 **factory_boy>=3.3.0**。
- **backend/tests/factories.py**：新增 **AlertPayloadFactory**（AlertPayload）、**MockUserFactory**（JWT user 字典），供单元/集成测试复用。
- **backend/tests/unit/test_alert_manager.py**：`mock_user` fixture 与两处 `AlertPayload` 改为由工厂生成，无硬编码字面量。
- **docs/ARCHITECTURE_CHECKPOINTS.md**：5.1.2 说明更新为「已引入 factories，test_alert_manager 已改用工厂；其余测试待推广」。

### 17. CI 合规工作流与 3.7 运维说明（法典 5.2、3.7）

- **.github/workflows/compliance.yml**：新增 Compliance 工作流，在 push/PR 到 main|master 时执行：安装 backend 依赖、**black / isort / flake8** 检查、**backend 单元测试**；满足 5.2.2 代码规范门禁，并为 5.2.1 提供流水线入口（Trivy/audit 可后续追加）。
- **docs/ops/cron-gc-restic.md**：新增 3.7 运维示例：容器 GC（含 zen70.gc.keep 豁免）、Restic forget --prune、PostgreSQL/应用级清理、不可删除区域；检查点 3.7.x 说明引用该文档。
- **scripts/release.sh**：顶部注释注明「禁止人工打 Tag，必须通过本脚本或 CI 自动打 Tag，遵循 Conventional Commits」。
- **docs/ARCHITECTURE_CHECKPOINTS.md**：5.2.2 标为 ✅（CI 已跑 black/isort/flake8）；5.2.1/5.2.3、3.7.x 说明更新。

### 18. 全局无冲突审计与 O(N) 性能肃清 (V3.0 升级)

- **backend/workers/media_watcher.py**：多容器横向扩容 (HPA) 下，针对未处理资产引入 Postgres 原生悲观行锁 `.with_for_update(skip_locked=True)`，彻底消灭多个扫描守护进程并发拉取同一任务引发的冲突。
- **backend/workers/iot_bridge.py**：修复 `paho-mqtt` 异步跨线程抛出 `RuntimeError` 的史诗级缺陷，由 `asyncio.get_event_loop()` 迁移至显式挂载主循环，并经由 `run_coroutine_threadsafe(..., self.loop)` 安全注入。
- **backend/api/iot.py**：抹除了导致网络 IO 堵塞风暴的串行 N+1 `redis.get` 循环，重构成了一次性吞吐的 `redis.mget` 批量管道获取（O(1) 性能），微压榨接口长连接。
- **全域守护脚本**：执行严格的静态抽象语法树 (AST) 清除，在 `assets.py`、`push.py` 等文件中清除了大量的未使用或冗余 Import (`torch`, `sys`, `time`, `json`)，强制削减微服务的初始驻留物理内存。

### 19. main.py 单体拆分 (BR-4)（法典 8.2：零省略号重构、SRP）

### 20. 动态渲染 JSON 网关降级闭环（法典 3.2.5、ADR 0009）

- **文件**：`backend/api/routes.py`、`backend/api/models/__init__.py`
- **内容**：恢复了 `/api/v1/capabilities` 获取 `get_capabilities_matrix()` 的完整逻辑，弃用了硬过滤为 `{}` 的幽灵逻辑，切实保障在 Redis 宕机时，前端能够收到含 `reason` 的 `ALL_OFF_MATRIX` 安全降级矩阵（法典 3.2.5）。同时，修复了 `CapabilityResponse` Pydantic 模型的强校验缺失（`endpoint: Optional[str]`、`enabled: bool`），确保完全符合【ADR 0009 契约驱动】原则，杜绝 500 序列化报错。

### 21. Redis Client 防腐代理（法典 2.5、8.2）

- **文件**：`backend/core/redis_client.py`、`backend/api/auth.py`
- **内容**：遵循 `优雅启停与防腐代理` 设计，拒绝在 `auth.py` 等高层业务代码中直接暴露原生 `redis_client._redis.get()` 调用。通过在 `RedisClient` 类显式声明 `get/set/setex/delete/incr/expire` 的异步代理方法，拦截可能未初始化的 `None` 调用并返回安全默认值 `0/False/None`。彻底拦截并修复了密码与 PIN 码登录认证时因 `AttributeError` 引发的系统 500 崩溃，并在防爆破限制上严格合规（法典 3.6 WebAuthn 降级）。

- **backend/main.py**（1057 → 144 行）：精简为 App 工厂 + Lifespan，仅负责日志初始化、中间件/路由注册、异常处理器绑定。
- **backend/capabilities.py**（新增 ~250 行）：能力矩阵 + Redis 拓扑读取 + LRU 缓存 + FeatureFlag 双闸门。
- **backend/middleware.py**（新增 ~230 行）：RequestID 注入、UPS 全局只读熔断、请求体大小限制、成功响应 Envelope 包装。
- **backend/background_tasks.py**（新增 ~170 行）：Bit-Rot 静默巡检 + SRE 微服务探针（Liveness/Readiness）。
- **backend/gateway_routes.py**（新增 ~270 行）：网关路由（capabilities/SSE/health/shred）+ 异常处理器。
- **backend/shared_state.py**（新增 ~24 行）：共享运行时状态（service_readiness / service_liveness_fails），消除循环依赖。
- **backend/ai_router.py**：import 路径更新（`backend.main` → `backend.capabilities`）。
- **backend/tests/unit/test_capabilities_matrix.py**：mock 路径更新。

### 20. 封装泄漏修复（法典 8.2：模块间仅走公有 API）

- **capabilities.py**：新增 `get_lru_matrix()` 公有访问器，替代 middleware.py 直引 `_lru_cache` 私有变量。
- **capabilities.py**：新增 `is_redis_available()` 公有访问器，替代 gateway_routes.py 重复 `import redis.asyncio`。
- **shared_state.py**：集中管理 `service_readiness` 和 `service_liveness_fails`，替代 middleware.py 中的延迟导入 hack。

### 21. 架构修复 A-1～A-5（法典 1.2/2.5/3.2/8.2）

- **A-1 Redis 连接池统一**（法典 1.2：工业级成熟方案）：消除 `capabilities.py` 中 per-request `redis.Redis()` 短连接，全面统一到 `app.state.redis`（RedisClient 连接池）。涉及 `capabilities.py`、`middleware.py`、`gateway_routes.py`、`background_tasks.py`、`main.py`。
- **A-2 上帝函数拆解**（法典 8.2：SRP）：`get_capabilities_matrix()` 拆为 `fetch_topology()` (纯 I/O) + `_read_feature_flags()` (纯 I/O, pipeline 批量) + `build_matrix()` (纯转换，无 I/O，可直接单元测试)。
- **A-3 SQLite async 改造**（法典 2.5：asyncio 非阻塞）：Bit-Rot 巡检的 `sqlite3.connect()` + `cursor.execute()` + SHA256 hashing 全部移入 `asyncio.to_thread()`，消除同步阻塞事件循环。
- **A-4 中间件执行顺序注释**（法典 8.1：工程级注释）：main.py 中间件注册块添加 Starlette 洋葱模型执行顺序注释（外→内：`add_request_id` → `success_envelope` → `limit_request_body` → `global_readonly_lock` → 路由）。
- **A-5 异常处理器去重**：gateway_routes.py 合并为单一 `http_exception_handler`，消除重复注册覆盖。

### 22. 文档架构瘦身 (D-1～D-4)

- **D-1**：历史架构正文曾归档至 `docs/archive/`，后续已删除并并入当前权威正文。
- **D-2**：删除 `docs/zen70_v3.0/` 整个历史文档包（业务说明、部署指南、全量文档与索引），改为只保留上游权威文档。
- **D-3**：新增 `docs/CHANGELOG.md`（Keep a Changelog 格式，覆盖 V1.58 → V2.9 → V3.0 → Unreleased）。
- **D-4**：更新 `docs/INDEX.md`（新增 CHANGELOG/CANON_COMPLIANCE 条目，archive/ 路径更新，第七节指向 `docs/archive/`）。

### 23. pgvector 迁移全链路修复（法典 §1.1、§3.5）

- **system.yaml**：PostgreSQL 镜像从 `postgres:15-alpine` 切换至 `pgvector/pgvector:pg15`，确保 `CREATE EXTENSION vector` 可用。
- **alembic/versions/8c2f1a6b4d10_*.py**：新增 `Vector(UserDefinedType)` 类定义，替代不存在的 `sa.dialects.postgresql.VECTOR(512)`。
- **alembic/versions/0b6c9c3f1a21_*.py**：同上，替代 `VECTOR(384)` 和 `VECTOR(512)` 两处引用。
- **requirements.txt**：补齐 `PyJWT>=2.8.0`、`python-dotenv>=1.0.0`，解决 Alembic `env.py` 的 `ModuleNotFoundError`。
- **结果**：4 个 Alembic 迁移（`7b9fa39e00a0` → `8c2f1a6b4d10` → `0b6c9c3f1a21` → `4a2d9e9b7c11`）全部成功，共创建 8 张数据库表。

### 24. IaC 管线审计与已知缺陷登记

以下为本次部署调试中发现但**尚未修复**的 `compiler.py` 管线缺陷，已登记到 `CHANGELOG.md [Unreleased] → Known Issues`：

| 缺陷 | 根因位置 | 严重度 |
|:---|:---|:---|
| `POSTGRES_DSN` 从未由 compiler 生成 | `prepare_env()` + `.env.j2` | P1 |
| `redis_host`、`redis_user` 硬编码 | `prepare_env()` L324/L327 | P1 |
| `token_urlsafe()` 密码含 URI 保留字符 | `secrets_manager.py` L119 | P1 |
| JWT 双轨轮转不执行真降级 | `secrets_manager.py` L125 | P1 |
| `users.acl` 含 `#` 注释致 Redis 崩溃 | `compiler.py` L446 | P0 已知根因 |
| PgBouncer SCRAM/MD5 认证不匹配 | 配置层 | P1 |

---

## 三、未改代码的合规项（已核对）

以下条款在现有代码中已满足，本次仅做核对：

- **X-Request-ID**：`backend/main.py`、`api/main.py` 中间件注入并回写响应头。
- **统一错误码 ZEN-xxx + recovery_hint**：`auth_helpers.zen()`、`ai_router`、`main.py` 等已用统一契约。
- **结构化日志**：`core/structured_logging.py` JSON 格式化，含 request_id/caller/level。
- **503 后前端不无限轮询**：`frontend/src/utils/http.ts` 断路器 15s 冷却。
- **Lifespan 优雅启停**：`main.py`、`api/main.py` 中关闭任务与 Redis。
- **目录预建 + chown 1000:1000**：`bootstrap.py` 预建挂载卷并 chown。
- **swapoff -a**：`bootstrap.py` 在 Linux 下已执行。
- **--remove-orphans**：`bootstrap.py`、`deployer.py` 使用 `docker compose up -d --remove-orphans`。

---

## 四、可选依赖

- **NTP 预检**：`scripts/bootstrap.py` 的 NTP 预检使用可选依赖 `ntplib`。若需在离线环境跳过预检，可保留当前「不可达则 WARN 并继续」行为；若需强制预检，可 `pip install ntplib` 或在项目/运维文档中说明。

---

*文档生成后请随发布更新；所有变更均对应 .cursorrules V2.0 绝对零度版。*

---

## 五、IaC 编译器工业级加固 (2026-03-20)

### 23. 编译器安全与合规 (法典 §1.2 / §3.4)

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `scripts/compiler/secrets_manager.py` | 修改 | URL-safe 密码生成（过滤 `@:/%?#`）；`POSTGRES_DSN` 自动构造注入；JWT `--rotate-jwt` 真轮转 |
| `scripts/compiler/lint.py` | 重写 | 51→160 行 Schema 强校验（必填字段检查，缺少关键字段 exit(1)）|
| `scripts/compiler.py` | 修改 | 去硬编码（`redis_host`/`postgres_host` 从 system.yaml 读取）；`--dry-run` 预览模式；`_dict_to_yaml_block()` 结构化 YAML；Redis ACL 零注释；emoji 去除（Windows GBK 兼容）；Healthcheck 极简镜像适配 |
| `scripts/templates/.env.j2` | 修改 | 新增 `POSTGRES_DSN` 变量 |
| `backend/alembic/versions/*.py` | 修改 | 2 个迁移脚本添加 `@generated by ZEN70-AI-Agent` 溯源头 |
| `system.yaml` | 修改 | 版本 `1.0` → `2.0`（消除 config-lint WARN）|

**合规覆盖**：
- §1.2 IaC 唯一事实来源：`POSTGRES_DSN` 自动管理 + 去硬编码 + Schema 校验 → ⚠️ 升为 ✅
- §3.4 JWT 双轨轮转：`--rotate-jwt` CURRENT→PREVIOUS 降级 → ⚠️ 升为 ✅
- §8.2 溯源头注入：迁移脚本 `@generated` 标头

---

### 24. Caddy 路由与 SW 防投毒 (法典 §2.1 / §3.6)

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `scripts/templates/Caddyfile.j2` | 重写 | `@api`/`@sse` 命名匹配器 + `handle` 互斥路由块，确保 API/SSE 优先于 SPA `try_files` fallback |
| `frontend/vite.config.ts` | 修改 | SW `navigateFallbackDenylist: [/^\/api/]`；API 缓存添加 `headers: {'content-type': 'application/json'}` 验证 |
| `frontend/dist/clear-cache.html` | 新增 | 一次性缓存清理页面（清除 SW 投毒残留）|

**根因**：旧 Caddyfile 中 `try_files {path} /index.html` 在顶层未被 API 路由拦截，导致 `/api/*` 请求返回 `index.html`（HTML）。Service Worker 的 `CacheableResponsePlugin({statuses:[0,200]})` 缓存了这些 HTML 响应，形成**缓存投毒**。

**三层修复**：
1. Caddy 层：`@matcher` + `handle` 互斥路由
2. SW 层：`navigateFallbackDenylist` + Content-Type 验证
3. 缓存层：`clear-cache.html` 批量清除旧缓存

**合规覆盖**：
- §2.1 SSE 代理层反缓冲：`X-Accel-Buffering: no` + 独立 `@sse` 路由
- §3.6 PWA 离线生存指南：SW 防投毒策略

---

## 六、测试体系重建全绿与前后端契约打通闭环 (2026-03-22)

### 25. 测试体系完备 (法典 §5.1.1)

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `backend/tests/unit/` | 新增 | 补充 `test_deps.py`, `test_jwt_core.py`, `test_errors.py`, `test_policy_engine.py` 等 11 个测试文件 |
| `backend/api/deps.py` | **P1 修复** | 生产 Bug：路由鉴权时 `HTTPException` 未捕获导致 500 崩溃，改为 `except Exception as e: raise zen(...)` |

**合规覆盖**：
- §5.1 单元测试：新增 105 个测试并全部通过（1.66s），使得 `C-501` 从局部覆盖（⚠️）升格为完全合规（✅）。

---

### 26. IaC 编译器三层分离落地 (法典 §1.2 / ADR 0011)

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `iac/policy/core.yaml` | 新增/修改 | 从 lint.py 硬编码中抽离出 10 条业务与安全红线（版本升级至 v2），支持 Tier3 (推荐) 级别，构成**独立策略层**。 |
| `scripts/iac_core/policy.py` | 重构 | 加载策略引擎验证规则，新增 `policy_version` 断言防降级，隔离编译器渲染逻辑与红线定义。 |
| `scripts/compiler.py` | 修改 | 每轮渲染并在完全无违规（0 fails/Warnings 降级）后，主动生成并注入 `render-manifest.json` 溯源日志，保留注入审计的防篡改指纹。 |

**合规覆盖**：
- 落实 **ADR 0011**，彻底将 IaC 系统切分为：`system.yaml`（事实陈述层）-> `core.yaml`（SRE 控制平面）-> `compiler.py`（编译器范化输出引擎）。

---

### 27. 统一 Axios 拦截器强制闭环 (法典 §3.1 / ADR 0015)

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `frontend/src/stores/auth.ts` | **P0 修复** | 截断 `updateAiPreference()` 中危险的原生 `fetch()`，替换为包装后的 `http.patch()`。 |
| `frontend/src/views/SystemSettings.vue` | 修改 | 修复 TS2322，`d.message ?? '操作成功'` 类型窄化。 |

**根因**：使用了浏览器的原生 `fetch` API，导致客户端请求直接绕过了应用预设的 4 重防火墙（X-Request-ID 附着、X-New-Token 吸星更新、503 API 电路熔断器退避、Envelope 数据解包）。
**影响**：断网重连与 JWT 到期换证时会爆发出难以预料的脱轨，直接瓦解了高可用架构在前端的闭环。已通过发布 **ADR 0015** 彻底封杀所有生产请求层的此类逃逸行为。



---

## 七、Mypy 全栈类型严格审查与零错误合规 (2026-03-23)

### 28. 类型抑制指令与注释防遮挡规范 (法典 1.1 / ADR 0019)

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| ackend/workers/iot_bridge.py | **P0 修复** | 移除双重注释遮挡，将 # type: ignore[union-attr] 移置中文注释之前，恢复 mypy 检查有效性 |
| ackend/api/media.py | **P1 修复** | 同上，修复 # type: ignore[attr-defined] 遮挡 |
| ackend/tests/unit/test_board.py | **P1 修复** | 同上，修复 # type: ignore[call-arg] 遮挡 |
| ackend/api/assets.py | **P2 修复** | 合并双重抑制码为 # type: ignore[arg-type, type-var] |
| 全域后端源文件 (106个) | 清扫 | 批量补齐 AST 级别的多行函数 -> None 签名注解，消除掩盖报错的冗余 unused-ignore |
| docs/adr/0019-mypy-type-ignore-comments-policy.md | 新增 | 确立 ADR 0019 规范：类型抑制指令必须为行内首个注释 |

**合规覆盖**：
- 落实 **ADR 0019**：明确了 mypy 在处理中文后缀注释时的贪婪解析陷阱，确立了 
esult = func()  # type: ignore[xxx]  # 中文注释 的安全书写红线。
- 遵循法典 **1.1 技术选型红线**与绝对强类型约束，正式实现后端代码 **零 Mypy Errors**（从 415 降至 0）。彻底封杀了任何透过不规范抑制或换行错位掩盖缺陷的取巧做法。


---

## 八、SRE 终极防线筑基与深水区网络防劫持 (2026-03-23)

### 29. 隧道网络层防劫持逃生舱 (法典 1.2 / 3.6)
| 文件 | 变更类型 | 说明 |
|------|----------|------|
| \system.yaml\ | **P0 修复** | 针对 \cloudflared\ 容器强制追加 \--protocol http2\ 启动指令，并硬编码挂载 \dns: [1.1.1.1, 8.8.8.8]\ 防污染 DNS。 |

**根因**：国内环境及运行 TUN 代理（如 Clash/V2ray 且开启 Fake-IP \198.18.x.x\）的宿主机常常会对 QUIC (UDP 443) 协议进行穿透拦截，导致 Cloudflare 节点握手 EOF 崩溃。
**合规覆盖**：通过协议降级 TCP (HTTP/2) 并剥离宿主机被污染的 DNS 请求栈，实现了隧道组件对极端网络审查的免疫。

### 30. SRE 及原生探针与防爆盘隔离 (法典 3.4 / 5.1.4)
| 文件 | 变更类型 | 说明 |
|------|----------|------|
| \system.yaml\ | 架构升维 | 为全部 16 个容器覆盖式增补 \healthcheck\。PG/Redis 使用原生探针，前台 Caddy 扫 /config，后台 Sentinel/Watchdog 利用 Linux 内核 \/proc/1/cmdline\ 断言全路径，根除假死。 |
| \system.yaml\ | 架构升维 | 为涉存储网关/节点追加 \stop_grace_period: 30s\ 与 \logging: max-size 10m\，杜绝停机撕裂与物理盘 100% 爆棚。 |

**跨文档双向验证**：此更新完美补齐了 **ADR 0018** 的前置条件。依赖真实的 Healthcheck 与 30s 优雅宽限期，\docker compose up -d\ 得以在新容器已真实就绪 + 老数据已安全落盘的绝对安全区间进行容器替换切换，达成无缝的零停机重载。

### 31. Bit-Rot 巡检内核库自保护 (法典 3.2.3)
| 文件 | 变更类型 | 说明 |
|------|----------|------|
| \ackend/sentinel/data_integrity.py\ | 重构 | 将 CPU 负载熔断阈值等硬编码抽离为环境变量（利用 \_read_positive_float_env\ 守护）；对底层分析 SQLite 注入 \usy_timeout=5000\，彻底免疫大批量巡检触发的 DB 互斥死锁；加入了 \psutil\ 缺失捕捉实现降权运行。 |

### 32. 核心鉴权链路与会话审计升维 (法典 3.4 / 3.6)
| 文件 | 变更类型 | 说明 |
|------|----------|------|
| \ackend/api/auth.py\ | 架构升维 | 新增基于 Redis Stream (\AUTH_AUDIT_STREAM_KEY\) 的轻量级、无感、不阻塞主流程的认证审计流（涵盖 login, reset, invite 等行为网络及 IP 溯源）。 |
| \ackend/api/auth.py\ | 架构升维 | 新增 WebAuthn 登录设备 \last_used_at\ 与 \last_used_ip\ 的轨迹追踪，以及 \/audit/events\ 与 \/users/{id}/devices\ 管理员端点。 |
| \ackend/api/deps.py\ | **P0 修复** | 抽离出 \get_redis_required\ 强校验依赖。 |
| \ackend/api/(iot, scenes, scheduler).py\ | **P0 修复** | 批量从弱 \get_redis\ 迁移至 \get_redis_required\，防范 \AttributeError\ 造成的 500 崩溃，在 Redis 断连时实现优雅的 503 降级。 |

**合规覆盖**：实现了 SRE 级别的侧信道审计，不仅符合零信任网络的轨迹留存，更通过隔离层 \get_redis_required\ 斩断了底层 NoneType 穿透业务层的可能。已配套发布 **ADR 0020** 决策档案。

### 33. 前端全域拦截器接管与无感刷新 (法典 §3.1 / ADR 0015)
| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `frontend/src/**/*.vue` & `*.ts` | **P0 修复** | 抹除所有的原生 `fetch()` 调用，全面接入基于 Axios 的 `http` 统一代理对象。 |
| `frontend/src/utils/api.ts` | 架构升维 | 实装 X-New-Token 透明换证响应头捕获，以及 503 熔断指数退避重演。 |

**合规覆盖**：彻底贯彻了 **ADR 0015** 拦截器封杀令，前端应用层对 JWT 轮转和节点瞬断完全免疫。

### 34. 端到端密码重置业务闭环 (法典 §3.4)
| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `frontend/src/views/PasswordResetView.vue` | 功能补全 | 新增了与后台 Redis 临时 Token 握手的前端展示层。 |
| `tests/test_auth_password_reset_contract.py` | 功能补全 | 增加了接口形态的端到端契约保证。 |

### 35. 极致 SRE 全维验收脚手架 (法典 §5.1 / §5.2)
| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `scripts/full_system_20x_qa.py` | SRE 基建 | 构造了覆盖断网、OOM、假死状态的系统级混沌探针。 |
| `tests/test_compliance_sre.py` | SRE 基建 | 将高可用标准显式注册为单元测试。 |

**合规覆盖**：标志着法典规范从“人工约束”升格为“代码自动化准入”，构筑了发版前的最后一道防火墙。

### 36. IaC 编译级 SRE 强制门禁 (法典 §1.2 / §3.3)
| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `scripts/iac_core/lint.py` | 架构升维 | 新增 `_check_cloudflared_contract` 强制校验 `--protocol http2` 防篡改。 |
| `scripts/iac_core/lint.py` | 架构升维 | 新增 `_check_postgres_dsn_target` 强制校验 `services.pgbouncer` 启用时网关 DSN 必须指向 `pgbouncer`。 |
| `scripts/iac_core/lint.py` | 架构升维 | 将密码重置链路新环境变量（`AUTH_AUDIT_STREAM_KEY` 等）硬编码入 Tier 3 门禁警告。 |

**合规覆盖**：将《法典》的纯文本红线转化为 `system.yaml` 编译期的 AST 级阻断器。从此以后，任何人如果忘记配置 HTTP2 或绕过 PgBouncer 直连数据库，配置编译阶段将直接抛出包含明确恢复建议的 `SchemaValidationError` 宕机。

### 37. 前端字典化路由重构 (法典 §1.2 / ADR 0009)
| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `frontend/src/components/VoiceButton.vue` 等 | 规范化 | 完全清除了 `/v1/agent/voice` 等魔术字符串硬编码，对齐到 `API.agent.voice()` 集中字典。 |

**合规覆盖**：贯彻了法典的“前端基于契约字典动态渲染”精神，彻底消灭了 API 路由的字符串漂移风险。

---

## 九、前端 API 全栈契约深度审计与架构治理 (2026-03-24)

### 38. 统一成功响应 Envelope (法典 §2.3 / ADR 0010 修订)
| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `backend/middleware.py` | 架构升维 | 删除了废弃的双轨 `success_envelope`。 |
| `backend/api/main.py` | 架构升维 | 确立此处为 Envelope 唯一事实来源。 |
| `docs/adr/0010-unified-success-envelope.md` | 文档修订 | 将文档契约向实现靠拢：成功响应（`ZEN-OK-0`）中**不再强制包含**对成功无语义的 `recovery_hint` 噪音字段，解决前端解析灾难。 |

**合规覆盖**：彻底解决了单项功能响应不统一导致的前端逻辑断裂与冗余判断风险，修复了 `REPORT_ENVELOPE_FETCH_AUDIT` 揭露的解析差异化问题。

### 39. 全域 Axios HTTP 栈统辖与网络逃逸阻断 (法典 §3.1 / ADR 0015)
| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `frontend/src/views/**/*.vue` | **P0 修复** | 抹除了 6 个核心组件（InviteView, UserManagementCard, FamilyBoard, MediaCenter 等）中残存的原生 `fetch`。 |
| `frontend/src/utils/push.ts` | **P1 修复** | 替换了原生态无保护的 `fetch` 为 `http.get/post`。 |

**合规覆盖**：执行 **ADR 0015** 最高红线。所有请求全部挂载至 `utils/http.ts`。所有端点自动获得 X-Request-ID 注入、401 熔断退避、双轨 JWT X-New-Token 透明换证以及 Envelope 统一解包。封死了通过裸 `fetch` 造成的鉴权追踪状态丢失与网络脱轨漏洞。

### 40. 前端 API 路径常量注册表 SSOT (法典 §1.2 / ADR 0021)
| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `frontend/src/utils/api.ts` | 架构升维 | 统一抽离出 `AUTH`, `BOARD`, `ASSETS`, `SCHEDULER`, `SCENES`, `ENERGY` 等 14 个命名空间 API 常量。 |
| `frontend/src/stores/**/*.ts` | 规范化 | 完全清除了所有的 `/v1/` 硬编码魔术字符串。 |
| `docs/adr/0021-frontend-api-path-constant-registry.md` | 新增 ADR | 确立建立统一 API 路径常量注册表作为单一事实源。 |

**合规覆盖**：彻底清除了前端对后端 API 路径的隐性依赖和随地挂载。前后端达成了严丝合缝的闭环契约拼接：`http.ts BaseURL(/api) + api.ts Constant(/v1/xxx) == FastAPI APIRouter prefix(/api/v1/xxx)`。至此前端侧所有路径字符串皆在常量审查范围内，彻底杜绝路径漂移 404！
