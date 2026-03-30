# Changelog

所有重要变更记录。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [Semantic Versioning](https://semver.org/)。

## Document Control

| 属性 | 值 |
|:---|:---|
| **文档编号** | ZEN70-DOC-CHANGELOG-001 |
| **法典版本** | V2.0 绝对零度版 |
| **最后更新** | 2026-03-21T23:23+08:00 |
| **责任人** | 系统架构组 |
| **审批状态** | 自动化生成 (CI/CD pipeline) |

---

## [Unreleased]

## [3.41] - 2026-03-27

### Fixed (Infrastructure & SRE Compliance)

#### P0 SSE Ping 45s 超时全链路打通 (Client-Token-in-URL + Redis SETEX)

| ID | 组件 | 修复内容 | 法典条款 | 风险等级 | 回滚方案 |
|:---|:---|:---|:---|:---|:---|
| FIX-067 | `api/routes.py` | **P0** SSE 45s 超时全链路打通：新增 `POST /api/v1/events/ping`（JWT 鉴权 + UUID 格式校验 + Redis SETEX 45s）；`sse_events` 支持 `?client_token=` query param + 首包下发 `connection_id` + 主循环 Redis EXISTS 超时检查 + ADR 0013 免死金牌 + finally DEL 清理 | §2.1/§3.2 | P0 | 回退 `sse_events` 旧版逻辑（无 Ping 无超时） |
| FIX-068 | `frontend/src/utils/sse.ts` | **P0** Client-Token-in-URL：`crypto.randomUUID()` 拼入 URL `?client_token=`；Ping 端点从 `/v1/stream/ping` 迁移至 `/v1/events/ping`；`connected` 事件不再作为 Ping 启动的触发条件 | §2.1 | P0 | 回退 `sse.ts` 旧版首包依赖逻辑 |
| FIX-069 | `gateway_routes.py` | **P2** docstring 标记 `@deprecated`，声明 SSE 已统一到 `api/routes.py` | §2.1 | P2 | 移除 deprecation 标记 |
| FIX-070 | `SystemSettings.vue` 等 5 组件 | **P1** ADR 0015 执法：所有 raw `fetch` → `authFetch` 注入熔断器 + X-Request-ID + 统一 token 管理（消除 `localStorage.getItem` 直接取 token） | ADR 0015 | P1 | 回退各组件 fetch 调用 |

#### 安装器 UX 体验跃升与合规重构 (Installer UX & SmartHome Refactoring)

| ID | 组件 | 修复内容 | 法典条款 | 风险等级 | 回滚方案 |
|:---|:---|:---|:---|:---|:---|
| FIX-062 | `SmartHome.vue` | **P1** SSE 重构防内存泄漏；强制依赖 `useAuthStore` 鉴权与 Axios 拦截器解包；清理 `any` 类型 | §2.1/§3.4 | P1 | 回退组件旧版代码 |
| FIX-063 | `start_installer.py` | **P1** 彻底根除 `except:` 裸异常与阻塞调用（`async` 内的 `subprocess.run` 转 `to_thread`），闭合防正则注入 | §7.7/§8.2 | P1 | 回退全量阻塞代码 |
| FIX-064 | `安装器 UI` | **P2** 增强防抖 (anti-double-submit) + 部署计时器 + 失败重试态 + 终端自适应高度 + 自动探测 `/api/v1/capabilities` 完毕后跳车 (Auto-Redirect) | §1.2 | P2 | 还原原始最小化 UI |
| FIX-065 | `Tunnel` / 配置流 | **P1** Cloudflare 隧道 Token 防篡改注入优化，支持可见性切换；修复 Windows 宿主路径解析 `Path` 适配 | §1.2 | P1 | 回退手动输入与裸奔路径 |
| FIX-066 | `部署验证` | **P2** 增加全栈部署状态后端探测 API，打通进度条验证与部署日志导出 (Log Export) | §4.2 | P2 | 关闭验证探测 |

#### 全栈 SRE 深水区代码级合规扫描 (Phase 3 & Phase 4 Deep Hardening)

| ID | 组件 | 修复内容 | 法典条款 | 风险等级 | 回滚方案 |
|:---|:---|:---|:---|:---|:---|
| FIX-067 | `全域 Python` | **P0** 彻底剿灭全库 150 余处 `except Exception` 宽泛捕获，全部精准收窄为实体异常元组（`ValueError`, `OSError`, `KeyError` 等） | §7.7 | P0 | 无（底层硬性合规） |
| FIX-068 | `Frontend` | **P0** 扫描断言前端 `src/` 达成 100% 零 `Any` 强类型全覆盖 | §8.2 | P0 | 无（类型基线） |
| FIX-069 | `Core/Main` | **P0** 断言 CORS `allow_origins=["*"]` 零存在；断言 ADR-0010 Envelope `ZEN-OK-0` 全域包装完毕 | §2.2/§2.3| P0 | 无 |
| FIX-070 | `Workers/DB` | **P1** 断言 `os.getenv` 全域容错兜底 (`or None`), 绝杀无兜底空爆；断言 `subprocess.run` 全量 `timeout` 防止死锁 | §7.X/§3.5 | P1 | 无 |

#### 深度 IaC 预检与全链路强加固 (Deep Hardening Audit V2)

| ID | 组件 | 修复内容 | 法典条款 | 风险等级 | 回滚方案 |
|:---|:---|:---|:---|:---|:---|
| FIX-056 | `system.yaml` | **P0** `watchdog` 环境变量 `COMPOSE_PROJECT_NAME` 清除目录名 `'32'` 泄漏，恢复为 `zen70` | §1.2 | P0 | 回退硬编码 |
| FIX-057 | `bootstrap.py` | **P0** 移除 `cloudflared` 分步启动，统一为单次全量 `up -d --remove-orphans` 防跨 project 冲突 | §3.1/§1.2 | P0 | 回退分步启动 |
| FIX-058 | `system.yaml` | **P0** 修复 Redis `--appendonly yes` 缺少引号导致 YAML 解析为布尔值导致启动崩溃 | §1.2 | P0 | 回退无引号 |
| FIX-059 | `deployer.py` / `bootstrap.py` | **P1** 彻底消灭所有 `env.setdefault("COMPOSE_PROJECT_NAME")` 弱赋值，全部升级为强赋 | §1.2 | P1 | 回退弱赋值 |
| FIX-060 | `deployer.py` / `update.py` | **P1** 补全 `docker compose` 子进程的 `TimeoutExpired`、`rc != 0` 异常捕获与回滚提示 | §8.2 | P1 | 回退静默调用 |
| FIX-061 | `loader.py` | **P2** 增强防御：识别 YAML 解析产生的布尔值 `True`/`False` 并转为安全全小写字符串 `"true"`/`"false"` 语义降级 | §8.2 | P2 | 回退原生 `str()` |

#### 文档合规回填（Audit Alignment）

| ID | 组件 | 修复内容 | 法典条款 | 风险等级 | 回滚方案 |
|:---|:---|:---|:---|:---|:---|
| FIX-025 | `docs/CANON_COMPLIANCE.md` | C-202（SSE 30s Ping）由 ⚠️ 更新为 ✅，补全代码证据路径；移除已闭环 RISK-004 | §2.1 | P2 | 回退该文档对应段落 |
| FIX-026 | `docs/ARCHITECTURE_CHECKPOINTS.md` | 2.1.2（30s/45s）由 ⚠️ 更新为 ✅，与 `frontend/src/utils/sse.ts` 和后端 timeout 事实对齐 | §2.1 | P2 | 回退该表格行 |
| FIX-027 | `docs/GLOBAL_CODE_AUDIT_V2.md` | 补充 2026-03-21 审计备注：前端 logger 治理完成、测试命令受策略拦截状态说明 | §5.1/§5.2 | P3 | 删除“本轮补充”小节 |

#### 全栈高可用与自愈审计加固 (Full-Stack HA & Self-Healing Hardening)

| ID | 组件 | 修复内容 | 法典条款 | 风险等级 | 回滚方案 |
|:---|:---|:---|:---|:---|:---|
| FIX-028 | `docker-compose.yml` | 16 个服务全量实装精确的 `healthcheck`/`stop_grace_period`/`depends_on condition`，实现拓扑级健康感知机制 | §3.4/§2.5 | P0 | 回推旧版 compose 配置 |
| FIX-029 | `watchdog.py`/`topology_sentinel.py` | 抛弃直连 socket（违规）及依赖 CLI。重构为通过 `DOCKER_HOST=tcp://docker-proxy:2375` (HTTP API) 巡检修复，彻底解决容器无 Docker 客户端导致的崩溃循环 | ADR 0006 | P0 | 强制切换旧版 socket 直连代码 |
| FIX-030 | `docker-compose.yml` | 发现 Loki/Promtail 为 Scratch 镜像无 shell，移除错误探针，改用 `service_started`，顺应物理规律 | §1.1 | P1 | 退回 `kill -0 1` 故障探针 |
| FIX-031 | `docker-proxy` | 为 watchdog 授权 `POST=1`（重启 API），挂载 `oom_score_adj: -999` 免死金牌 | §3.3 | P0 | 回退代理环境配置 |
| FIX-032 | `docker-compose.yml` | Caddy 改用管理员端口 `:2019/config/`，Mosquitto 改用免密码探针，全量通过最终验收测试 | §3.4 | P2 | 回退 curl 探针 |
| FIX-033 | `update.py`/`bootstrap.py`/`deployer.py` | 撤销暴力 `docker rm -f` 停服流程。引入前置冲突检测并 fallback 原生 `compose up -d --remove-orphans`，实现真·零停机滚动更新架构设计 | ADR 0018 | P0 | 回退含有暴力 rm 操作的旧脚本 |

#### IaC 管线永久回写 + 安全缺陷修复 + 全量代码质量清扫

| ID | 组件 | 修复内容 | 法典条款 | 风险等级 | 回滚方案 |
|:---|:---|:---|:---|:---|:---|
| FIX-034 | `compiler.py` | 回写 `stop_grace_period_block` 生成 + 修正全量 healthcheck 默认探针（VM→`/health`、alertmanager→`/-/healthy`） + `start_period` + OOM 扩展至 sentinel/watchdog/docker-proxy + `depends_on` 升级为结构化条件 | §3.3/§3.4 | P0 | `git checkout scripts/compiler.py` |
| FIX-035 | `docker-compose.yml.j2` | 新增 `{{ svc.stop_grace_period_block }}` 渲染插槽 | §3.4 | P1 | `git checkout` 模板 |
| FIX-036 | `system.yaml` | 新增 `watchdog` 服务定义 + `POST=1` for docker-proxy + mosquitto_passwd 卷挂载 | §1.2/ADR 0006 | P1 | `git checkout system.yaml` |
| FIX-037 | `portability.py` | **P0** `secure_shred_file` fallback `"ba+"` → `"r+b"` + 分块 1MB 写入 + `os.fsync()` 每轮刷盘 + `f.truncate()` 锁大小 | §3.3.2 | **P0** | 回退 shred fallback 模式 |
| FIX-038 | `portability.py` | **P1** 流式导出 `read_bytes` → `ZipInfo.open("w")` + 1MB 分块读取防 OOM | §3.3.2 | P1 | 回退 `writestr` 模式 |
| FIX-039 | `cluster.py` / `routes.py` | **P1** async 路由内 `subprocess.run` → `asyncio.to_thread()` 防阻塞 + SSE bytes → `.decode("utf-8")` 防 `b'...'` 字面量 | §2.5/§2.1 | P1 | 回退原始调用模式 |
| FIX-040 | 9 文件全量清扫 | 删除全部标准库懒加载 import（30+处）：`portability/cluster/routes/events_schema/iot/energy/observability`；删除 5 个废 import（`platform`/`partial`/`get_current_user`/`Field`）；`ci.yml` 覆盖率口径 `--cov=.` → `--cov=api --cov=core --cov=models --cov=sentinel` | §8.2/§5.2 | P2 | `git checkout` 各文件 |

#### IaC 管道 + 全局代码深度强化 (三轮扫描 / 35 缺陷 / 13 文件 / 624 passed)

| ID | 组件 | 修复内容 | 法典条款 | 风险等级 | 回滚方案 |
|:---|:---|:---|:---|:---|:---|
| FIX-041 | `compiler.py` | **P0** `build_block`/`command_block` f-string 拼接 → `_dict_to_yaml_block()` 结构化 YAML | §8.2 | P0 | `git checkout scripts/compiler.py` |
| FIX-042 | `compiler.py` | **P0** `import subprocess`/`difflib` 函数内懒加载 → 顶层 import | §8.2 | P0 | 回退 import 位置 |
| FIX-043 | `compiler.py` | **P1** `.replace("zen70-","")` 多前缀截断 → `.removeprefix()` | §8.2 | P1 | 回退 hostname 逻辑 |
| FIX-044 | `compiler.py` | **P1** `except Exception` (3 处) → 精确异常类型 `(json.JSONDecodeError, OSError)` / `(OSError, subprocess.SubprocessError)` | §8.2/§7.7 | P1 | 回退 except 范围 |
| FIX-045 | `compiler.py` | **P1** ACL f-string 多行拼接 → `list` + `"\n".join()` 结构化 | §8.2 | P1 | 回退 ACL 生成 |
| FIX-046 | `compiler.py` | **P2** `main()` 缺 logging handler / `stat().st_mtime` TOCTOU / `keep_trailing_newline` | §2.5/§8.2 | P2 | 回退相关行 |
| FIX-047 | `lint.py` | **P0** `str(version) < "2.0"` 字典序比较 → `tuple(int...)` 语义版本 | §8.2 | P0 | 回退 version 校验 |
| FIX-048 | `lint.py` | **P1** `print()` 警告无 stderr → 统一 `file=sys.stderr` | §2.5 | P1 | 回退 print 调用 |
| FIX-049 | `update.py` | **P1** `'old_head' in dir()` 脆弱反模式 → `old_head = "HEAD~1"` 哨兵预初始化 | §8.2 | P1 | 回退 old_head 逻辑 |
| FIX-050 | `update.py` | **P2** `open(path, "rb")` → `path.open("rb")` / `shlex.join()` 安全命令拼接 | §8.2 | P2 | 回退调用方式 |
| FIX-051 | `watchdog.py` | **P1** `conn.close()` 不在 `finally` → `try/finally` 防 FD 泄漏 (2 处) | §3.3 | P1 | 回退 HTTP 连接管理 |
| FIX-052 | `watchdog.py` | **P1** `except Exception:` → `except OSError:` + `str(PROJECT_ROOT)` 冗余转换删除 | §7.7/§8.2 | P1 | 回退相关行 |
| FIX-053 | 7 backend 文件 | **P2** 全局 `with open()` → `Path.open()` : portability/background_tasks/routing_operator/data_integrity/mqtt_worker/assets/gen_vapid | §8.2 | P2 | `git checkout` 各文件 |
| FIX-054 | `data_integrity.py` | **P1** `except Exception` → `except OSError` / `time.sleep(0)` 无操作删除 / `import httpx` 懒加载 → 顶层 | §8.2/§7.7 | P1 | 回退相关行 |
| FIX-055 | `assets.py` | **P2** 删除未使用 import (`get_db`, `FeatureFlag`)；`gen_vapid.py` 去除多余 f-string | §8.2 | P2 | 回退 import |

#### IaC 编译器加固 (Batch 1–4)

| ID | 组件 | 修复内容 | 法典条款 | 风险等级 | 回滚方案 |
|:---|:---|:---|:---|:---|:---|
| FIX-001 | `secrets_manager.py` | `POSTGRES_DSN` 自动构造注入 `.env`，消除手动管理 | §1.2 | P1 | 回退 `.env` 手动维护 DSN |
| FIX-002 | `compiler.py` | `redis_host`/`postgres_host`/`redis_port`/`postgres_port` 从 `system.yaml` 读取，去除硬编码 | §1.2 | P2 | 回退 `prepare_env()` 硬编码值 |
| FIX-003 | `secrets_manager.py` | URL-safe 密码生成，过滤 DSN 保留字符 `@:/%?#` | §1.2 | P0 | 回退 `secrets.token_urlsafe()` |
| FIX-004 | `compiler.py` | JWT `--rotate-jwt` 真轮转，CURRENT→PREVIOUS 降级 | §3.4 | P1 | 不执行 `--rotate-jwt` 即无影响 |
| FIX-005 | `lint.py` | Schema 强校验，缺少必填字段 exit(1) | §1.2 | P2 | 注释掉 schema 校验逻辑 |
| FIX-006 | `compiler.py` | `--dry-run` diff 预览 + 敏感数据脱敏 | §1.2 | P3 | 无需回滚（纯只读功能）|
| FIX-007 | `compiler.py` | `_dict_to_yaml_block()` 结构化 YAML 替代 f-string | §8.2 | P2 | 回退 f-string 拼接 |
| FIX-008 | `compiler.py` | Redis `users.acl` 生成时去除 `#` 注释 | §2.5 | P0 | Redis 7.4 strict 模式必须无注释 |

#### Caddy 路由 & 前端修复

| ID | 组件 | 修复内容 | 法典条款 | 风险等级 | 回滚方案 |
|:---|:---|:---|:---|:---|:---|
| FIX-009 | `Caddyfile.j2` | `@matcher` + `handle` 互斥路由，API/SSE 优先于 SPA fallback | §2.1 | P0 | 回退旧 `handle_path` Caddyfile |
| FIX-010 | `vite.config.ts` | SW `navigateFallbackDenylist` + Content-Type 验证防缓存投毒 | §3.6 | P1 | 移除 `navigateFallbackDenylist` |
| FIX-011 | `compiler.py` | Healthcheck 极简镜像适配 (`kill -0 1`) | §3.4 | P2 | 回退 `wget` healthcheck |
| FIX-012 | `compiler.py` | Loki 移除默认 healthcheck（静态二进制无 `/bin/sh`）| §3.4 | P3 | 重新添加 loki healthcheck |

#### 基础设施修复（前序）

| ID | 组件 | 修复内容 | 法典条款 | 风险等级 |
|:---|:---|:---|:---|:---|
| FIX-013 | `compiler.py` | `read_only: true` 自动注入 `tmpfs: ["/tmp"]` | §3.4 | P1 |
| FIX-014 | `system.yaml` | 版本 `1.0` → `2.0`，消除 config-lint WARN | §1.2 | P3 |
| FIX-015 | Alembic 迁移 | `@generated by ZEN70-AI-Agent` 溯源头注入 | §8.2 | P3 |
| FIX-016 | `pgvector/pgvector:pg15` | 替换 Alpine postgres，Alembic 自定义 Vector 类型 | §1.1/§3.5 | P1 |
| FIX-017 | `postgresql.conf` | ALTER ROLE 密码重置，修复认证失败 | — | P0 |
| FIX-018 | `Caddyfile` | 修复旧版 API 路由 502/404 | §2.1 | P0 |
| FIX-019 | `loki/local-config.yaml` | 清除废弃字段适配新 schema | — | P2 |
| FIX-020 | `categraf` | 挂载配置文件名修正 | — | P3 |
| FIX-021 | `users.acl` | Redis 7.4 `--aclfile` 去注释 | §2.5 | P0 |
| FIX-022 | `requirements.txt` | 补齐 PyJWT / python-dotenv | — | P1 |
| FIX-023 | `backend/core/redis_client.py` | `RedisClient` 增加代理方法 `get/set/incr/delete` 修复登录 500 | §8.2 | P0 |
| FIX-024 | `backend/api/routes.py` | 修复 `/api/v1/capabilities` Pydantic 模型与 Redis 断联降级矩阵 | §3.2.5 | P1 |

### Known Issues

| ID | 组件 | 描述 | 影响 | 优先级 |
|:---|:---|:---|:---|:---|
| KI-001 | PgBouncer | SCRAM-SHA-256/MD5 认证不匹配，DSN 绕过 PgBouncer 直连 | 连接池未生效 | P2 |

### Changed

| ID | 组件 | 变更内容 | 影响范围 |
|:---|:---|:---|:---|
| CHG-001 | `main.py` | 拆分单体为 5 模块 (`capabilities`/`middleware`/`background_tasks`/`gateway_routes`/`shared_state`) | 后端架构 |
| CHG-002 | `app.state.redis` | 统一 Redis 连接池，消除 per-request 短连接 | 后端性能 |
| CHG-003 | `capabilities.py` | `get_capabilities_matrix()` → `fetch_topology()` + `build_matrix()` | 可测试性 |
| CHG-004 | `background_tasks.py` | Bit-Rot SQLite 移入 `asyncio.to_thread()` | 并发安全 |
| CHG-005 | `docs/` | 历史文件归档 `docs/archive/`，清理空壳 | 文档结构 |

---

## [3.0.0] - 2026-03-16

### Added
- V3.0 文档包（历史归档，已删除）：业务说明、部署指南、全量文档
- 离线镜像导入脚本 (`A_一键导入离线镜像环境.bat`)
- 图形化部署向导 (`start_installer.py`)
- CI/CD: GitHub Actions (`ci.yml`) + Trivy 镜像扫描 + Dependabot
- `release.sh` 自动化语义发布脚本
- ADR 0001–0010 架构决策记录
- `CANON_COMPLIANCE.md` 法典实装率追踪

### Security
- JWT 双轨轮转 (CURRENT/PREVIOUS) 自动续签
- WebAuthn 无密登录 + PIN 降级（5 次锁 IP 15 分钟）
- 容器安全基线：non-root、read_only、cap_drop ALL

---

## [2.9.0] - 2026-03-12

### Added
- SRE 发布报告 (`ZEN70_V2.9_Release_SRE_Report.md`)
- 全局代码审计 V2 (`GLOBAL_CODE_AUDIT_V2.md`)

---

## [1.58.0] - 2025-12-xx

### Added
- 初始架构法典 (V1.58)：9 部分 93KB 全量规范
- 探针 (Topology Sentinel) 三重核验
- 三步熔断顺序
- UPS 联动优雅停机

> 注：V1.58 全文为历史归档内容，现已移除。
