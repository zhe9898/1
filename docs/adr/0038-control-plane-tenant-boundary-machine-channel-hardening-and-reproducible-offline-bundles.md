# 0038. 控制面租户边界、机器通道边缘防线与离线包可重现性

- 状态: Accepted
- 日期: 2026-03-27

## 背景

控制面已经具备 `nodes / jobs / connectors / console` 的后端驱动闭环，但仍存在三类高风险残口：

1. 控制面表缺少统一的 `tenant_id` 事实，`nodes / jobs / job_attempts / job_logs / connectors` 默认还是“全局域”语义。
2. 机器通道虽然已经有 `node_token` 应用层鉴权，但入口层没有第二道防线，`/api/v1/nodes/*` 与 `/api/v1/jobs/*` 可被公网直接持续撞击。
3. 离线发布 workflow 仍依赖漂移标签并允许覆盖上传，同一 release tag 下的离线包资产不可追溯，也不可复现。

## 决策

### 1. 控制面数据默认进入租户隔离域

- 为 `nodes / jobs / job_attempts / job_logs / connectors` 显式补齐 `tenant_id`
- 将上述控制面表纳入 RLS 白名单
- 人类控制面接口统一通过 `get_tenant_db()` 绑定当前 JWT 的 `tenant_id`
- 机器接口统一要求 body 带 `tenant_id`，并在机器鉴权前先执行 `set_tenant_context()`
- 节点 bootstrap 回执必须下发 `RUNNER_TENANT_ID`，机器启动命令不能再省略租户上下文

### 2. Console 运维聚合接口不再允许匿名访问

- `/api/v1/console/overview`
- `/api/v1/console/diagnostics`

以上接口统一改为要求登录用户，并继承租户隔离上下文。匿名访客只允许读取 `/api/v1/console/menu` 这类低敏快照。

### 3. 机器通道增加入口层第二道防线

- Caddy 单独匹配：
  - `/api/v1/nodes/register`
  - `/api/v1/nodes/heartbeat`
  - `/api/v1/jobs/pull`
  - `/api/v1/jobs/*/progress`
  - `/api/v1/jobs/*/renew`
  - `/api/v1/jobs/*/result`
  - `/api/v1/jobs/*/fail`
- 上述路径默认只允许 `MACHINE_API_ALLOWLIST` 指定来源访问
- 缺省值固定为 `private_ranges`

这不是替代应用层 node token，而是入口层第二道收口。

### 4. 离线包发布改为“按提交冻结”

- workflow 不再硬编码拉取一串 `latest` 并直接覆盖资产
- 构建时先生成 compose 镜像清单，再解析并记录 `image-lock.txt`
- 本地镜像按当前提交构建，并把 commit SHA 写入 bundle 元数据
- release 资产名带短 SHA
- 上传资产不再使用 `--clobber`

## 后果

### 正面

- 控制面不再默认跨租户共享数据域
- 机器通道在入口层和应用层都具备防线
- Console 聚合视图回到正常网关的安全边界
- 离线包可以按提交追溯，release 资产不再被静默覆盖

### 代价

- runner/native client 协议必须增加 `tenant_id`
- 旧的匿名 overview/diagnostics 调用将被拒绝
- 离线包 workflow 变得更严格；同名资产重复上传会失败或被跳过，而不是被覆盖

## 实施说明

- 数据模型与 RLS 变更：`backend/models/*`, `backend/db.py`, `backend/core/rls.py`
- 控制面 API 变更：`backend/api/nodes.py`, `backend/api/jobs.py`, `backend/api/connectors.py`, `backend/api/console.py`
- runner 协议对齐：`runner-agent/internal/*`
- 入口层防线：`scripts/templates/Caddyfile.j2`, `system.yaml`
- 离线包可重现性：`.github/workflows/build_offline_v2_9.yml`
