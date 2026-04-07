# ZEN70 Gateway Kernel

ZEN70 当前以 `Gateway Kernel` 为正式运行时形态，提供控制面 API、调度治理、连接器管理、节点注册、执行面协同和后端驱动控制台。

## 快速开始

```bash
python start_installer.py
```

安装向导默认监听 `http://127.0.0.1:8080`。

命令行编译与部署：

```bash
python scripts/compiler.py system.yaml -o . --dry-run
python scripts/bootstrap.py
```

## 当前架构结论

- 正式 runtime surface 只有 `gateway-kernel`。
- `gateway`、`gateway-core`、`gateway-iot`、`gateway-ops` 是兼容输入或 pack preset，不是新的正式产品面。
- 根 [system.yaml](system.yaml) 是声明式部署事实源；运行时调度策略读取统一经由 `PolicyStore + RuntimePolicyResolver`。
- 控制台采用 backend-driven + cookie-primary 边界：HTTP 和 SSE 默认走 cookie，前端只保留会话 claims，不长期持有 bearer token。
- 执行面由 Go `runner-agent` 驱动，节点能力以 `AcceptedKinds` 和机器身份链表达，而不是按硬件型号硬编码。
- Pack 表达能力合同，不等于默认内核自动加载对应路由或服务。

## 代码优先原则

文档不是第一事实源。仓库真相顺序为：

1. 实现代码与导出的契约
2. 测试与门禁
3. ADR 与说明文档

当文档与代码冲突时，以 [docs/adr/0052-code-backed-architecture-governance-registry.md](docs/adr/0052-code-backed-architecture-governance-registry.md) 和对应实现为准。

## 目录

- [backend](backend) FastAPI 控制面、服务层、调度治理与安全边界
- [frontend](frontend) Vue 控制台，消费后端协议与 cookie 会话
- [runner-agent](runner-agent) Go 执行面、任务轮询、续租与结果上报
- [scripts](scripts) IaC 编译、部署与仓库治理脚本
- [docs](docs) 当前文档入口与 ADR

## 文档入口

从 [docs/INDEX.md](docs/INDEX.md) 开始。高频入口：

- [docs/INDEX.md](docs/INDEX.md) 当前文档索引
- [docs/adr/README.md](docs/adr/README.md) ADR 索引
- [docs/control-plane-phase-roadmap.md](docs/control-plane-phase-roadmap.md) 控制面与执行面阶段路线
- [docs/pack-matrix.md](docs/pack-matrix.md) Pack 交付阶段与边界
- [docs/profile-matrix.md](docs/profile-matrix.md) Profile 与兼容输入
- [docs/protocol-matrix.md](docs/protocol-matrix.md) 核心协议矩阵
- [docs/EXTENSIBILITY.md](docs/EXTENSIBILITY.md) 扩展边界与守卫
- [docs/kernel-release-checklist.md](docs/kernel-release-checklist.md) 发版与仓库硬化检查

## 提交前自检

- 不要把旧 profile 或 `full-pack` 重新写回正式产品叙事。
- 不要在运行时代码里直接解析并缓存 `system.yaml` 做策略判断。
- 不要把 bearer token、secret 或后端原始错误直接暴露到前端。
- 不要让 route / worker / sentinel 直接写核心聚合状态字段。
