# 仓库加固清单

## 租户与鉴权
- 登录前识别必须使用 `tenant_id + username`，不允许再按裸 `username` 命中用户。
- 前端登录入口必须显式发送 `tenant_id`。
- `password`、`PIN`、`WebAuthn`、`Push` 路径必须与后端路由和 OpenAPI 保持一致。
- 管理权限判定以 [`backend/api/deps.py`](/E:/3.4/backend/api/deps.py) 为单一真源；`admin` 与 `superadmin` 不允许在不同接口上出现不同语义。
- 前端角色集合可以保留 `geek`，但不得再把它当作管理员角色。

## 控制面租户双保险
- `nodes / jobs / job_attempts / job_logs / connectors` 必须带 `tenant_id`。
- `get_tenant_db()` 之外，关键读取代码也要显式带 `tenant_id` 条件，不能只赌 RLS 单点。
- `portability/export` 这类高敏导出接口必须使用 `get_tenant_db()`，且查询显式带租户过滤。
- `connectors`、`nodes`、`users`、`jobs.idempotency_key` 的唯一性都应按租户维度收口。
- API 启动必须验证 RLS 已真实生效；`get_tenant_db()` 与机器链路租户绑定在无 RLS 时必须拒绝服务。

## Secrets 与临时产物
- `runtime/secrets/`、`runtime/tmp-compile/`、`config/users.acl` 必须被忽略且不得进入 git index。
- Redis ACL 必须写入外置安全状态目录，不允许落回仓库工作区。
- `scripts/backup.py` 必须强制外部注入 `ASHBOX_PASSWORD`，禁止生成密码文件。
- 备份必须强制 `pyzipper` AES 加密，禁止普通 ZIP 回退。
- 数据库 dump 不得明文落盘；失败时必须清理半成品。

## 机器通道与 Runner
- `runner-agent` 默认必须使用 HTTPS 网关地址。
- 非 loopback 主机必须强制 HTTPS；仅允许本机开发通过 `RUNNER_ALLOW_INSECURE_HTTP=true` 显式放行 HTTP。
- 自定义 CA 文件必须在启动时校验为有效 PEM；证书指纹 pin 必须是 64 位 SHA256。
- 节点 bootstrap 回执必须明确提醒 HTTPS 默认要求，避免把明文 Bearer 当成正常路径。

## CI 与发布不可变性
- `.github/workflows/*.yml` 禁止 `ubuntu-latest`。
- `.github/workflows/*.yml` 禁止 `@v*`、`@master`、`@main` 这类浮动 action 引用。
- 仓库内禁止 `:latest` 作为正式发布输入。
- 外部镜像必须统一 `@sha256:` digest pin。
- `docker-publish` 只允许 `sha-*` 与完整 `semver` 标签；禁止 `branch / pr / schedule / major / minor` 这类可变标签进入正式发布语义。
- Python CI 必须通过 `requirements-ci.lock + --require-hashes` 安装，不允许直接 `pip install -r ...` 漂移依赖。
- 离线包必须按提交生成不可变 release tag，不能持续向固定 release tag 追加资产。
- 离线包上传跳过逻辑必须同时校验 ZIP 与 `.sha256`，缺一不可跳过。
- 离线包必须输出 `image-lock.txt` 与校验文件。

## 本地引导与可复现性
- `scripts/bootstrap.py` 在存在 `package-lock.json` 时必须优先使用 `npm ci`。
- `deploy/bootstrap.py` 兼容包装层必须保留失败返回码和 stderr 可观测性，不能静默吞错。

## 最低回归门禁
- `python -m pytest backend/tests/unit -q`
- `python -m pytest tests/test_compliance_sre.py tests/test_repo_hardening.py tests/test_backup_security.py -q`
- `python -m pytest tests/integration/test_rls_runtime_enforcement.py -q`
- `cd frontend && npm run lint`
- `cd frontend && npm run test -- --run`
- `cd frontend && npm run build`
- `cd runner-agent && go test ./...`
- `python scripts/generate_contracts.py`
- `python scripts/compiler.py system.yaml -o . --dry-run`

## 本轮补充

- 停用账号必须在 `password / PIN / WebAuthn / invite fallback` 全链路统一阻断，禁止继续签发 JWT。
- 前端认证令牌只允许保存在 `sessionStorage`；若检测到旧 `localStorage` 令牌，必须立即迁移并删除旧副本。
- 默认机器通道必须走 `https://caddy`，并通过只读挂载的 Caddy 私有 CA 根证书完成 TLS 校验。
- 离线包必须排除前端临时构建/审计文本和旧 `config/system.yaml`，正式安装包只能暴露根 `system.yaml` 作为真源。
- `backend/Dockerfile` 与 `runner-agent/Dockerfile` 的基础镜像必须 `@sha256:` 固定，不允许只写 tag。
- `gateway-iot / gateway-ops / gateway-full` 和 `deploy/bootstrap.py` 只允许作为迁移兼容入口，不得继续作为正式交付叙事的一部分。

## 2026-03-28 增补

- `:80` 入口不得再直接反代 `/api/*`、SSE 或机器控制通道，必须统一重定向到 HTTPS。
- `push_subscriptions` 必须带 `tenant_id`，且唯一性必须收口到 `(tenant_id, endpoint)`。
- 全局 `feature_flags` 与 `system_config` 的读写权限必须限定为 `superadmin`，租户 `admin` 不得修改全局设置。
- `scripts/update.py` 不得写死 `main`；必须按环境变量、upstream tracking branch、当前分支的顺序解析拉取目标。
- 仓库正式 surface 不得再包含 `config/system.yaml`；根 `system.yaml` 是唯一正式配置入口。
- `deploy/config-compiler.py` 必须保持为对 `scripts/compiler.py` 的兼容 wrapper，不得再次嵌入第二套编译器逻辑。
- bundle 校验必须同时检查 `system.yaml / render-manifest.json / docker-compose.yml / config/Caddyfile / docs/openapi-kernel.json / contracts/openapi/zen70-gateway-kernel.openapi.json` 的一致性，而不是只检查文件存在。
