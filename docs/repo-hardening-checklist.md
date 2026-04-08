# Repo Hardening Checklist

## 租户与认证

- 登录前识别必须使用 `tenant_id + username`
- 人类控制面请求必须走租户作用域与统一认证依赖
- `password`、`PIN`、`WebAuthn`、邀请降级等路径都必须与后端路由和 OpenAPI 保持一致

## 控制面租户边界

- `nodes / jobs / job_attempts / job_logs / connectors` 必须带 `tenant_id`
- `get_tenant_db()` 外的关键查询也必须显式带租户过滤
- `jobs.idempotency_key`、`nodes.node_id` 等唯一性必须按租户维度收口
- API 启动必须验证 RLS readiness

## Secrets 与临时产物

- `runtime/secrets/`、`runtime/tmp-compile/`、`config/users.acl` 必须被忽略且不得进入 git index
- Redis ACL 必须写入外置安全状态目录
- 备份产物必须加密且失败时清理半成品

## 机器通道与 Runner

- `runner-agent` 默认必须使用 HTTPS 网关地址
- 非 loopback 主机必须强制 HTTPS
- 自定义 CA 文件必须在启动时校验为有效 PEM
- 节点 bootstrap 回执必须明确 HTTPS 默认要求

## CI 与发布不可变性

- `.github/workflows/*.yml` 禁止 `ubuntu-latest`
- `.github/workflows/*.yml` 禁止浮动 action 引用
- 外部镜像必须统一 `@sha256:` digest pin
- Python CI 必须使用锁文件和 `--require-hashes`
- 离线包必须按提交生成不可变 release tag，并带 `.sha256`

## 本地引导与可复现性

- `scripts/bootstrap.py` 在存在 `package-lock.json` 时必须优先使用 `npm ci`
- 仓库不得保留 `deploy/bootstrap.py`、`deploy/config-compiler.py` 之类兼容壳
- 统一入口只能是 `scripts/bootstrap.py` 和 `scripts/compiler.py`

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
