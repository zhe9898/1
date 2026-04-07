# ADR 0017: 全量审计技术债治理与优先级决策

- Status: Accepted
- Date: 2026-03-22
- Scope: 全量审计技术债治理与优先级决策

> Source of truth: code and tests override ADR text. See ADR 0052 when documentation and implementation diverge.

## 全量深度审计——技术债识别、评级与治理路线

- **状态**: 接受
- **日期**: 2026-03-22
- **触发**: 2026-03-22 全量代码审计（覆盖 backend/ frontend/ scripts/ installer/ docs/）

---

## 1. 背景上下文

经过多轮迭代（ADR 0015 拦截器执法、ADR 0016 SSE Client-Token 心跳打通、P0 审计修复），系统核心架构已达到准售级。
全量深度审计在确认 **P0 无阻断** 的前提下，识别出 P1~P3 共 **8 项技术债**，涉及安全策略、代码卫生、测试覆盖与 CI 门禁。

本 ADR 的目的是：
1. **统一评级标准**，避免技术债散落在口头讨论中。
2. **锁定治理优先级**，为每一项制定明确的处置方案与交付里程碑。
3. **防止回归**，通过 ADR 留痕确保后续迭代不重复引入已知缺陷。

---

## 2. 缺陷清单与决策

### P1 — 架构/安全/一致性（下一迭代优先处理）

#### P1-01: SSE 匿名连接策略需产品确认

| 维度 | 内容 |
|------|------|
| **现状** | `GET /api/v1/events` 无 JWT 鉴权（EventSource API 不支持 Header），`POST /api/v1/events/ping` 需 JWT。未登录用户可建连但 45s 后因无法 Ping 而自动断开。 |
| **风险** | 产品层未明确声明此行为是"特性"还是"漏洞"。若匿名短时连接不可接受，需收紧前端建连时机。 |
| **决策** | **产品确认后二选一**：<br/>**A) 接受当前行为**（推荐）：在 API 文档中标注"未携带合法 client_token 的连接将在 45s 内被安全断开"，ADR 0016 已覆盖此设计。<br/>**B) 仅登录建连**：在 `App.vue` 的 `onMounted` 中加入 `if (!authStore.token) return` 守卫，阻止未登录状态下的 EventSource 初始化。 |
| **交付** | 产品确认后，若选 B 则 1 行代码 + 1 行测试。 |

#### P1-02: `client_token` 必选语义需 API 文档化 (已修复)

| 维度 | 内容 |
|------|------|
| **现状** | 无 `client_token` 或格式非法时，服务端兜底生成 UUID 并首包回显，但调用方无法续期，45s 断开。 |
| **已完成** | SSE docstring 已补充明确说明；ADR 0016 已记录此设计决策。 |
| **决策** | 在下一版 OpenAPI 导出时，将 `client_token` 标注为 `recommended` 参数，附带行为说明。 |

#### P1-03: `toggle_switch` 与 `build_switch_event` 的 `updated_by` 不一致 (已修复)

| 维度 | 内容 |
|------|------|
| **现状** | `set_switch` 使用 `current_user.get("sub", "manual_override")`，`build_switch_event` 原先硬编码 `"manual_override"`。 |
| **已完成** | L226 已改为传入实际 `updated_by` 变量。`grep "manual_override" routes.py` 仅 L218 `get()` 默认值残留（正确防御）。 |

---

### P2 — 可靠性 / 可维护性（当前迭代内处理或挂起）

#### P2-01: `gateway_routes.py` 死代码清理

| 维度 | 内容 |
|------|------|
| **现状** | 含内存 `_sse_last_ping` 与未挂载路由；已在 docstring 标注 `@deprecated`；生产不走此路径。 |
| **风险** | DRY 违反、新人误用、仓库噪声。 |
| **决策** | **分步清理**：<br/>1. 将 `shred` 等仍有参考价值的端点迁移至 `api/routes.py` 或独立模块。<br/>2. 迁移完成后彻底删除 `gateway_routes.py`。<br/>3. 更新 `ARCHITECTURE_CODE_COMPLIANCE_REVIEW.md` 中的 SRP 拆分记录。 |
| **交付** | 下一迭代，预计工时 2h。 |

#### P2-02: SSE EXISTS 异常类型加固 (已修复)

| 维度 | 内容 |
|------|------|
| **现状** | EXISTS 异常捕获原仅含 `OSError` 等。 |
| **已完成** | L369 except 元组已新增 `ConnectionError`，覆盖 redis-py 连接级异常。 |

#### P2-03: `agent.py` 大模块拆分

