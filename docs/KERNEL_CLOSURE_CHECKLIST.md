# ZEN70 Gateway Kernel 收口清单

**审计日期**: 2026-03-29
**当前状态**: 成型但未完全收口的 Gateway Kernel 工程仓
**核心判断**: 内核控制面骨架成立，但边界和治理还没完全收口

---

## 核心优点 ✅

### 1. 产品身份清晰
- ✅ 默认产品身份已收成 `ZEN70 Gateway Kernel`
- ✅ 默认 profile: `gateway-kernel`
- ✅ 默认 packs: `[]`
- ✅ 业务 pack 不得回流默认 kernel（文档已明确）

### 2. 控制面合同清楚
- ✅ 公开 capability: `gateway.dashboard`, `gateway.nodes`, `gateway.jobs`, `gateway.connectors`
- ✅ 管理员 capability: `gateway.settings`
- ✅ 默认 API: `/api/v1/profile`, `/api/v1/capabilities`, `/api/v1/console/*`, `/api/v1/nodes`, `/api/v1/jobs`, `/api/v1/connectors`, `/api/v1/settings`, `/api/v1/events`

### 3. 节点合同强类型
- ✅ 节点合同: `executor`, `os`, `arch`, `zone`, `protocol_version`, `lease_version`, `agent_version`, `max_concurrency`, `cpu_cores`, `memory_mb`, `gpu_vram_mb`, `storage_mb`
- ✅ 节点舰队治理: `drain_status`, `health_reason`, `active_lease_count`
- ✅ 机器通道鉴权: `Authorization: Bearer <node_token>`
- ✅ 节点凭证由控制面签发: `auth_token_hash`, `auth_token_version`, `enrollment_status`

### 4. 任务合同强类型
- ✅ 任务租约合同: `idempotency_key`, `attempt`, `lease_token`
- ✅ 任务派发选择器: `priority`, `target_os`, `target_arch`, `target_executor`, `required_capabilities`, `target_zone`, `required_cpu_cores`, `required_memory_mb`, `required_gpu_vram_mb`, `required_storage_mb`, `timeout_seconds`, `max_retries`, `estimated_duration_s`, `source`
- ✅ Lease owner 校验: `_assert_valid_lease_owner()`
- ✅ Row-level lock: `with_for_update()` 在 complete/fail/renew 中
- ✅ 调度匹配: 按 executor/os/arch/zone/capability/资源做匹配

### 5. 主调度链能跑
- ✅ 节点注册 → 心跳 → 拉活 → 执行 → 回调 闭环完整
- ✅ 失败重试预算由控制面强制执行
- ✅ 每次 lease 可通过 `/api/v1/jobs/{id}/attempts` 审计
- ✅ 运维人员可通过控制面治理: `nodes/{id}/drain`, `jobs/{id}/cancel`, `jobs/{id}/retry`, `jobs/{id}/explain`

---

## 核心问题（需收口）

## P0 - 架构反向依赖 🔴

### 1. 控制面真源方向反了
**问题**: 后端 `backend/core/control_plane.py` 读取前端文件 `frontend/src/config/controlPlaneSurfaces.json` 来定义 control-plane surfaces。

**影响**:
- 后端控制面真源依赖前端文件，这是反的
- 前端文件变更可能破坏后端 API 合同
- 部署时前端文件缺失会导致后端控制面失效

**修复方案**:
1. 将 `controlPlaneSurfaces.json` 移到 `backend/config/` 或直接硬编码到 `backend/core/control_plane.py`
2. 前端从后端 `/api/v1/console/surfaces` 读取控制面定义
3. 确保后端是控制面合同的唯一真源

**优先级**: P0 - 架构级问题，必须修复

---

## P1 - 边界未切干净 🟠

### 2. Settings 面混入非 kernel 内容
**问题**: `/api/v1/settings` 还混着 AI provider、模型扫描、GPU/磁盘系统信息

**影响**:
- 默认 kernel 边界不干净
- Settings 面超出纯 kernel 控制面边界
- 业务/运维配置回流到默认 kernel

**修复方案**:
1. 审计 `backend/control_plane/adapters/settings/` 所有路由
2. 移除 AI provider、模型扫描、GPU/磁盘系统信息相关 API
3. Settings 只保留 kernel 运行时配置：节点舰队、任务队列、连接器注册、系统基线

**优先级**: P1 - 边界问题，影响产品定位

---

### 3. Switches 旧控制链残留
**问题**:
- `/api/v1/switches` 还在默认 API 里
- SSE 还订阅 `hardware`、`switch` 通道
- 旧业务/设备控制残留还没切完

**影响**:
- 默认 kernel 混入设备控制逻辑
- SSE 通道暴露非 kernel 事件
- 旧控制链和新控制面双轨并存

**修复方案**:
1. 移除 `/api/v1/switches` 路由（或移到 IoT Pack）
2. SSE 只保留 kernel 事件通道：`nodes`, `jobs`, `connectors`, `control-plane`
3. 移除 `hardware`, `switch` 通道订阅

