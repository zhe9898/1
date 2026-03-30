# Changelog

All notable changes to the ZEN70 project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [3.42] - 2026-03-28

### Added
- **P1-4 会话管理**：Session 模型、设备指纹识别、并发登录限制（默认 10），支持查看/踢出活跃会话。API：`GET/DELETE /api/v1/sessions/me`，管理员可强制踢出任意用户会话。
- **P1-5 节点审批流程**：节点注册后置 `pending`，管理员审批后才变 `active`，防止恶意节点直接承接任务。API：`GET /api/v1/nodes/pending`，`POST /api/v1/nodes/{id}/approve|reject`。
- **P1-6 资源配额系统**：租户级配额限制（nodes/connectors/jobs_concurrent/jobs_per_hour），`-1` 表示无限制。配额检查集成到节点注册和连接器创建路径。API：`GET/PUT /api/v1/quotas`。
- **P1-7 监控告警**：AlertRule 可配置条件（node_offline/job_failure_rate）与动作（webhook/log）。Webhook 通知通过 Job 派发由 runner 执行，不在网关进程内发起 HTTP。API：`/api/v1/alerts/rules` CRUD + `/api/v1/alerts` 历史记录。
- **P2 内核能力注册表**：12 个 KernelCapability 声明（identity/control/platform 三域），版本化，可发现。API：`GET /api/v1/kernel/capabilities`。
- **P2 DAG 任务编排**：Workflow + WorkflowStep 模型，拓扑排序，循环检测，步骤依赖注入，失败快速传播。API：`POST/GET /api/v1/workflows`，步骤回调 complete/fail。
- **auth.py 拆分**：1139 行拆为 8 个独立模块（auth_shared/bootstrap/password/webauthn/pin/user/invite），最大文件 226 行。
- **P0 平台内核稳定性**：审计日志系统、用户状态管理（suspend/activate/delete）、细粒度权限模型（Scope + require_scope()）、协议版本强制检查、Job/Connector Kind 注册表。

### Fixed
- **架构修正**：alerting.py 删除 httpx 直接调用，改为派发 `alert.notify` Job，规则留网关，执行移出网关。
- **IAC 三层唯一事实来源**：system.yaml 增加 sentinel 容器角色声明；编译器注入 `SENTINEL_*` 环境变量；`POSTGRES_HOST` 显式导出；alembic/env.py 消除硬编码主机名。
- **JWT 撤销健壮性**：新增 `REDIS_REQUIRED_FOR_TOKEN_REVOCATION=1` 严格模式；Redis 不可用时 fail closed；黑名单写失败从 debug 升为 warning。
- **DB 连接池 TCP Keepalive**：`tcp_keepalives_idle=30s/interval=10s/count=5`，防止 NAT 静默断连。
- **RLS 功能验证**：`assert_rls_ready()` 增加探针查询，从元数据验证升级为行为验证。
- **IAC lint 强化**：`deployment.profile` 加入 REQUIRED_SCHEMA；version 格式强制 x.y。
- **IAC migrator 回滚**：回写失败自动从 .bak 恢复。
- **编译器原子写入**：降级路径加 fsync；chmod 失败从静默 pass 改为 warning。
- **密钥幂等性保护**：.env 丢失但 postgres 数据目录存在时，生成新密码前发出 WARNING。

## [3.41] - 2026-03-27

### Added
- **运维自动化（法典 6.9）**：`deployer.py` 幂等部署，支持 `--rollback` 回滚；`install_wizard.py` 交互式安装向导；`zen70-doctor.sh` 一键诊断；bootstrap 点火时 Linux 下执行 `swapoff -a`。
- **供应链（法典 1.1）**：私有镜像仓库支持。`system.yaml` 新增 `registry.enabled` / `registry.url`。
- **安全拦截防线**：前端 Axios 全局捕获 503/504 熔断，自动激活大屏降阶“维护模式”骨架屏。

## [2.9.1] - 2026-03-18

### Fixed
- **CI/CD 发版死锁重试漏洞**：修复了在 GitHub 自动执行大文件打包（1.5GB）时，由于远端同名 Release `v2.9` 已存在导致的 GitHub API HTTP 422 (Unprocessable Entity) 封锁。对 `Create Release` 及 `Asset Upload` 全面注入三轮指数退避重试循环，以标准 DevOps 语意升级基线至 V2.9.1 彻底根除发版冲突。
- **Flake8 & Isort 格式规范审计**：将项目全局 Python 单行字符上限放宽至 160 字符，并自动排列 `backend/tests/unit/test_alert_manager.py` 的绝对引入路径（解决模块互调的 `ModuleNotFoundError`），目前已 100% 绿灯通过云端 `Compliance` 合规工作流。

### Added
- **云端离线打包体系 (GFW Bypass)**：落地 `build_offline_v2_9.yml` 官方企业级发版流水线，实现脱离本地宽带局限，自动化并行拉取十余个上游官方镜像，打包并压缩输出自带 `zen70-gateway` 层的单体 `zen70_v2.9.1_offline_bundle.zip`。
- **离线一键加载批处理**：提供配套的终端防小白工具 `A_一键导入离线镜像环境(必点).bat`，点击即可物理注入全套 Docker 缓存环境。

### Changed
- **文档大一统编纂（Single Source of Truth 收束）**：清除多达 15 份过时的功能碎片文档与 `ZEN70_Architecture.txt` 粗糙大纲。重绘合并为极具穿透力且格式标准的终极白皮书 `ZEN70_Architecture_and_Features_V2.9.1.md`，并将性能压测报告降级收拢归档至 `docs/reports`，全面响应“**所有文档合规**”审计要求。
