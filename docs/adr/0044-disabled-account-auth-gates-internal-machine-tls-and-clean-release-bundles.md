# ADR 0044: 停用账号登录阻断、内置机器 TLS 与纯净发布包

- Status: Accepted
- Date: Unknown
- Scope: 停用账号登录阻断、内置机器 TLS 与纯净发布包

> Source of truth: code and tests override ADR text. See ADR 0052 when documentation and implementation diverge.

## 背景

3.4 控制面已经完成多租户、节点令牌、调度器和后端驱动控制台，但仍有几条会在正式环境里留下真实事故面的链路：

1. 停用账号仍可能通过密码、PIN、WebAuthn 或邀请降级链路继续签发 JWT。
2. `runner-agent` 已经要求非本机默认 HTTPS，但默认编排仍把 `GATEWAY_BASE_URL` 指向 `http://gateway:8000`，导致部署默认值和安全策略互相打架。
3. 前端认证令牌仍落在 `localStorage`，一旦出现 XSS 会放大窃取面。
4. 离线包仍可能夹带前端本地构建/审计残留，且正式包里保留旧 `config/system.yaml` 入口，影响唯一事实源表达。
5. `backend/Dockerfile`、`runner-agent/Dockerfile` 的基础镜像未 digest pin，供应链不可变性不完整。

## 决策

### 1. 所有登录入口统一做停用账号阻断

在 `backend/api/auth.py` 中引入统一的 `assert_user_active` 守卫，并强制应用到：

- `password_login`
- `pin_login`
- `webauthn/login_begin`
- `webauthn/login_complete`
- `invite_webauthn_register_begin`
- `invite_webauthn_register_complete`
- `invite_fallback_login`

停用账号命中时返回 `403`，同时打审计日志，不再继续签发 JWT 或允许继续完成邀请降级链路。

### 2. 默认机器通道改为内部 TLS

默认编排不再让 Runner 走 `http://gateway:8000`，改为：

- `GATEWAY_BASE_URL=https://caddy`
- `GATEWAY_CA_FILE=/caddy-data/caddy/pki/authorities/local/root.crt`

同时让 `runner-agent` 共享只读 `caddy_data` 卷，读取 Caddy `tls internal` 生成的私有 CA 根证书。

`scripts/templates/Caddyfile.j2` 新增独立的内部机器通道站点：

- Host：`https://{$MACHINE_API_INTERNAL_HOST:caddy}`
- 证书：`tls internal`
- 用途：仅承载机器 API 和事件流

这样默认部署路径就是“可用且加密”的，而不是靠 `RUNNER_ALLOW_INSECURE_HTTP=true` 兜底。

### 3. 邀请降级登录增加显式确认

`invite_fallback_login` 现在要求 `X-Invite-Fallback-Confirm: degrade-login`。
前端在调用前显示二次确认，并把确认头显式带上。

这条链路继续保留，但不再是“一个链接点一下就直接换 JWT”。

### 4. 前端认证令牌改为 sessionStorage

前端认证令牌不再写入 `localStorage`，统一改为 `sessionStorage`。

为兼容已有会话，首次加载时会将旧 `localStorage` 里的 token 迁移到 `sessionStorage`，然后立即删除旧副本。

### 5. 发布包继续收口为纯净输入

离线包仓库打包阶段新增排除：

- `frontend/build_*.txt`
- `frontend/eslint_*.txt`
- `frontend/vuetsc_*.txt`
- `frontend/full_build_*.txt`
- `frontend/test_output.txt`
- `frontend/test_result*.json`
- `config/system.yaml`

同时 `deploy/config-compiler.py` 的默认配置入口改回根 `system.yaml`，避免正式安装包里出现两个“像真源”的配置入口。

### 6. Dockerfile 基础镜像 digest pin

以下 Dockerfile 基础镜像全部改为 `@sha256:`：

- `backend/Dockerfile`
- `runner-agent/Dockerfile`

这一步把系统配置层和 Dockerfile 层的不可变性对齐起来。

## 结果

### 正向效果

- 停用账号治理终于真正生效，不再只停留在用户列表展示。
- 默认 Runner 部署路径从“安全策略和默认配置互相冲突”变成“默认可用且默认加密”。
- 前端 token 暴露面收缩。
- 正式离线包更干净，唯一事实源表达更清楚。
- 供应链不可变性从 `system.yaml` 延伸到 Dockerfile 层。

### 代价

- 邀请降级登录多了一次显式确认，交互上更严格。
- Runner 容器默认依赖 Caddy 内部 CA 卷；如果人为删掉该只读挂载，会在启动时 fail-fast。
- 旧的 `localStorage` 登录态会被迁移到 `sessionStorage`，浏览器会话关闭后需要重新登录。

## 验证

- 后端新增停用账号登录回归测试，覆盖密码、PIN、WebAuthn、邀请降级。
- 前端新增 token 存储迁移与 `sessionStorage` 契约测试。
- 编译器与渲染产物测试覆盖内部 TLS Caddyfile、Runner CA 挂载和 HTTPS 默认地址。
- 仓库硬化测试新增：
  - Dockerfile digest pin
  - 离线包排除前端审计残留
  - 离线包排除 `config/system.yaml`

## 后续

- 若后续要把外部用户入口也统一为显式 TLS 合同，应在 Caddy 公网入口上继续收口，而不是回退机器通道默认值。
- 若未来引入短期会话刷新机制，可继续把前端 token 暂存面再往内收，但不影响当前这轮基线。
