# ADR 0015: 统一 HTTP 调用与拦截器强制管控网络逃逸保护

- Status: Accepted
- Date: 2026-03-22
- Scope: 统一 HTTP 调用与拦截器强制管控网络逃逸保护

> Source of truth: code and tests override ADR text. See ADR 0052 when documentation and implementation diverge.

## 背景
在前端契约审计中发现 `auth.ts` 在修改 AI 偏好时，违规使用原生 `fetch` 绕过了全局封装的 Axios `http` 实例。这种网络逃逸行为导致：
1. **链路追踪断裂**：未注入 `X-Request-ID`，后端日志无法闭环。
2. **高可用降级**：绕过了由 `http.ts` 维护的全局 503 熔断器与指数退避重连机制。
3. **安全逃逸**：缺失对 `X-New-Token` 的响应头拦截，破坏了双轨 JWT 令牌轮换。

在法典 §2 中，所有的安全降级、日志闭环和重连策略均维系于客户端网络层的钩子函数，网络逃逸对准系统级的可用性构成直接威胁。

## 决策
1. **严格禁止原生 Fetch 调用后端 API**：应用层所有对后端 `/api/v1` 的调用，必须且只能通过全局导出的 Axios `http` 实例 (`src/utils/http.ts`) 执行。
2. **允许的特殊豁免场景**：
   - 应用加载早期（无环境或无拦截器必要时，例如 Login / Invite 系统引导阶段获取系统状态）。
   - Service Worker 环境 (`push.ts`)，无法使用 Axios 时。
   - 依赖特定流式特性的特殊 API (如 SSE 订阅或大文件原始切片上传，且必须显式手动补齐 Token 与 Request-ID)。
3. **全局防腐层拦截**：任何试图绕过系统核心网络库发起的状态突变请求，在严格代码审查与自动化 Lint 规则中将被直接阻断。

## 影响面
- 彻底封锁因网络请求库不一导致的上下文状态丢失。
- 将重连、熔断及鉴权刷新收束至唯一的“网络总线”中，大幅提高断网自愈能力的健壮性。
- 新增功能在发起 IO 操作时必须复用现有的请求 Envelope 解包逻辑。
