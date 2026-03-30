# 0048. Health Pack MVP Skeleton 与 Pack 成熟度合同

- 状态: 已采纳 — _v3.43 更新：`full-pack` 已从注册表移除；`delivery_stage=compatibility-only` 条目不再存在_
- 日期: 2026-03-28

## 背景

`Gateway Kernel` 与 `pack` 的边界已经冻结，但 `health-pack` 之前仍只是 placeholder。这样会带来两个问题：

- 文档和代码都在说 `Health Pack` 已经是边界正确的业务 pack，但仓库里没有任何最小可交付物。
- 控制台和 API 只能知道 pack 是否被选中，却无法表达一个 pack 当前到底是“已有运行体”、“只有合同”还是“仅兼容输入”。

如果继续维持这种状态，`pack` 会重新滑回“命名上存在、交付上不存在”的假成熟。

## 决策

1. 为 `PackDefinition` 增加 `delivery_stage` 合同字段。
2. `/api/v1/profile` 必须把 `delivery_stage` 下发到控制台，让前端和文档消费同一个成熟度事实。
3. `health-pack` 从 placeholder 升级成 `mvp-skeleton`：
   - iOS 交付最小 Swift Package 骨架
   - Android 交付最小 Gradle/Kotlin 骨架
   - 双端都提供 `client.yaml`，明确 `Gateway Identity`、`tenant_id`、`node_token`、bootstrap receipts 与 selector 提示
4. `vector-pack` 继续保持 `contract-only`；~~`full-pack` 保持 `compatibility-only`，不冒充运行体成熟度。~~ **_v3.43: full-pack 已从 pack 注册表中彻底移除。_**

## 影响

正面影响：

- `pack` 的成熟度不再靠口头说明，而是进入 API 合同、控制台和文档。
- `health-pack` 不再只是目录占位，已经具备最小交付骨架。
- 后续推进 `Health Pack` 真正产品化时，可以在现有 skeleton 基础上演进，而不是从 placeholder 重来。

成本：

- 需要维护一层原生客户端骨架目录。
- 控制台和测试需要同步消费 `delivery_stage`。

## 落地

- `backend/core/pack_registry.py`
- `backend/api/profile.py`
- `frontend/src/types/console.ts`
- `frontend/src/stores/console.ts`
- `frontend/src/views/ControlDashboard.vue`
- `clients/health-ios/*`
- `clients/health-android/*`
- `docs/pack-matrix.md`
