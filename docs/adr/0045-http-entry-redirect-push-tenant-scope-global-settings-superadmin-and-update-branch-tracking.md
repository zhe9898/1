# ADR 0045: HTTP 入口重定向、Push 租户作用域、全局设置超管化与更新分支跟踪

- Status: Accepted
- Date: Unknown
- Scope: HTTP 入口重定向、Push 租户作用域、全局设置超管化与更新分支跟踪

> Source of truth: code and tests override ADR text. See ADR 0052 when documentation and implementation diverge.

## 背景

在全链路审计中，控制面主链已经基本闭环，但仍残留 4 个会持续制造运维和安全噪音的缺口：

1. `:80` 入口仍直接承接 `/api/*` 与机器控制通道，HTTP 明文入口没有彻底关闭。
2. `push_subscriptions` 仍按全局 `endpoint` 归属，缺少租户维度唯一性和 RLS 保护。
3. `feature_flags` 与 `system_config` 是全局表，但写接口只要求 `admin`，与既有 `superadmin` 角色模型不一致。
4. `scripts/update.py` 写死 `git pull ... main`，而仓库当前主线是 `master`，自动更新脚本存在漂移和失败风险。

## 决策

### 1. 80 端口不再承接 API 明文流量

- `:80` 入口只负责把 `/api/*`、SSE 和机器控制通道重定向到 HTTPS。
- 机器控制通道的 IP allowlist 仍保留，但在 `:80` 上不再直代到网关。
- `caddy-only` 渲染仍保留动态 routes 能力，用于 routing-operator 的受限重编译。

### 2. Push 订阅进入租户作用域

- `push_subscriptions` 增加 `tenant_id`。
- 唯一性从全局 `endpoint` 改为 `(tenant_id, endpoint)`。
- `push_subscriptions` 纳入 RLS 保护表集合。
- Push 查询和覆盖逻辑统一按 `tenant_id + endpoint` 或 `tenant_id + user_id` 过滤。

### 3. 全局设置只能由 superadmin 修改

- `feature_flags`、`system_config` 继续保持全局表语义。
- 其读写接口统一要求 `superadmin`，不再允许租户 `admin` 修改全局开关、模型默认值或 provider URL。
- `admin` 与 `superadmin` 的判定继续只由 `backend/api/deps.py` 作为单一真源。

### 4. 自动更新跟踪真实分支

- `scripts/update.py` 不再硬编码 `main`。
- 更新脚本按以下顺序确定拉取分支：
  1. `ZEN70_UPDATE_BRANCH`
  2. Git upstream tracking branch
  3. 当前检出分支
  4. 最终回退到 `master`

## 影响

### 正面

- API 和机器控制通道默认不会再被 HTTP 明文误用。
- Push 订阅不会再出现跨租户抢占同一 endpoint 的风险。
- 全局设置与租户管理员边界重新一致。
- 更新脚本在 `master`/自定义分支/跟踪分支场景下都更稳。

### 代价

- 旧数据库需要执行 Alembic 迁移，把 `push_subscriptions` 升级到租户维度。
- 运维如果仍需要通过 HTTP 调试 API，必须显式使用 HTTPS 重定向后的地址，不再存在“80 端口也能用”的隐性路径。

## 验证

- 增加 Push 租户作用域测试。
- 增加 Settings 超管边界测试。
- 增加 `update.py` 分支探测与 `step_git_pull()` 行为测试。
- 增加 Caddy `:80` 重定向与动态 routes 渲染测试。
- 增加 Alembic 迁移契约测试和 RLS push 表覆盖测试。
