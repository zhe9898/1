# ADR 0010: Unified Response Envelope (Protocol-first)

- Status: Amended
- Date: 2026-03-24
- Scope: Unified Response Envelope (Protocol-first)

> Source of truth: code and tests override ADR text. See ADR 0052 when documentation and implementation diverge.

## 决策

采用 **可辨识联合类型 (Discriminated Union)**，以 `code` 字段作为判别符，将成功响应与错误响应明确为两种不同的形状：

### SuccessResponse

网关层对所有 **成功 JSON 响应（status_code < 400）** 强制包装：

```json
{
  "code": "ZEN-OK-0",
  "message": "ok",
  "data": { "original_payload": "..." }
}
```

- `code` 固定为 `"ZEN-OK-0"`
- `message` 固定为 `"ok"`（或简要操作完成描述）
- `data` 承载原始业务载荷
- **不包含** `recovery_hint` 和 `details`（这些字段仅属于错误响应）

### ErrorResponse

```json
{
  "code": "ZEN-xxx",
  "message": "人类可读错误信息",
  "recovery_hint": "恢复建议",
  "details": { "request_id": "...", "field_errors": {} }
}
```

- `code` 为 `"ZEN-"` 前缀的错误码（非 `ZEN-OK-0`）
- `recovery_hint` 提供用户侧恢复建议
- `details` 承载调试与定位信息
- **不包含** `data` 字段

### 前端解包规则

前端通过 Axios 响应拦截器自动解包，判别条件：

```typescript
if (obj.code === "ZEN-OK-0" && Object.prototype.hasOwnProperty.call(obj, "data")) {
  response.data = obj.data; // 剥离 envelope，使调用点直接读取业务数据
}
```

## 修订理由

初版 ADR 要求成功响应也包含 `recovery_hint: ""` 和 `details: {...}`。经架构审计发现此设计违反接口隔离原则 (ISP)：

1. `recovery_hint` 对成功响应无语义意义，是纯噪音
2. `details.request_id` 已通过 HTTP Header `X-Request-ID` 注入，在 Body 中重复违反 DRY
3. 强制共用字段导致前端解包条件过于复杂，引发了全域协议断裂 BUG

后端 `main.py` 的 `success_envelope` 中间件自始至终只输出 `{code, message, data}`，**实现优于文档**。本次修订使文档对齐实现。

## 后果

- 优点：
  - 协议驱动 UI 更彻底：成功与错误通过 `code` 统一判别
  - 成功响应体积更小，减少序列化开销
  - 前端解包条件简洁可靠（`code === "ZEN-OK-0"` + `hasOwnProperty("data")`）
  - 类型系统完美支持 TypeScript Discriminated Union
- 代价：
  - OpenAPI 中的响应 schema 需要同步升级（后续用 contracts 自动生成 TS 类型）
  - 任何绕过 Axios 的调用（如 ADR 0015 豁免的 push.ts）需手动判别 `code` 解包
