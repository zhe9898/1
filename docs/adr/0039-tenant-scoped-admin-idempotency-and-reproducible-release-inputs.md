# 0039 租户作用域管理、控制面复合唯一键与可复现发布输入

## 状态

已采纳

## 背景

控制面完成 `tenant_id` 主链路后，仍有几个高风险口没有闭合：

- `PIN` 登录签发 JWT 时没有携带真实 `tenant_id`，默认会落到 `"default"`。
- `users.username` 仍是全局唯一，而登录前认证路径又只按 `username` 查人。
- `jobs.idempotency_key` 仍是全局唯一，不是按租户隔离。
- `nodes.node_id` 仍是全局唯一，不利于租户独立命名空间。
- 用户管理接口仍偏“全局 admin”语义，租户管理员边界不清。
- RLS 初始化失败时默认软返回，存在“部分表未受保护但服务继续启动”的风险。
- 离线发布流程与 `system.yaml` 仍保留漂移输入，难以做到同 commit 可复构。

## 决策

### 1. 管理员默认是租户管理员

- `admin` 角色默认仅管理自身 `tenant_id`。
- `superadmin` 作为保留的全局治理角色，仅在显式签发时拥有跨租户权限。
- 用户列表、创建用户、吊销凭证、创建邀请都必须按租户作用域过滤。

### 2. JWT 必须传播真实租户上下文

- `bootstrap`、`password_login`、`pin_login`、邀请完成登录等路径，签发 JWT 时必须携带真实 `tenant_id` 与真实 `role`。
- 任何默认 `"default"` 的租户回退都只能用于防御性兜底，不能覆盖真实用户租户。

### 3. 控制面唯一性按租户收口

- `users.username` 的唯一性改为 `(tenant_id, username)`。
- `jobs.idempotency_key` 的唯一性改为 `(tenant_id, idempotency_key)`。
- `nodes.node_id` 的唯一性改为 `(tenant_id, node_id)`。
- 登录前认证路径也必须同步切到 `tenant_id + username`，避免同名用户歧义。
- 相关查询与冲突处理都必须同步按租户维度执行。

### 4. RLS 初始化默认 fail-fast

- RLS 策略应用失败时，默认阻断启动并输出预期受保护表清单。
- 仅允许通过显式环境变量开启软失败，供本地排障或特殊迁移窗口使用。

### 5. 离线发布输入必须冻结

- Workflow 使用固定的 GitHub Action commit SHA。
- Workflow 使用固定的 runner 镜像，不再使用浮动 runner 标签。
- `system.yaml` 内部镜像与外部依赖镜像都必须写成显式版本引用，不允许漂移标签。
- 离线包必须输出 `image-lock.txt`、bundle SHA256，并禁止覆盖同名发布资产。

## 结果

- PIN / 密码 / WebAuthn 登录不再错绑租户上下文，也不再依赖全局唯一用户名。
- 不同租户可以安全复用相同的 `idempotency_key` 和 `node_id`。
- 租户管理员越界面被收紧，控制面多租户语义更清晰。
- RLS 失败不再静默放过。
- 离线构建输入更稳定，审计与回滚链路可追溯性更强。

## 代价

- 用户管理与控制面查询都必须持续维护租户作用域测试。
- `superadmin` 被保留为特殊治理角色，前后端都不能把它当成普通租户角色随意扩散。
- 外部镜像版本升级需要显式修改仓库配置，而不是依赖漂移标签。
