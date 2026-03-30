# ADR 0011: 统一 IaC 编译器核心库与模板事实来源

- **状态**: 接受
- **日期**: 2026-03-21

## 1. 背景上下文

项目当前存在**两套独立的 IaC 编译器实现**：

| 编译器 | 路径 | 规模 | 职责 | 产物 |
|--|--|--|--|--|
| 生产编译器 | `scripts/compiler.py` | 631 行 | system.yaml → 完整 IaC 渲染 | docker-compose.yml + .env + Caddyfile + users.acl |
| 部署编译器 | `deploy/config-compiler.py` | 386 行 | 系统配置加载/合并/迁移/渲染 | docker-compose.yml + .env |

两者**不共享任何代码**，各自维护了 YAML 加载、Jinja2 渲染、CLI 入口。长期维护存在以下 P1 架构风险：

1. **行为漂移**：同一 `system.yaml`，两个入口可能生成不同的容器编排产物
2. **校验缺口**：`config-compiler.py` 不调用 `lint.py`、不调用 `secrets_manager.py`，不执行三层 Schema 校验
3. **重复维护**：每次改变 IaC 逻辑需同步两个文件，极易遗漏
4. **模板分裂**：`scripts/templates/`（完整 6 模板）与 `deploy/templates/`（简化 1 模板）各自独立维护

依赖链审计显示：
- `deploy/bootstrap.py` → `deploy/config-compiler.py`（**唯一硬依赖**）
- CI/CD (`.github/workflows/`)、主管线 (`scripts/`)、安装器均**零引用** `deploy/config-compiler.py`

## 2. 决策选项

### 方案 A：模板统一 + 核心库提取（选定）

抽出 `scripts/iac_core/` 独立核心库，两个 CLI 壳共享同一套 load/merge/migrate/lint/secrets/render 逻辑。模板统一到 `scripts/templates/`（唯一事实来源），发布离线包时打包携带模板副本 + manifest 指纹。

### 方案 B：模板分离 + 核心库提取

核心库提取同 A，但两边保留各自的模板目录，各自渲染。消灭逻辑漂移但保留模板漂移。

### 方案 C：废弃 deploy/config-compiler.py

直接删除，`deploy/bootstrap.py` 改为调用 `scripts/compiler.py`。需确认 deploy 离线包不再独立分发。

## 3. 评估对比

### 方案 A（选定）

- **优势**：
  - 强一致——同模板、同逻辑、同 lint、同 secrets
  - 离线可用——打包时 `scripts/templates/` 拷入 `deploy/templates/` + `templates.manifest`
  - 可审计——manifest 含 SHA-256 指纹，运行时校验模板未被篡改
  - 渐进式——分 4 Phase 实施，每 Phase 可独立回滚

- **劣势**：
  - 工作量最大（~40-60 工具调用）
  - `release.sh` 需增加模板拷贝 + manifest 生成步骤

### 方案 B

- **优势**：工作量较 A 少（不需模板统一）
- **劣势**：模板漂移风险依然存在，deploy 渲染的 compose 与生产版可能不一致

### 方案 C

- **优势**：最简单，彻底消灭双编译器
- **劣势**：deploy 离线包失去自包含性；如果 deploy/bootstrap.py 仍在某些客户环境使用，直接删除是发布面风险

## 4. 最终决定

**选择方案 A**。理由：

1. 方案 B 的"弱一致"会长期累积不可见的模板漂移
2. 方案 C 的风险需要完整确认 deploy 通道的使用情况，当前无法 100% 排除
3. 方案 A 通过**三重保障**同时获得强一致 + 离线可用 + 可审计：
   - 打包时拷贝模板到 deploy 包 → 离线自包含
   - deploy 壳启动时 fail-fast 校验模板存在性 → 不生成半成品
   - `templates.manifest` SHA-256 校验 → 防篡改/版本不一致

### 核心库结构

```
scripts/iac_core/
  __init__.py
  loader.py            # load_yaml() + deep_merge() + conf.d 碎片合并
  migrator.py          # 链式版本迁移 (v1→v2→...)
  lint.py              # 三层校验 (TIER_FAIL / TIER_SECURITY / TIER_WARN)
  secrets.py           # 密钥生成/解析 (现 secrets_manager.py)
  renderer.py          # Jinja2 通用渲染器
  models.py            # TypedDict: ServiceDef, NetworkDef 等
```

