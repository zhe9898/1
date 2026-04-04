# 0051. IaC 三层字段解析：system.yaml 优先、内置默认、全局兜底

- 状态: 已采纳
- 日期: 2026-04-04

## 1. 背景上下文

IaC 编译器 (`scripts/compiler.py`) 通过 `scripts/iac_core/loader.py` 将 `system.yaml` 转换为 `docker-compose.yml`、`.env` 和 `Caddyfile`。在 v3.41 及之前，`ulimits`、`oom_score_adj`、`networks` 三个字段采用硬编码默认值，`system.yaml` 中的声明会被编译器的内置值覆盖。这违反了 "system.yaml 为唯一事实源" 的架构原则（ADR 0008）。

具体问题：

- `ulimits` 硬编码为固定值，无法按服务定制。
- `oom_score_adj` 仅对部分服务设置，且不可从 system.yaml 覆盖。
- `networks` 每个服务默认加入 `backend_net`，无法声明更丰富的网络拓扑。

## 2. 决策选项

1. **方案 A — 完全声明式**：所有字段必须在 system.yaml 中显式声明，无内置默认。
2. **方案 B — 三层解析**：system.yaml 声明优先 → 服务级内置默认 → 全局兜底值。
3. **方案 C — 编译器模板覆盖**：在 Jinja2 模板中处理默认值。

## 3. 评估对比

### 方案 A（完全声明式）
- **优势**：最大透明度
- **劣势**：system.yaml 臃肿；遗漏字段导致服务启动失败

### 方案 B（三层解析）
- **优势**：用户只需声明偏离默认的部分；内置默认覆盖常见场景；向后兼容
- **劣势**：默认值逻辑在 loader.py 中，需阅读代码了解

### 方案 C（模板覆盖）
- **优势**：模板可见性好
- **劣势**：Jinja2 模板中嵌入复杂逻辑难以测试和维护

## 4. 最终决定

采用 **方案 B**：在 `loader.py` 的 `_build_service_entry()` 中对 `ulimits`、`oom_score_adj`、`networks` 实现三层解析。

### 解析规则

对于每个字段，`_build_service_entry()` 按以下优先级填充：

```
第一层：svc.get("field")  → system.yaml 中服务级声明（最高优先级）
第二层：内置默认映射       → loader.py 中按服务名匹配的默认值
第三层：全局兜底           → 所有服务共享的安全基线值
```

### 各字段的三层实现

#### Networks（网络）

| 层级 | 值 |
|------|-----|
| 第一层 | `svc.get("networks")` — system.yaml 声明 |
| 第二层 | cloudflared → `["frontend_net", "backend_net"]` |
| 第三层 | 其他服务 → `["backend_net"]` |

#### Ulimits（资源限制）

| 层级 | 值 |
|------|-----|
| 第一层 | `svc.get("ulimits")` — system.yaml 声明 |
| 第二层 | gateway / redis → `{"nofile": {"soft": 65536, "hard": 65536}}` |
| 第三层 | 其他服务 → 不设置（使用系统默认） |

#### OOM Score Adj（OOM 保护）

| 层级 | 值 |
|------|-----|
| 第一层 | `svc.get("oom_score_adj")` — system.yaml 声明 |
| 第二层 | gateway / redis / sentinel / watchdog / docker-proxy → `-999`（免死保护） |
| 第三层 | 其他服务 → 不设置（使用内核默认） |

### 安全基线

当 `svc.get("security")` 包含 `apply_baseline: true` 时，自动注入：

| 字段 | 值 |
|------|-----|
| `user` | `${PUID:-1000}:${PGID:-1000}` |
| `read_only` | `true` |
| `tmpfs` | `[/tmp]` |
| `cap_drop` | `ALL` |
| `cap_add` | `[NET_BIND_SERVICE]` |

## 5. 影响范围

正面影响：

- 运维团队可在 system.yaml 中按服务覆盖 ulimits / oom_score_adj / networks，无需修改编译器代码。
- 内置默认覆盖了 gateway（高文件描述符需求）和 redis（AOF 写入）等关键服务的常见配置。
- 核心基础设施服务（gateway / redis / sentinel / watchdog / docker-proxy）默认获得 OOM 免死保护 (`-999`)。

成本：

- 需了解三层优先级规则才能正确覆盖配置。
- `_build_service_entry()` 方法已处理 15+ 个字段，复杂度需持续关注。

## 落地

- `scripts/iac_core/loader.py` — `_build_service_entry()` 三层字段解析
- `system.yaml` — 可选的服务级 ulimits / oom_score_adj / networks 声明
- `docker-compose.yml` — 编译输出（自动生成，不手工编辑）
