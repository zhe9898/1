# ADR 0022: Tiered Release Smoke Gate (分层发布冒烟门禁)

- **状态**: 已接受 (Accepted 2026-03-25)
- **影响域**: CI/CD, 部署验证, 架构守门 (SRE)

## 背景

在 ZEN70 架构的演进中，我们发现纯粹的“非黑即白 (0 或 1)” 验收测试会导致不可接受的发布阻塞。系统中存在严格的 **Critical** 路径（如认证、网关健康、基础能力发现），以及允许功能降级的 **Non-Critical** 路径（如媒体刮削状态、SSE 实时推送）。

如果在发布预检 (`release.sh` 之前) 或部署后验证 (`postdeploy_verify.py`) 中，若 Non-Critical 路径波动（例如网络延迟导致 SSE 握手偶现超时）就直接阻断发布 (exit 1)，会导致极高的运维摩擦与错误的警报疲劳。同时，旧版的探针只检查 Redis 存活，忽略了 PostgreSQL 的宕机，导致放行了实际上已有严重隐患的构建。

## 决策

1. **多重依赖联合健康判定 (Health Probe Fix)**  
   后端 `/health` 端点必须验证 **所有** 核心 Stateful 依赖组合。
   - `healthy`: Redis (`ok`) AND Postgres (`ok`)
   - `degraded`: One of them is not `ok` (e.g., Redis `ok`, Postgres `error`)
   - `unhealthy`: Both are not `ok`

2. **分层验证退出码约定 (Tiered Exit Codes)**  
   无论是 `preflight_smoke.py`、`release_smoke_gate.py` 还是 CI Pipeline 脚本，必须强制遵循三态退出码契约：
   - `0` (Success): 所有的检查点（Critical 和 Non-Critical）全部通过，或服务主动报告为安全的 `degraded` 状态。
   - `1` (Critical Block): 任何一个 Critical 端点（如 `/health`, `/api/v1/auth/sys/status`）响应异常或格式错误。系统强制阻断发布。
   - `2` (Non-Critical Warn): 所有 Critical 端点通过，但存在 Non-Critical 端点异常（如 SSE 不可达）。此时流水线可配置为“黄色警告（带伤发布）”或通过人工确认后继续。

## 后果

- **优点**：
  - 架构的韧性直接反映在了发布流水线上，允许微服务级别“带伤作战”。
  - 避免了“偶发依赖抖动”锁死整条交付管线。
  - `/health` 联合状态精准制止了隐形脑裂。
- **代价**：
  - CI (如 GitHub Actions / GitLab CI) 需要专门捕获 `exit 2` 并配置 `continue-on-error` 或 Manual Approval 节点，不能使用简单的 `set -e` 粗暴拦截所有非 0 退出码。
