# ADR 0040: 登录前租户合同、临时编译 secrets 治理与全仓不可变 workflow

- Status: Accepted
- Date: Unknown
- Scope: 登录前租户合同、临时编译 secrets 治理与全仓不可变 workflow

> Source of truth: code and tests override ADR text. See ADR 0052 when documentation and implementation diverge.

## 背景

本轮自审确认了 4 类仍会破坏控制面边界的问题：

1. `runtime/tmp-compile/**` 被错误留在仓库中，且包含 `runtime/tmp-compile/runtime/secrets/users.acl` 明文 Redis ACL。
2. `connectors` API 虽然已经走 `get_tenant_db()`，但代码层主要仍按 `connector_id` 查询，过度依赖 RLS 单点。
3. 多租户登录前合同已经切到 `tenant_id + username`，但前端登录页和共享 API 常量没有完全跟上。
4. `.github/workflows/*.yml` 仍有 `ubuntu-latest`、`@v*`、`@master` 之类可漂移输入，不满足发布可审计和可复现要求。

## 决策

### 1. 临时编译产物一律视为不可信运行态垃圾

- `runtime/tmp-compile/` 加入仓库忽略。
- 从索引中移除所有已跟踪的 `runtime/tmp-compile/**` 文件。
- 离线打包显式排除 `runtime/tmp-compile/`。
- repo hardening 测试阻断任何 `runtime/tmp-compile`、`runtime/secrets`、`config/users.acl` 被重新跟踪。

### 2. connectors 继续做租户双保险，不只靠 RLS

- `Connector` 的唯一性收口为 `(tenant_id, connector_id)`。
- `upsert/list/invoke/test` 统一显式附加 `Connector.tenant_id == current_user.tenant_id`。
- 继续保留 `get_tenant_db()` 与 RLS，形成“查询显式 tenant + 会话级 tenant context”双保险。

### 3. 登录前合同必须明确 tenant，不允许前端回退默认全局语义

- 前端共享 API 常量补齐 `AUTH.pinLogin` 与 `AUTH.pinSet`。
- 登录页显式收集 `tenantId`，并在 password / WebAuthn 登录链路上发送 `tenant_id`。
- 前端角色集合与后端签发角色集合对齐，保留 `superadmin`、`admin`、`geek`、`family`、`child`、`elder`、`guest`、`user`。

### 4. 全仓 workflow 输入必须冻结

- 所有 `.github/workflows/*.yml` 的 `runs-on` 固定到 `ubuntu-24.04`。
- 所有 `uses:` action 固定为 commit SHA，而不是 `@v*`、`@master` 或 `@main`。
- repo hardening 测试和离线 bundle workflow 双重扫描 workflow 漂移输入。

## 结果

### 正向结果

- `tmp-compile` 再次混入 secrets 会被 gitignore、repo hardening 测试和离线包排除同时拦住。
- `connectors` 的租户边界不再只靠数据库侧保护，代码层也会直接拒绝跨租户命中。
- 多租户登录从“后端支持、前端默认忽略”变成真正前后端闭环。
- workflow 漂移输入被全仓冻结，CI / 发布路径更可追溯。

### 代价

- 登录页新增一个 `tenant` 输入，单租户默认仍填 `default`。
- workflow 维护会更机械，需要显式更新 SHA。
- `connectors` 从全局命名转为租户命名，旧环境依赖全局唯一时需要接受新约束。

## 落地文件

- `E:/3.4/.gitignore`
- `E:/3.4/backend/api/connectors.py`
- `E:/3.4/backend/db.py`
- `E:/3.4/backend/models/connector.py`
- `E:/3.4/frontend/src/utils/api.ts`
- `E:/3.4/frontend/src/stores/auth.ts`
- `E:/3.4/frontend/src/views/Login.vue`
- `E:/3.4/.github/workflows/ci.yml`
- `E:/3.4/.github/workflows/compliance.yml`
- `E:/3.4/.github/workflows/docker-publish.yml`
- `E:/3.4/.github/workflows/build_offline_v2_9.yml`
- `E:/3.4/tests/test_repo_hardening.py`

## 验证

- 后端单测通过
- 合规测试通过
- 前端 `lint / vitest / build` 通过
- `generate_contracts` 通过
- `compiler --dry-run` 通过
