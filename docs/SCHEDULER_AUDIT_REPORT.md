# ZEN70 调度系统审计报告

**审计日期**: 2026-03-29（最后更新: 2026-03-30）
**审计范围**: 内核调度 + 业务调度完整性
**审计结论**: P0 安全无问题；P1 前三项（并发治理 / 重试退避 / kind 接受面）已全部修复；剩余 P1、P2 项为语义增强

---

## P0 致命断链检查 ✅

**结论**: 未发现会直接把调度系统打穿的致命断链。

已验证以下关键安全机制：

1. **任意节点伪造结果回调** ✅
   - `complete_job()` 和 `fail_job()` 都调用 `_assert_valid_lease_owner()` 验证 lease owner
   - 位置: `backend/api/jobs/routes.py:329, 386`

2. **不校验 lease owner 就收终态** ✅
   - 所有终态回调（complete/fail/progress/renew）都有 lease owner 检查
   - 位置: `backend/api/jobs/routes.py:329, 386, 490, 538`

3. **心跳旁路直接激活 pending 节点** ✅
   - `pull_jobs()` 调用 `authenticate_node_request(..., require_active=True)` 验证节点
   - 位置: `backend/api/jobs/routes.py:209-214`

4. **pull_jobs 完全不看 selector / capability / 资源** ✅
   - `select_jobs_for_node()` 调用 `job_matches_node()` 检查所有约束
   - 位置: `backend/core/job_scheduler.py:146-153, 210`

---

## P1 业务调度问题（实打实影响调度效果）

### 1. 分层业务调度只落默认值，没落并发治理 ✅ 已修复

**修复内容**:
- `create_job()` 现在调用 `_check_concurrent_limits(db, tenant_id, job_type, connector_id)` 完整检查三级并发限制
- 全局（global）、租户（per_tenant）、连接器（per_connector）级别都有 429 限流
- 限制值由 `get_max_concurrent_limit()` 从 `job_type_separation.py` 配置读取

**位置**:
- 检查函数: `backend/api/jobs/routes.py:_check_concurrent_limits()`
- 调用点: `backend/api/jobs/routes.py:create_job()` — flush 前
- 配置源: `backend/core/job_type_separation.py`

---

### 2. 重试无冷却/退避 ✅ 已修复

**修复内容**:
- `fail_job()` 现在调用 `calculate_retry_delay_seconds(failure_category, retry_count, base_delay, max_delay)` 计算指数退避延迟
- 重试任务设置 `job.retry_at = now + timedelta(seconds=retry_delay_seconds)`，`pull_jobs()` 查询条件包含 `retry_at <= now`
- 退避参数通过环境变量 `RETRY_BASE_DELAY_SECONDS`（默认10）/ `RETRY_MAX_DELAY_SECONDS`（默认600）外部化

**位置**:
- 退避计算: `backend/core/failure_taxonomy.py:calculate_retry_delay_seconds()`
- 执行层: `backend/api/jobs/routes.py:fail_job()` — 设置 `retry_at`
- 查询层: `backend/api/jobs/routes.py:pull_jobs()` — `retry_at <= now` 条件

---

### 3. kind 接受面不是正式节点合同 ✅ 已修复

**修复内容**:
- `count_eligible_nodes_for_job()` 现在接受 `accepted_kinds` 参数
- 每个节点的 `node.accepted_kinds`（合同级别）优先;无合同则使用拉活时的 `accepted_kinds`
- 这使 `eligible_nodes_count` 和 `scarcity_score` 更真实

**位置**:
- `backend/core/job_scheduler.py:count_eligible_nodes_for_job()` — 参数 + 优先逻辑
- `backend/core/job_scheduler.py:select_jobs_for_node()` — 传入 `accepted_kinds`

---

### 4. create_job() 广义异常会污染幂等冲突语义 ⚠️

**问题描述**:
- `create_job()` 在 `db.flush()` 失败后，会 rollback 并重新查询 idempotency_key
- 但如果失败原因不是幂等冲突（如网络抖动、DB 超时），这个逻辑会误判

**影响**:
- 非幂等冲突的异常可能被误报为 "ZEN-JOB-4090: Idempotency key already belongs to a different job definition"
- 用户会收到错误的恢复提示

**位置**:
- `backend/api/jobs/routes.py:162-178`

---

### 5. DLQ 回放不重置 attempt 体系 ⚠️

**问题描述**:
- DLQ (Dead-Letter Queue) 回放时，没有重置 `attempt_count` 和 `attempt` 字段
- 导致回放任务可能因为旧的 attempt 计数而无法正常重试

**影响**:
- DLQ 回放的任务可能立即失败，因为 `attempt_count >= max_retries + 1`
- 人工治理语义和系统预算语义不一致