**优先级**: P1 - 边界问题，影响产品定位

---

### 4. Capabilities 代码面双轨
**问题**:
- `/api/v1/capabilities` 对外公开面现在相对干净（已修复）
- 但 `backend/capabilities.py` 那套旧动态矩阵代码还在
- 里面仍混着 Agent/记忆/语音/能耗等项的代码残留（虽然已注释/移除）

**影响**:
- 代码面是双轨的
- 旧代码残留可能被误用
- 维护成本高

**修复方案**:
1. ✅ 已清理 `build_matrix()` 中的非 kernel 能力
2. 进一步清理 `fetch_topology()`, `_read_feature_flags()` 等旧代码
3. 或者保留这些函数但明确标注为 "Reserved for pack-specific capabilities"

**优先级**: P1 - 代码清理，降低维护成本

---

## P1 - 权限边界不够硬 🟠

### 5. SSE 事件流没强制鉴权
**问题**: `/api/v1/events` 没强制登录用户鉴权，但会挂上 node/job/connector/hardware/switch 事件流

**影响**:
- SSE 面不够硬
- 未登录用户可能订阅敏感事件
- 安全风险

**修复方案**:
1. `/api/v1/events` 强制要求 `Depends(get_current_user)`
2. 根据用户角色过滤事件流（普通用户只能看自己 tenant 的事件）
3. Admin 事件（如 node drain/undrain）只对 admin 可见

**优先级**: P1 - 安全问题

---

### 6. Toggle Switch 权限过松
**问题**: `toggle_switch()` 只要求普通登录用户，不是 admin，就能改软开关并触发物理控制链

**影响**:
- 普通用户可以触发物理控制
- 权限边界不清晰
- 安全风险

**修复方案**:
1. `toggle_switch()` 改为 `Depends(get_current_admin)`
2. 或者移除 `toggle_switch()` API（如果属于 IoT Pack）

**优先级**: P1 - 安全问题

---

### 7. Connectors 权限过松
**问题**: Connectors 的 upsert/invoke/test 只要求普通登录用户，不是 admin

**影响**:
- 普通用户可以注册/调用/测试连接器
- 资源边界不清晰
- 可能被滥用

**修复方案**:
1. Connector 注册/更新/删除改为 `Depends(get_current_admin)`
2. Connector 调用保持普通用户权限（业务需要）
3. Connector 测试改为 `Depends(get_current_admin)`

**优先级**: P1 - 安全问题

---

## P1 - 调度治理未收死 🟠

### 8. 并发治理未落实（已修复 ✅）
**问题**: scheduled/background 分类有定义，但并发治理没真正落到调度入口

**修复**:
- ✅ 已实现 `_check_concurrent_limits()` 检查 global/per_tenant/per_connector 并发限制
- ✅ `create_job()` 在创建任务前检查并发限制

**优先级**: P1 - 已修复

---

### 9. 重试无冷却/退避（已修复 ✅）
**问题**: 自动重试是直接回 pending，没有按 job type 使用 retry delay/backoff

**修复**:
- ✅ 已实现 `retry_at` 字段
- ✅ `fail_job()` 根据 job_type 设置 retry_at（scheduled 5分钟, background 60秒）
- ✅ `pull_jobs()` 过滤 `retry_at > now` 的任务

**优先级**: P1 - 已修复

---

### 10. Aging 被候选窗口削弱
**问题**: 候选集先裁窗口（limit * 40，上限 200），低优老任务的防饿死能力会被削弱

**影响**:
- 如果高优 backlog 长期很大，低优先级旧任务根本进不了前 200 个候选
- Aging 只能救进了候选窗口的任务
- 低优长尾业务任务可能被长期挤压

**修复方案**:
1. 在 DB 查询中应用 aging（使用 PostgreSQL 计算 effective_priority）
2. 或者增大候选窗口上限（如 500）
3. 或者为低优任务设置单独的"饥饿救援"通道

**优先级**: P2 - 调度优化，不影响功能正确性

---

### 11. accepted_kinds 不是正式节点合同（已修复 ✅）
**问题**: accepted_kinds 只是 pull 时瞬时上报，不是正式节点合同字段

**修复**:
- ✅ 已修复 `count_eligible_nodes_for_job()` 接受 `accepted_kinds` 参数
- ✅ `select_jobs_for_node()` 传递 `accepted_kinds` 给 `count_eligible_nodes_for_job()`
- ✅ `eligible_nodes_count` 和 `scarcity_score` 现在准确

**优先级**: P1 - 已修复

---

### 12. Attempt 体系语义不统一（已修复 ✅）
**问题**: attempt / attempt_count / retry_count / DLQ requeue / manual retry 语义没完全统一

**修复**:
- ✅ `pull_jobs()` 中每次 lease 时增加 `attempt_count`
- ✅ `retry_job_now()` 重置 `attempt_count = 0`
- ✅ 现在 `attempt_count` 是真实调度尝试总次数

