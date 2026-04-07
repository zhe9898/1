# 0041. 启动期 RLS 证明、外置 ACL 状态与 digest 固定镜像

## 状态

已采纳

## 背景

控制面已经具备 `tenant_id`、RLS、机器通道 token 和离线发布门禁，但仍存在 4 个高风险缺口：

1. RLS 仅在 `init_db()` 中应用，API 主启动链未证明“策略已经真实生效”。
2. Redis ACL 产物落在仓库运行目录时，容易被误提交、误打包或误备份。
3. 外部镜像只禁 `latest` 不够，`caddy:2`、`redis:7-alpine` 这类 tag 仍然可漂移。
4. 非 production 环境允许 JWT 回落到弱默认密钥，存在环境标记错误时带弱密钥上线的风险。

这些问题会直接影响多租户隔离、供应链可复现性和基础设施凭据治理。

## 决策

### 1. API 启动必须证明 RLS 已就绪

- API 启动期必须执行 RLS 运行模式校验。
- API 启动期必须用真实数据库 session 执行 `assert_rls_ready()`。
- 任一 tenant 表缺失 `tenant_id`、未开启 `FORCE ROW LEVEL SECURITY`、缺失策略时，启动直接失败。
- `ZEN70_RLS_ALLOW_SOFT_FAIL=true` 仅允许在非 production 环境下生效；production 一律 fail-fast。

### 2. 租户绑定依赖层统一化

- 人类控制面接口默认走 `get_tenant_db()`。
- 机器控制面接口统一走 `get_machine_tenant_db()`，先绑定 `tenant_id` 再执行业务查询。
- `jobs/nodes/connectors` 关键读取除了 RLS 外，还必须显式附带 `tenant_id` 过滤，避免单点依赖 RLS。

### 3. Redis ACL 状态外置

- 编译器不再默认向仓库内 `runtime/secrets/users.acl` 写入 ACL。
- 默认 ACL 输出路径迁移到用户外置安全状态目录：
  `~/.zen70/runtime/secrets/users.acl`
- 若配置仍试图把 ACL 写入仓库路径，编译器直接失败。
- `.env` 通过 `REDIS_ACL_FILE` 把外置路径传给 compose。
- 仓库、离线包和预检必须阻断 `runtime/secrets/`、`runtime/tmp-compile/`、`config/users.acl`。

### 4. 外部镜像必须固定到 digest

- `system.yaml` 与 `tests/docker-compose.yml` 中所有外部镜像必须使用 `@sha256:...`。
- 本地构建镜像 `zen70-gateway`、`zen70-runner-agent` 不在这条规则内。
- workflow 必须阻断 `latest`、可变 action ref、可变 runner 标签。
- `docker-publish` 只保留不可变 tag（SHA / semver），移除 branch / pr / schedule 可变标签。

### 5. JWT 运行时必须显式就绪

- 启动期必须调用 `assert_jwt_runtime_ready()`。
- 任何环境都不允许继续依赖默认弱密钥值。
- 密钥缺失、仍为默认值或长度不足时，启动直接失败。

## 结果

- 控制面启动从“假定隔离存在”改成“证明隔离存在”。
- Redis ACL 明文不再落在仓库运行目录，泄露面明显收缩。
- 离线包与主干配置的镜像输入进入 digest 冻结状态。
- JWT 运行时从“开发宽松”切换为“统一显式配置”。

## 验证

- 新增真实 PG 策略检查集成测试：启动前后验证 RLS 真正落库。
- 新增 `get_tenant_db()` 在无 RLS 时必须返回 `503` 的负向测试。
- 新增外部镜像 digest 门禁测试。
- 新增 runtime secrets / tmp 编译目录泄露扫描测试。
- 新增编译器 ACL 外置路径与仓库路径拒绝测试。

## 不包含

- 本 ADR 不引入外部 secrets manager，也不把 Redis ACL 改造成纯内存注入。
- 本 ADR 不改变本地构建镜像的产物模型。
