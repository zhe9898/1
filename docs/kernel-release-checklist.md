# ZEN70 Gateway Kernel Release Checklist

## 身份与入口

- 正式产品名: `ZEN70 Gateway Kernel`
- 正式 runtime profile: `gateway-kernel`
- 正式编译入口: `python scripts/compiler.py system.yaml -o .`
- 正式 bootstrap 入口: `python scripts/bootstrap.py`
- Pack 入口: `deployment.packs` / `GATEWAY_PACKS`

## 默认运行形态

- 默认部署模型必须是 `host-first`
- 默认宿主机进程: `gateway` `topology-sentinel` `control-worker` `routing-operator` `runner-agent`
- 默认基础设施容器: `caddy` `postgres` `redis` `nats`
- 默认不包含 `sentinel` 侧车容器
- `docker-proxy`、`watchdog`、`mosquitto`、可观测性容器都属于可选包容器，不属于默认 kernel 运行集

## 编译与产物合同

- `system.yaml`、`.env`、`docker-compose.yml`、`render-manifest.json` 必须一致
- `render-manifest.json` 必须显式包含:
  - `deployment_model = host-first`
  - `container_services_rendered`
  - `infrastructure_containers_rendered`
  - `optional_pack_containers_rendered`
  - `host_processes_rendered`
  - `migration_copy_plan`
- `docker-compose.yml` 中的服务列表必须等于 `container_services_rendered`
- `infrastructure_containers_rendered + optional_pack_containers_rendered` 必须精确等于 `container_services_rendered`
- `runtime_services_rendered` 必须等于容器集合与宿主机进程集合的并集
- `docs/openapi-kernel.json` 与 `contracts/openapi/zen70-gateway-kernel.openapi.json` 必须同步

## 多机迁移口径

- 宿主机进程复制: 复制 systemd / host runtime 产物，不复制容器编排
- 基础设施容器复制: 复制 `caddy` `postgres` `redis` `nats`
- 可选包容器复制: 只复制被明确选中的 pack 容器，例如 `docker-proxy` 和观测栈
- 迁移时不得把“复制全部服务”当成默认策略
- 详细矩阵见 `docs/host-first-multinode-migration.md`

## 安全与治理

- 人类控制面请求必须经过租户作用域与认证边界
- 机器通道统一使用 `Authorization: Bearer <node_token>`
- RLS readiness、JWT runtime 校验、镜像 digest pin、Redis ACL 外置注入必须通过
- `MACHINE_API_ALLOWLIST` 必须存在并收口机器入口

## 禁止项

- 禁止重新引入 bundle preset、legacy profile 或兼容壳
- 禁止重新引入默认 `sentinel` 侧车监督模型
- 禁止让运行时模块直接读取 YAML 做决策
- 禁止让文档或离线包继续把 `docker-proxy` 当作默认 kernel 运行时
