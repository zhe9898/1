# ADR 0046: Kernel-only Runtime Surface and Compatibility Retirement

## 状态

已采纳

## 背景

`ZEN70 Gateway Kernel` 已经收口为默认产品定义，但仓库里仍存在三类容易误导运维和后续开发的兼容层：

1. `gateway-iot / gateway-ops / gateway-full / gateway / full` 这类历史 profile 名仍作为输入存在，容易被误读成正式运行时 profile。
2. `deploy/config-compiler.py` 仍像一份并列的编译器入口，削弱了 `scripts/compiler.py` 作为唯一事实源的表达。
3. `config/system.yaml` 作为旧配置入口长期与根 `system.yaml` 并列存在，容易制造“双真源”错觉。

这些问题不会立刻打坏运行时，但会持续放大运维成本、文档歧义和兼容层维护债。

## 决策

1. 公开运行时 profile surface 固定为 `gateway-kernel`。
2. `gateway-iot / gateway-ops / gateway-full / gateway / full` 只保留为 legacy compatibility input。
3. legacy 输入只能在规范化阶段展开为 `packs`，不得再作为正式产品名、公开 API profile、OpenAPI profile 或发布口径出现。
4. `deploy/config-compiler.py` 收口为对 `scripts/compiler.py` 的兼容 wrapper，不再携带独立编译逻辑。
5. 根 `system.yaml` 是唯一正式配置入口；旧 `config/system.yaml` 从仓库正式 surface 中移除。
6. 离线包和仓库门禁继续禁止 `config/system.yaml` 回流正式交付面。

## 影响

### 正向

- 运行时、打包、文档、OpenAPI 和控制台口径统一为 `gateway-kernel + packs`
- 运维不再需要判断“哪个 profile/哪个 system.yaml 才是真的”
- 兼容层边界更清晰，后续可以按版本退场

### 代价

- legacy profile 名仍需在迁移和 bootstrap 解析层保留一段时间
- 文档与测试必须明确标注“兼容输入 != 正式产品面”

## 护栏

- 正式 UI 和 API 只能暴露 `gateway-kernel`
- legacy profile 名只允许出现在兼容解析逻辑、兼容测试和迁移说明中
- 仓库与离线包校验必须阻止 `config/system.yaml` 回流
- `deploy/config-compiler.py` 只能保留 wrapper 语义，不得再次演化为第二套编译器
