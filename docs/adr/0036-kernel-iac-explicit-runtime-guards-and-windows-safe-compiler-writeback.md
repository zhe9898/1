# ADR 0036: Kernel IaC 显式运行守卫与 Windows 安全写回

- 状态：Accepted
- 日期：2026-03-27

## 背景

默认 `gateway-kernel` 已经收口为唯一默认产品，但 `system.yaml` 仍残留一类“看起来可用、实际靠编译器兜底”的配置：

1. 默认 kernel 服务没有在 IaC 中显式声明 `restart`、`logging`、`stop_grace_period`
2. `caddy/postgres/redis/gateway/sentinel/docker-proxy` 仍依赖编译器注入默认 `healthcheck`
3. `gateway/redis` 的 `ulimits.nofile` 和 `gateway/redis/sentinel/docker-proxy` 的 `oom_score_adj` 仍由策略层自动补齐
4. `render-manifest.json` 会持续记录 `policy_injections` 和 `tier3_warnings`，导致默认 kernel 不是零告警基线

同时，在 Windows 上执行 `scripts/compiler.py -o .` 时，`Path.replace()` 偶发触发 `WinError 5`，会让根目录产物写回失败，即使编译结果本身是正确的。

## 决策

我们将默认 kernel 的运行守卫正式上收到 `system.yaml`，并规定：

1. 默认 kernel 的运行约束必须显式声明在 IaC，不再依赖编译器兜底注入
2. 默认 `render-manifest.json` 中 `policy_injections` 和 `tier3_warnings` 必须为空数组
3. `scripts/compiler.py` 仍优先使用原子替换写回文本产物
4. 当 Windows 上的原子替换被拒绝时，编译器允许对 `docker-compose.yml` 和 `.env` 走受控覆盖回退，避免因为文件锁导致整次编译失败

## 具体落地

- `system.yaml`
  - 为默认 kernel 服务显式补齐 `restart`
  - 显式补齐 `logging`
  - 显式补齐 `stop_grace_period`
  - 显式补齐默认 `healthcheck`
  - 显式补齐 `ulimits.nofile`
  - 显式补齐 `oom_score_adj`
- `scripts/compiler.py`
  - 增加 `_replace_text_artifact(...)`
  - 保留原子替换优先级
  - Windows 原子替换失败时回退到文本覆盖写入
- 测试
  - 默认 kernel IaC 零注入/零 warning 合同
  - Windows 写回回退单测

## 影响

### 正向影响

- 默认 kernel 成为真正的显式 IaC 基线，而不是“运行靠编译器补”
- `render-manifest.json` 可以直接作为零告警发布证据
- Windows 本地开发和发布流程不再因为 `WinError 5` 偶发失败

### 代价与约束

- `system.yaml` 变得更啰嗦，但这属于必要的显式性成本
- 编译器需要维护一个仅针对文本产物的 Windows 写回回退逻辑

## 不做的事

- 不把编译器兜底默认值全部删除；非默认服务仍允许保留兼容注入能力
- 不把 Windows 写回问题扩展成通用二进制文件替换逻辑；本次仅覆盖文本产物