### Lint 三层校验规则

**Tier 1 — 必填硬门槛（缺失 = exit(1)）**：

- `version` ≥ 2（int 类型）
- `services.<name>.image` 或 `services.<name>.build`（二选一必存在）
- `services.<name>.container_name`
- `services.<name>.networks`
- `services.<name>.restart`（核心服务：gateway/redis/postgres/sentinel）
- `services.gateway`、`services.redis`、`services.postgres` 必须存在
- `network.domain`
- `network.planes.backend_net.internal` 结构

**Tier 2 — 安全/可靠性（不合规 = exit(1)）**：

- gateway/redis `ulimits.nofile.soft/hard` ≥ 65536
- gateway/redis/sentinel/watchdog/docker-proxy `oom_score_adj` == -999
- postgres/redis/docker-proxy 不得接入 `frontend_net`
- 有状态服务（postgres/redis）`volumes` 非空
- `read_only: true` 的服务必须配套 `tmpfs`

**Tier 3 — 建议项（先 warn，后续可升级）**：

- healthcheck 完整度（interval/timeout/retries/start_period）
- `stop_grace_period`
- `deploy.resources.limits`（CPU/MEM）
- `sentinel.switch_container_map`/`watch_targets`/`mount_container_map`
- GPU 声明但未配 `MULTIMODAL_TIMEOUT_SECONDS`
- `services.*.logging` 显式声明
- 备份条件校验：`services.restic` 存在时强制要求 `backup.s3_endpoint/s3_bucket/retention_days`

### 模板统一策略

```
scripts/templates/                ← 唯一事实来源（git 管理）
  docker-compose.yml.j2
  .env.j2
  Caddyfile.j2
  ...

deploy/templates/                 ← .gitignore，由 release.sh 打包时生成
  (scripts/templates 副本)
  templates.manifest              ← SHA-256 指纹文件
```

### docker-compose 预检修复

```diff
- cmd = ["docker-compose", ...]
- if "No such file or directory" in (res.stderr or ""):
-     cmd = ["docker", "compose", ...]
+ if shutil.which("docker-compose"):
+     compose_cmd = ["docker-compose"]
+ elif shutil.which("docker"):
+     compose_cmd = ["docker", "compose"]
+ else:
+     compose_cmd = None  # graceful skip
```

### Git 操作安全化

```diff
- git reset --hard origin/main
+ git checkout -f origin/main     # 保护 untracked 文件
```

## 5. 影响范围

| 影响面 | 详情 | 风险等级 |
|--|--|--|
| `scripts/compiler.py` | 内部重构为调用 `iac_core`，外部行为不变 | 低 |
| `deploy/config-compiler.py` | 改为调用 `iac_core` + 模板校验，外部行为更安全 | 低 |
| `deploy/bootstrap.py` | `git reset --hard` → `git checkout -f` | 低 |
| `release.sh` | 新增模板拷贝 + manifest 生成步骤 | 低 |
| `scripts/compiler/lint.py` | 重构为三层规则函数架构 | 中 |
| `deploy/templates/` | 加入 `.gitignore`，由打包流程生成 | 低 |
| CI/CD | 无影响（不引用 deploy 通道） | 无 |
| 测试 | 需全量回归 624 tests + IaC 渲染验证 | — |

### 实施分期与回滚策略

| Phase | 内容 | 可独立回滚 | 影响范围 |
|--|--|--|--|
| 1 | 提取 `iac_core`（loader + migrator + lint + secrets） | `git rm -rf scripts/iac_core` | 无破坏（新目录） |
| 2 | `scripts/compiler.py` 改为调用 `iac_core` | `git checkout scripts/compiler.py` | 仅 scripts 通道 |
| 3 | `deploy/config-compiler.py` 改为调用 `iac_core` + 模板校验 | `git checkout deploy/config-compiler.py` | 仅 deploy 通道 |
| 4 | lint 三层扩展 + shutil.which + git checkout | 各文件独立 checkout | 全通道 |

> 注：Phase 1-3 的 lint 三层扩展和 shutil.which/git checkout 修复（Phase 4）可以独立实施，不依赖 iac_core 提取。建议**优先实施 Phase 4**（即时收益），iac_core 提取可在后续迭代中完成。
