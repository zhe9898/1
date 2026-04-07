# ADR 0047: 控制台与 Runner 回归密度补齐

- Status: Accepted
- Date: 2026-03-28
- Scope: 控制台与 Runner 回归密度补齐

> Source of truth: code and tests override ADR text. See ADR 0052 when documentation and implementation diverge.

## 决策

为控制面主链补齐直接回归护栏：

1. 前端新增直接挂页面的动作测试，覆盖 `Dashboard / Nodes / Jobs / Connectors` 的后端驱动动作与过滤回拉。
2. Runner 新增 `client / heartbeat / jobs` 包级测试，直接覆盖 Bearer 头、Envelope 解包、心跳载荷、任务执行成功链和失败链。
3. 将这批回归纳入默认发布门禁，和现有后端、合规、IaC 验证一起执行。

## 影响

正面影响：

- 控制台动作链不再只靠人工点击验证。
- Runner 协议改动会更早暴露在包级测试，而不是等到 `service` 级失败才发现。
- 后续继续推进 pack 交付时，前后端和执行端的回归护栏更厚。

成本：

- 前端测试桩和假数据维护量增加。
- Runner 测试需要持续与 API 合同同步。

## 落地

- 前端新增 `frontend/tests/control_plane_actions.spec.ts`
- Runner 新增：
  - `runner-agent/internal/api/client_test.go`
  - `runner-agent/internal/heartbeat/heartbeat_test.go`
  - `runner-agent/internal/jobs/poller_test.go`

## 不包含

- 不在这一轮引入 E2E 浏览器测试框架。
- 不在这一轮引入移动端原生客户端测试仓。
