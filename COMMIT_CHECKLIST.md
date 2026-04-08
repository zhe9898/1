# 需要提交的文件清单

## 修改的文件

### 核心模型
- `backend/models/job.py` - 新增边缘算力、调度策略、业务调度字段
- `backend/models/node.py` - 新增 accepted_kinds、边缘算力字段

### 调度器
- `backend/kernel/scheduling/job_scheduler.py` - 强化评分算法、支持调度策略、亲和性
- `backend/core/control_plane.py` - 控制面真源移到后端
- `backend/capabilities.py` - 清理非 kernel 能力

### API
- `backend/api/jobs/routes.py` - 并发限制、重试延迟、attempt 体系
- `backend/api/console.py` - 新增 /surfaces 端点
- `backend/api/settings.py` - 移除 AI/系统端点
- `backend/api/routes.py` - 移除 switches API、清理 SSE 通道
- `backend/api/nodes.py` - 心跳链支持边缘算力字段

## 新增的文件

### 调度算法
- `backend/kernel/scheduling/scheduling_strategies.py` - 5种调度策略实现
- `backend/kernel/scheduling/business_scheduling.py` - 业务调度算法

### 测试
- `tests/test_business_scheduler_hardening.py` - 业务调度门禁测试
- `tests/test_kernel_boundary_hardening.py` - 内核边界门禁测试

### 文档
- `docs/SCHEDULER_AUDIT_REPORT.md` - 调度系统审计报告
- `docs/KERNEL_CLOSURE_CHECKLIST.md` - 内核收口清单
- `docs/KERNEL_HARDENING_SUMMARY.md` - 内核强化总结
- `docs/ADVANCED_SCHEDULING_ALGORITHMS.md` - 高级调度算法文档
- `docs/FINAL_HARDENING_SUMMARY.md` - 最终强化总结

### 数据库迁移
- `migrations/003_advanced_scheduling.sql` - 数据库迁移脚本

## 提交建议

```bash
# 如果是 git 仓库，建议分多个 commit：

# Commit 1: P0/P1 边界清理
git add backend/core/control_plane.py
git add backend/capabilities.py
git add backend/api/console.py
git add backend/api/settings.py
git add backend/api/routes.py
git commit -m "fix(kernel): P0/P1 boundary cleanup

- Control-plane source moved to backend (P0)
- Capabilities only expose kernel surfaces (P1)
- Settings API removed AI/system endpoints (P1)
- Switches API removed (P1)
- SSE only subscribe kernel events (P1)

Co-Authored-By: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>"

# Commit 2: P1 调度治理
git add backend/models/job.py
git add backend/api/jobs/routes.py
git add backend/kernel/scheduling/job_scheduler.py
git commit -m "feat(scheduler): P1 scheduling governance

- Concurrent limits enforcement
- Retry delay mechanism (retry_at field)
- Attempt semantics cleanup (attempt_count)
- accepted_kinds in node contract
- accepted_kinds global count fix

Co-Authored-By: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>"

# Commit 3: 边缘算力编排
git add backend/models/job.py
git add backend/models/node.py
git add backend/kernel/scheduling/job_scheduler.py
git add backend/api/nodes.py
git commit -m "feat(edge): edge computing orchestration

- Data locality (data_locality_key, cached_data_keys)
- Network latency constraints (max_network_latency_ms)
- Power management (power_budget_watts, power_capacity_watts)
- Thermal management (thermal_sensitivity, thermal_state)
- Cloud fallback (cloud_fallback_enabled, cloud_connectivity)
- Node heartbeat chain updated

Co-Authored-By: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>"

# Commit 4: 高级调度算法
git add backend/kernel/scheduling/scheduling_strategies.py
git add backend/kernel/scheduling/business_scheduling.py
git add backend/models/job.py
git add backend/kernel/scheduling/job_scheduler.py
git commit -m "feat(scheduler): advanced scheduling algorithms

- 5 scheduling strategies (spread/binpack/locality/performance/balanced)
- Node affinity (required/preferred)
- Anti-affinity (anti_affinity_key)
- Priority inheritance (parent_job_id)
- Job dependencies (depends_on)
- Gang scheduling (gang_id)
- Batch scheduling (batch_key)
- Job preemption (preemptible)
- Deadline scheduling (deadline_at)
- SLA management (sla_seconds)

Co-Authored-By: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>"

# Commit 5: 测试和文档
git add tests/
git add docs/
git add migrations/
git commit -m "docs(scheduler): tests and documentation

- Business scheduler hardening tests
- Kernel boundary hardening tests
- Scheduler audit report
- Kernel closure checklist
- Advanced scheduling algorithms doc
- Database migration script

Co-Authored-By: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>"
```

## 注意事项

1. **anti-affinity 未完全实现**: 字段和接口已添加，但评分惩罚逻辑标记为 "skip for now"，需要后续补充
2. **数据库迁移**: 需要执行 `migrations/003_advanced_scheduling.sql`
3. **前端更新**: 需要更新前端以使用 `/api/v1/console/surfaces` 端点
4. **API 文档**: 需要更新 Job 创建接口文档，说明新增字段
5. **测试**: 建议补充调度策略、边缘算力、业务调度的集成测试