**位置**:
- DLQ 回放逻辑需要补充 attempt 体系重置

---

## P2 调度语义污染

### 1. aging 被候选窗口削弱 ⚠️

**问题描述**:
- `pull_jobs()` 先从 DB 按 `priority desc, created_at asc` 抓候选窗口（limit * 40，上限 200）
- 然后才把这批候选送进 `sort_jobs_by_stratified_priority(..., aging_enabled=True)` 做 aging 排序
- aging 只能救进了候选窗口的任务

**影响**:
- 如果高优 backlog 长期很大，低优先级旧任务根本进不了前 200 个候选
- 它再老也没用，aging 无法救它们
- 低优长尾业务任务可能被长期挤压
- "有 aging"不等于"真防饿死"

**位置**:
- `backend/api/jobs/routes.py:235-258`

---

### 2. 过期 attempt 回收不主动 ⚠️

**问题描述**:
- `_expire_previous_attempt_if_needed()` 只会在任务下次再次被 lease 前，才把旧 attempt 标记成 expired
- 如果任务 lease 过期后很久都没人再拉到它，旧 attempt 可能一直停在 leased/running 语义上

**影响**:
- attempt 审计不够干净
- 节点可靠性统计会滞后（`_load_node_metrics()` 基于 JobAttempt.status 做过去 24 小时成功率计算）
- 控制台 explain / diagnose 语义会脏

**位置**:
- `backend/api/jobs/routes.py:280` (只在下次 lease 前才标记)

---

### 3. explain 与真实调度上下文不完全一致 ⚠️

**问题描述**:
- `explain_job()` 会对所有节点做 blockers 和 score 解释，看起来很完整
- 但真实 `pull_jobs()` 有候选集窗口、`accepted_kinds` 瞬时过滤、`recent_failed_job_ids`、当前请求节点 slots 等多层上下文

**影响**:
- explain 现在更像："静态为什么这节点理论可/不可"
- 不是："这一轮真实 pull 为什么这个业务任务会不会被派到它"
- 这不是 bug，但属于业务调度解释能力不完全真实

**位置**:
- `backend/api/jobs/routes.py:674-749`

---

### 4. attempt / attempt_count / retry_count 模型语义不够干净 ⚠️

**问题描述**:
- `attempt`: 每次 lease 时 +1，表示"第几次尝试"
- `attempt_count`: 只在 `fail_job()` 的"会重试"分支里才 +1，表示"自动重试次数近似计数"
- `retry_count`: 每次重试时 +1，但 `retry_job_now()` 手工重试时会重置为 0

**影响**:
- `attempt_count` 不是"真实调度尝试总次数"
- `retry_job_now()` 手工重试后，`attempt_count` 没重置，但 `retry_count` 重置了
- `should_retry_job()` 检查 `attempt_count >= max_retries + 1` 时会沿用旧计数
- 人工治理语义和系统预算语义不一致

**位置**:
- `backend/api/jobs/routes.py:283` (attempt +1)
- `backend/api/jobs/routes.py:421` (attempt_count +1)
- `backend/api/jobs/routes.py:654` (retry_count 重置，但 attempt_count 没重置)
- `backend/core/failure_taxonomy.py:199-200` (should_retry_job 检查 attempt_count)

---

### 5. DLQ 更像索引，不是独立失败状态机 ⚠️

**问题描述**:
- DLQ (Dead-Letter Queue) 目前更像是一个失败任务的索引
- 不是一个独立的失败状态机，没有独立的状态流转和治理策略

**影响**:
- DLQ 回放、清理、监控等治理能力不完整
- 失败任务的生命周期管理不够清晰

---

## 总结

### 内核调度 ✅
- P0 安全机制完整
- 节点认证、lease owner 验证、资源约束检查都在
- 主干调度链路可用

### 业务调度 ❌
- 策略定义已存在，但执行层未闭环
- 这是系统性没收口，不是单个点修一下就完
- 需要补充：
  1. 并发限制执行层
  2. 重试延迟机制
  3. accepted_kinds 正式化
  4. attempt 体系语义清理
  5. DLQ 状态机完善

---

## 建议优先级

### 高优（影响业务调度效果）
1. 补充并发限制检查（P1.1）
2. 实现重试延迟机制（P1.2）
3. 修复 accepted_kinds 全局计数（P1.3）

### 中优（语义清理）
4. 重置 manual retry 的 attempt_count（P1.5）
5. 清理 attempt 体系语义（P2.4）
6. 主动回收过期 attempt（P2.2）

### 低优（体验优化）
7. 优化 aging 候选窗口（P2.1）
8. 完善 explain 上下文（P2.3）
9. 完善 DLQ 状态机（P2.5）
