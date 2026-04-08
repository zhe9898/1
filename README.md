# ZEN70 Gateway Kernel

ZEN70 当前只保留一个正式 runtime surface: `gateway-kernel`。它提供 backend-driven control plane、调度治理、节点注册、作业执行协议、连接器管理，以及由后端协议驱动的控制台。

## 快速开始

```bash
python start_installer.py
python scripts/compiler.py system.yaml -o . --dry-run
python scripts/bootstrap.py
```

安装向导默认监听 `http://127.0.0.1:8080`。

## 当前架构结论

- 对外正式入口只有两件事：`gateway-kernel` runtime surface，以及 backend-driven control plane。
- `system.yaml` 是声明式部署事实源；运行时策略统一经 `PolicyStore + RuntimePolicyResolver` 解析。
- 扩展路径固定为 `capability -> surface -> policy -> service contract -> execution contract`。
- Pack 是能力合同与运行边界，不是新产品面；可选能力必须通过显式 canonical pack keys 打开。
- 执行面由 Go `runner-agent` 驱动，节点能力通过 `AcceptedKinds` 与机器身份表达，不靠硬编码机型分支。
- 控制台采用 backend-driven + cookie-primary 边界；HTTP 与 SSE 默认走 cookie，会话 claims 由后端签发和收口。

## 事实源优先级

文档不是第一事实源。仓库真相顺序为：

1. 实现代码与导出的合同
2. 测试与门禁
3. ADR 与说明文档

当文档与代码冲突时，以 [0052](docs/adr/0052-code-backed-architecture-governance-registry.md) 和对应实现为准。

## 目录

- [backend](backend) FastAPI control plane、领域服务、调度治理与安全边界
- [frontend](frontend) Vue 控制台，消费后端协议与 cookie 会话
- [runner-agent](runner-agent) Go 执行面、任务轮询、续租与结果上报
- [scripts](scripts) IaC 编译、部署与仓库治理脚本
- [docs](docs) 当前文档入口与 ADR

## 文档入口

从 [INDEX](docs/INDEX.md) 开始。高频入口：

- [ADR 索引](docs/adr/README.md)
- [控制面路线图](docs/control-plane-phase-roadmap.md)
- [Profile / Pack 矩阵](docs/profile-matrix.md)
- [协议矩阵](docs/protocol-matrix.md)
- [扩展边界](docs/EXTENSIBILITY.md)
- [发版清单](docs/kernel-release-checklist.md)

## 提交前自检

- 不要把旧 profile、bundle preset 或兼容 wrapper 写回正式产品叙事。
- 不要在运行时代码里直接解析并缓存 `system.yaml` 做策略判断。
- 不要把 bearer token、secret 或后端原始错误直接暴露到前端。
- 不要让 route、worker 或 sentinel 直接写核心聚合状态字段。
