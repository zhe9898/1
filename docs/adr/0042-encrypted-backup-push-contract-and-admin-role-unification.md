# ADR 0042: 加密备份、Push 合同与管理权限统一

- Status: Accepted
- Date: Unknown
- Scope: 加密备份、Push 合同与管理权限统一

> Source of truth: code and tests override ADR text. See ADR 0052 when documentation and implementation diverge.

## 背景

控制面最近一轮自审暴露出 5 个会直接影响安全性或产品可用性的断点：

1. `scripts/backup.py` 在缺少外部密码时会生成密码文件，并把 `db_dump.sql` 明文落盘，还允许在 `pyzipper` 缺失时回退普通 ZIP。
2. 前端已经调用 `/v1/auth/push/*`，但后端未把 Push 路由装配到 `/api/v1/auth/push/*`，导致运行时 404 与 OpenAPI 漂移。
3. `admin/superadmin` 判定在 `deps.py` 与 `settings.py` 中各自实现，跨路径行为不一致。
4. `portability/export` 仍使用 `get_db`，并且资产查询缺少显式 `tenant_id` 过滤。
5. `docker-publish` 仍输出 `major/minor` 这类可变语义标签，不满足发布不可变性要求。

## 决策

### 1. 备份链路强制外部密钥与 AES 加密

- `ASHBOX_PASSWORD` 必须由外部显式注入，禁止生成任何明文密码文件。
- `pyzipper` 变为强依赖；缺失时直接失败，禁止无加密 ZIP 回退。
- PostgreSQL dump 改为内存捕获后直接写入加密 ZIP，禁止再生成 `db_dump.sql` 临时明文文件。
- ZIP 失败时清理半成品，并对最终产物执行最佳努力的 owner-only 权限收紧。

### 2. Push 以认证域路径为单一真源

- Push 路由统一挂载在 `/api/v1/auth/push/*`。
- 前端 API 常量、后端路由装配和 OpenAPI 由同一路径集合收口。
- Web Push 初始化只在用户已登录后启动，避免匿名态触发伪可用请求。

### 3. 管理权限判定单点化

- `backend.control_plane.adapters.deps` 成为管理角色判定的单一事实源。
- 统一提供：
  - `has_admin_role()`
  - `is_superadmin_role()`
  - `require_admin_role()`
- `settings` 与后续管理面接口不得再各自实现 `admin` 判定。

### 4. 导出接口按租户作用域收口

- `portability/export` 使用 `get_tenant_db()`。
- 资产读取显式添加 `tenant_id` 与 `is_deleted = false` 条件。
- 这条查询不能只依赖 RLS 单点。

### 5. Docker 发布只保留不可变标签

- `docker-publish` 仅保留：
  - `sha-*`
  - 完整 `semver`（`x.y.z`）
- 移除 `major`、`major.minor` 这类可重指向标签。

## 影响

### 正面影响

- 备份工件不再默认落地高危明文内容。
- Push 前后端合同恢复一致，运行时不再 404。
- `superadmin` 与 `admin` 的权限表现跨路径一致。
- 租户导出链路不再依赖“现场 RLS 必定正确”这一假设。
- 发布物更可追溯，部署侧更难误用可变标签。

### 代价

- Ashbox 备份现在要求部署环境明确提供密码并安装 `pyzipper`。
- 旧的“geek 视为 admin”前端语义被取消，UI 权限以真实后端角色为准。

## 验证

- 单元测试覆盖：
  - 备份密码与加密强制
  - Push 路由装配
  - `superadmin` 权限一致性
  - `portability` 租户过滤
  - `docker-publish` 不可变标签约束
- OpenAPI 重新导出并回归前端合同。
