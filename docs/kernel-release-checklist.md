# ZEN70 Gateway Kernel Release Checklist

## 身份与入口

- 正式产品：`ZEN70 Gateway Kernel`
- 正式 runtime profile：`gateway-kernel`
- 正式编译入口：`python scripts/compiler.py system.yaml -o .`
- 正式 bootstrap 入口：`python scripts/bootstrap.py`
- 正式 pack 入口：`deployment.packs` / `GATEWAY_PACKS`

## 默认运行面

- 默认服务集：`caddy` `docker-proxy` `gateway` `postgres` `redis` `runner-agent` `sentinel`
- 默认控制面：`dashboard` `nodes` `jobs` `connectors` `settings(admin)`
- 默认请求链只承载 kernel 控制面，不承载业务执行面

## 编译与产物

- `system.yaml`、`.env`、`docker-compose.yml`、`render-manifest.json` 必须一致
- `requested_packs` / `resolved_packs` 必须与 pack 合同一致
- `docs/openapi-kernel.json` 与 `contracts/openapi/zen70-gateway-kernel.openapi.json` 必须同步
- 仓库中不得存在 `deploy/config-compiler.py`、`deploy/bootstrap.py` 等兼容 wrapper

## 安全与治理

- 人类控制面请求必须通过租户作用域与认证边界
- 机器通道必须统一使用 `Authorization: Bearer <node_token>`
- RLS readiness、JWT runtime 校验、镜像 digest pin、Redis ACL 外置注入都必须通过
- `MACHINE_API_ALLOWLIST` 必须存在并收口机器入口

## Pack 边界

- Pack 必须显式声明能力、router、service、runtime owner、delivery stage
- `iot-pack` 可映射到 `gateway-iot` 镜像目标，但不得形成第二 runtime profile
- `health-pack` 和 `vector-pack` 的 maturity 叙事必须与代码一致
- 默认 kernel 路径不得回流业务 pack 执行逻辑

## 禁止项

- 禁止重新引入 bundle preset、legacy profile 或兼容壳
- 禁止让运行时模块直接读 YAML 决策
- 禁止把前端重新做成独立业务事实源