| 维度 | 内容 |
|------|------|
| **现状** | `backend/api/agent.py` 约 900+ 行，认知负荷高，回归测试成本大。 |
| **风险** | 修改一处可能影响多个不相关功能；Code Review 效率低。 |
| **决策** | **规划拆分 ADR（独立）**：按职责拆为 `agent_chat.py`（对话流）、`agent_tools.py`（工具调用）、`agent_memory.py`（记忆管理）。需独立 ADR（预编号 0018）记录拆分边界与迁移策略。 |
| **交付** | 后续迭代，预计工时 4h。 |

---

### P3 — 运维 / CI / 测试债（计划迭代处理）

#### P3-01: Bandit 安全扫描非阻断

| 维度 | 内容 |
|------|------|
| **现状** | CI 中 Bandit 以 `\|\| true` 运行，生成报告但不阻断合并。 |
| **风险** | 安全门禁偏软，依赖人工查看 artifact；与法典"安全门禁阻断合并"字面要求有差距。 |
| **决策** | **分阶段收紧**：<br/>1. **近期**：设置 `--severity-level medium` 阈值，仅高危阻断。<br/>2. **中期**：去掉 `\|\| true`，全面硬门禁。<br/>3. 同步维护 `.bandit.yaml` 白名单，避免误报阻塞。 |
| **交付** | 下一 CI 迭代。 |

#### P3-02: 核心路由 SSE/Ping 测试缺口

| 维度 | 内容 |
|------|------|
| **现状** | `api/routes.py` 的 SSE 建连、Ping 续期、EXISTS 超时断开、ADR 0013 降级等关键路径无专用单测。`test_gateway_routes.py` 仍针对旧模块。 |
| **风险** | 回归风险高；核心路径变更无自动化守护。 |
| **决策** | **新增 `tests/test_routes_sse_ping.py`**，覆盖：<br/>1. 带 `client_token` 建连 → 首包回显 `connection_id`。<br/>2. `POST /events/ping` → Redis SETEX 验证。<br/>3. 无 Ping 45s 后连接断开（mock Redis `exists` 返回 0）。<br/>4. Redis 不可用时免死金牌（mock Redis 抛异常，连接不断）。<br/>使用 `httpx.AsyncClient` + `unittest.mock.AsyncMock` for Redis。 |
| **交付** | 下一迭代，预计工时 3h。 |

#### P3-03: 高风险脚本缺乏变更演练清单

| 维度 | 内容 |
|------|------|
| **现状** | `scripts/bootstrap.py`、`deploy/bootstrap.py`、`installer/main.py` 涉及子进程、网络、文件系统操作，属变更高风险区。 |
| **决策** | **建立变更演练清单**（`docs/ops/script-change-checklist.md`），要求任何修改这些文件时必须在 PR 中附带演练记录。 |
| **交付** | 后续迭代，预计工时 1h。 |

---

## 3. 评估对比（治理策略）

| 策略 | 优势 | 劣势 |
|------|------|------|
| **一次性全修** | 债务清零 | 变更面极大，回归风险高 |
| **按优先级分迭代治理**（选定） | 风险可控，每次变更可独立验证 | 债务存在周期较长 |
| **仅文档化不修** | 零代码风险 | 债务持续累积 |

## 4. 最终决定

采用 **按优先级分迭代治理** 策略：

- **当前迭代（已完成）**：P1-02、P1-03、P2-02 已修复并验证。
- **下一迭代**：P1-01 产品确认、P2-01 gateway 清理、P3-01 Bandit 收紧、P3-02 SSE 测试补全。
- **后续迭代**：P2-03 agent.py 拆分（独立 ADR 0018）、P3-03 演练清单。

## 5. 影响范围

- **安全性**：Bandit 硬门禁将提升 CI 安全基线。
- **可测试性**：SSE/Ping 测试补全后，核心通信路径具备自动化回归守护。
- **可维护性**：gateway_routes 清理与 agent.py 拆分将降低认知负荷。
- **部署流程**：无影响——所有治理项均为增量改进，不改变 IaC 管道。

## 6. 治理进度追踪

| 编号 | 状态 | 预计完成 |
|------|------|----------|
| P1-01 | 🟡 待产品确认 | 下一迭代 |
| P1-02 | ✅ 已完成 | 2026-03-22 |
| P1-03 | ✅ 已完成 | 2026-03-22 |
| P2-01 | 🔲 待排期 | 下一迭代 |
| P2-02 | ✅ 已完成 | 2026-03-22 |
| P2-03 | 🔲 待排期 | 后续迭代 (ADR 0018) |
| P3-01 | 🔲 待排期 | 下一 CI 迭代 |
| P3-02 | 🔲 待排期 | 下一迭代 |
| P3-03 | 🔲 待排期 | 后续迭代 |