**优先级**: P1 - 已修复

---

## P2 - Connector 面不够硬 🟡

### 13. test_connector() 是假健康检查
**问题**: `test_connector()` 本质上只是本地状态和 endpoint 格式检查，不是真正远端连通性测试

**影响**:
- 属于"假健康"
- 用户以为测试通过就能用，但实际可能连不上
- 误导性强

**修复方案**:
1. `test_connector()` 改为真正的远端连通性测试
2. 根据 connector type 执行不同的测试逻辑：
   - HTTP: 发送 GET/POST 请求
   - Database: 执行 SELECT 1
   - MQTT: 连接并订阅测试主题
3. 测试超时设置为 10 秒
4. 返回详细的测试结果（延迟、错误信息）

**优先级**: P2 - 体验优化

---

### 14. invoke_connector() 选择器太粗
**问题**: `invoke_connector()` 生成 job 时选择器太粗，只塞了 `kind=connector.invoke` 和 `required_capabilities=["connector.invoke"]`

**影响**:
- 没有更细的 executor/zone/kind-specific 调度约束
- 可能派到不合适的节点
- 调度效率低

**修复方案**:
1. Connector 注册时记录 `preferred_executor`, `preferred_zone`
2. `invoke_connector()` 生成 job 时使用这些选择器
3. 根据 connector type 设置不同的 `required_capabilities`：
   - HTTP connector: `["connector.invoke", "http.client"]`
   - Database connector: `["connector.invoke", "db.client"]`
   - MQTT connector: `["connector.invoke", "mqtt.client"]`

**优先级**: P2 - 调度优化

---

## 收口优先级总结

### 立即修复（P0）
1. **控制面真源方向反了** - 架构级问题

### 高优修复（P1）
2. Settings 面混入非 kernel 内容
3. Switches 旧控制链残留
4. Capabilities 代码面双轨
5. SSE 事件流没强制鉴权
6. Toggle Switch 权限过松
7. Connectors 权限过松
8. ✅ 并发治理未落实（已修复）
9. ✅ 重试无冷却/退避（已修复）
11. ✅ accepted_kinds 不是正式节点合同（已修复）
12. ✅ Attempt 体系语义不统一（已修复）

### 中优优化（P2）
10. Aging 被候选窗口削弱
13. test_connector() 是假健康检查
14. invoke_connector() 选择器太粗

---

## 门禁强化建议

### 1. 添加边界检查测试
```python
def test_default_kernel_api_surface():
    """Verify default kernel only exposes control-plane APIs."""
    allowed_prefixes = [
        "/api/v1/profile",
        "/api/v1/capabilities",
        "/api/v1/console",
        "/api/v1/nodes",
        "/api/v1/jobs",
        "/api/v1/connectors",
        "/api/v1/settings",
        "/api/v1/events",
    ]

    forbidden_prefixes = [
        "/api/v1/switches",  # IoT Pack
        "/api/v1/scenes",    # IoT Pack
        "/api/v1/scheduler", # IoT Pack
        "/api/v1/metrics",   # Ops Pack
        "/api/v1/health",    # Health Pack (除了 /health 探针)
        "/api/v1/vector",    # Vector Pack
    ]
```

### 2. 添加权限边界测试
```python
def test_admin_only_endpoints_require_admin():
    """Verify admin-only endpoints reject non-admin users."""
    admin_only_endpoints = [
        "POST /api/v1/nodes/{id}/drain",
        "POST /api/v1/nodes/{id}/undrain",
        "POST /api/v1/jobs/{id}/cancel",
        "POST /api/v1/connectors",  # Create
        "PUT /api/v1/connectors/{id}",  # Update
        "DELETE /api/v1/connectors/{id}",  # Delete
        "POST /api/v1/connectors/{id}/test",  # Test
    ]
```

### 3. 添加控制面真源测试
```python
def test_control_plane_surfaces_defined_in_backend():
    """Verify control-plane surfaces are defined in backend, not frontend."""
    # Should NOT read from frontend/src/config/controlPlaneSurfaces.json
    # Should read from backend/config/ or hardcoded in backend/core/control_plane.py
```

---

## 总结

**当前状态**: 成型但未完全收口的 Gateway Kernel 工程仓

**核心优点**:
- 产品身份清晰
- 控制面合同清楚
- 节点/任务合同强类型
- 主调度链能跑

**核心问题**:
- P0: 控制面真源方向反了（架构级）
- P1: 边界未切干净（Settings/Switches/Capabilities）
- P1: 权限边界不够硬（SSE/Toggle/Connectors）
- P1: 调度治理未收死（部分已修复）
- P2: Connector 面不够硬（假健康检查、选择器太粗）

**下一步**:
1. 立即修复 P0 - 控制面真源方向
2. 逐步修复 P1 - 边界和权限问题
3. 优化 P2 - Connector 和调度细节
4. 补充门禁测试，防止回流
