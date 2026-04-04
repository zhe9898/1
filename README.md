# ZEN70 Gateway Kernel

分布式任务调度与控制面内核，提供节点注册、任务派发、连接器管理和后端驱动控制台。

---

## 快速启动

```bash
# 启动 Web 图形化安装向导
python start_installer.py
```

访问 `http://127.0.0.1:8080` 开启安装向导。

**前置要求**：Docker 已安装且 Daemon 运行中。

---

## 核心定位

- **产品名称**：ZEN70 Gateway Kernel
- **默认 Profile**：gateway-kernel
- **核心能力**：
  - 节点注册与心跳管理
  - 任务队列与调度
  - 连接器注册与调用
  - 后端驱动控制台
  - Pack 扩展合同层

---

## 默认服务（7个）

| 服务 | 说明 |
|------|------|
| `caddy` | 反向代理与 TLS 终结 |
| `postgres` | 持久化存储（pgvector） |
| `redis` | 缓存与会话 |
| `gateway` | 控制面 API（FastAPI） |
| `runner-agent` | Go 任务执行器 |
| `sentinel` | 拓扑监控与路由协调 |
| `docker-proxy` | Docker Socket 代理 |

---

## 默认控制面（5个页面）

| 页面 | 路由 | 说明 |
|------|------|------|
| Dashboard | `/` | 控制面概览 |
| Nodes | `/nodes` | 节点舰队管理 |
| Jobs | `/jobs` | 任务队列与调度 |
| Connectors | `/connectors` | 连接器注册与调用 |
| Settings | `/settings` | 系统设置（管理员） |

---

## Pack 扩展系统

Gateway Kernel 通过 Pack 系统提供扩展能力，但 **Pack 实现不在默认内核中**：

| Pack | 说明 | 交付阶段 |
|------|------|----------|
| `iot-pack` | IoT 设备接入、场景、调度 | runtime-present |
| `ops-pack` | 可观测性与能耗监控 | runtime-present |
| `health-pack` | 健康数据采集（原生客户端） | mvp-skeleton |
| `vector-pack` | 向量检索与语义搜索 | contract-only |

Pack 通过 `system.yaml` 的 `deployment.packs` 字段启用。

**重要**：Pack declaration = capability contract，不等于默认内核自动加载其路由。

---

## 目录结构

| 目录 | 说明 |
|------|------|
| `backend/` | FastAPI 控制面 API |
| `frontend/` | Vue 3 后端驱动控制台 |
| `runner-agent/` | Go 任务执行器 |
| `scripts/` | 部署与编译脚本 |
| `config/` | IaC 运行时产物（Caddyfile 等编译输出） |
| `docs/` | 架构与文档 |

---

## 快速命令

```bash
# Web 图形化安装
python start_installer.py

# 命令行部署
python scripts/bootstrap.py

# 系统诊断
./zen70-doctor.sh

# 编译配置（dry-run）
python scripts/compiler.py --dry-run
```

---

## 架构原则

- **IaC 唯一事实源**：所有配置收束于根目录 `system.yaml`
- **调度策略唯一入口**：所有调度配置通过 `PolicyStore` 单例消费（ADR 0049）
- **后端驱动控制台**：前端无独立业务逻辑
- **协议闭环**：能力通过 `/api/v1/capabilities` 暴露
- **硬件解耦**：通过能力标签和 `AcceptedKinds` 调度，而非硬件型号
- **Pack 分层**：业务能力不回流默认 Kernel
- **三层字段解析**：IaC 编译器对每个字段采用 system.yaml 优先 → 内置默认 → 全局兜底（ADR 0051）

---

## 文档索引

完整文档见 [docs/INDEX.md](docs/INDEX.md)：

- [架构设计](docs/ZEN70_Architecture_V2.md)
- [扩展指南](docs/EXTENSIBILITY.md)
- [Kernel 发版清单](docs/kernel-release-checklist.md)
- [控制面路线图](docs/control-plane-phase-roadmap.md)
- [ADR 索引](docs/adr/README.md)

---

## Git 推送说明

- 禁止硬编码本机路径
- 生成文件已在 `.gitignore` 中排除
- 使用 `pathlib.Path` 处理路径

---

## PR 审查清单

- [ ] 硬件零硬编码（能力标签调度）
- [ ] IaC 隔离断崖（system.yaml 唯一源）
- [ ] 协议驱动 UI（后端驱动渲染）
