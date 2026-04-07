# ZEN70 Gateway Kernel - Critical Fixes

## 执行标准

- **高可用**：零单点故障，所有操作幂等
- **严禁弱代码**：无拼接、无过时库、无逻辑错误
- **强代码标准**：类型安全、事务完整、错误处理完备
- **准售架构**：生产级质量，可直接交付
- **IaC 唯一事实源**：所有配置收束于 system.yaml
- **全链路打通**：前后端契约严格，无逻辑错误

---

## P0-1: 竞态条件 - 添加行级锁

### 问题
`fail_job`、`complete_job`、`renew_job_lease` 没有使用行级锁，存在竞态条件。

### 修复方案

#### 1. 创建带锁的查询函数

```python
async def _get_job_by_id_for_update(
    db: AsyncSession,
    tenant_id: str,
    job_id: str,
    *,
    skip_locked: bool = False,
) -> Job:
    """Get job by ID with row-level lock for safe updates.

    Args:
        db: Database session
        tenant_id: Tenant ID
        job_id: Job ID
        skip_locked: If True, skip locked rows (for pull_jobs)

    Returns:
        Job instance with exclusive lock

    Raises:
        zen("ZEN-JOB-4040"): Job not found
    """
    stmt = (
        select(Job)
        .where(Job.tenant_id == tenant_id, Job.job_id == job_id)
        .with_for_update(skip_locked=skip_locked)
    )
    result = await db.execute(stmt)
    job = result.scalars().first()
    if job is None:
        raise zen("ZEN-JOB-4040", "job not found", status_code=404)
    return job
```

#### 2. 修复 fail_job

```python
@router.post("/{id}/fail", response_model=JobResponse)
async def fail_job(
    id: str,
    payload: JobFailRequest,
    db: AsyncSession = Depends(get_machine_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
    node_token: str = Depends(get_node_machine_token),
) -> JobResponse:
    """Mark job as failed with row-level lock to prevent race conditions."""
    await authenticate_node_request(
        db, payload.node_id, node_token,
        require_active=True, tenant_id=payload.tenant_id
    )

    # CRITICAL: Use row-level lock to prevent race with lease expiration
    job = await _get_job_by_id_for_update(db, payload.tenant_id, id)

    _assert_valid_lease_owner(job, payload, "fail")
    if job.status == "failed":
        return _to_response(job)

    attempt = await _get_attempt_for_callback(db, job, payload)
    if attempt is None:
        raise zen(
            "ZEN-JOB-4093",
            "Job attempt history is missing for this lease",
            status_code=409,
            recovery_hint="Pull a fresh job lease before reporting terminal state",
            details={"job_id": job.job_id, "node_id": payload.node_id, "attempt": payload.attempt},
        )

    now = _utcnow()

    # Infer failure category if not provided
    failure_category = payload.failure_category or _infer_failure_category(
        error_message=payload.error,
        exit_code=payload.error_details.get("exit_code") if payload.error_details else None,
        error_details=payload.error_details,
    )

    attempt.status = "failed"
    attempt.error_message = payload.error
    attempt.failure_category = failure_category
    attempt.completed_at = now
    attempt.updated_at = now

    # Decide if should retry based on failure category
    should_retry = _should_retry_job(job, failure_category)

    if should_retry:
        job.retry_count = int(job.retry_count or 0) + 1
        job.attempt_count = int(job.attempt_count or 0) + 1
        job.status = "pending"
        job.node_id = None
        job.lease_token = None
        job.error_message = payload.error
        job.failure_category = failure_category
        job.completed_at = None
        job.started_at = None
        job.leased_until = None
        job.updated_at = now
        await db.flush()

        await _append_log(
            db,
            job.job_id,
            payload.log or f"job failed on {payload.node_id}; requeued retry={job.retry_count}/{job.max_retries} category={failure_category}",
            level="warning",
            tenant_id=job.tenant_id,
        )

        response = _to_response(job, now=now)
        await publish_control_event(
            redis,
            "job:events",
            {
                "event": "requeued",
                "job_id": job.job_id,
                "tenant_id": job.tenant_id,
                "retry_count": job.retry_count,
                "max_retries": job.max_retries,
                "failure_category": failure_category,
            },
        )
        await db.commit()

        # Check if node has too many failures
        await _check_node_failure_pattern(db, payload.node_id, payload.tenant_id)

        return response
    else:
        job.status = "failed"
        job.error_message = payload.error
        job.failure_category = failure_category
        job.completed_at = now
        job.updated_at = now
        await db.flush()

        await _append_log(
            db,
            job.job_id,
            payload.log or f"job failed permanently on {payload.node_id} category={failure_category}",
            level="error",
            tenant_id=job.tenant_id,
        )

        response = _to_response(job, now=now)
        await publish_control_event(
            redis,
            "job:events",
            {
                "event": "failed",
                "job_id": job.job_id,
                "tenant_id": job.tenant_id,
                "failure_category": failure_category,
                "will_retry": False,
            },
        )
        await db.commit()

        # Move to dead-letter queue if exceeded retries
        if job.retry_count >= job.max_retries:
            await _move_to_dead_letter_queue(redis, job)

        return response
```

#### 3. 修复 complete_job

```python
@router.post("/{id}/result", response_model=JobResponse)
async def complete_job(
    id: str,
    payload: JobResultRequest,
    db: AsyncSession = Depends(get_machine_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
    node_token: str = Depends(get_node_machine_token),
) -> JobResponse:
    """Mark job as completed with row-level lock to prevent race conditions."""
    await authenticate_node_request(
        db, payload.node_id, node_token,
        require_active=True, tenant_id=payload.tenant_id
    )

    # CRITICAL: Use row-level lock to prevent race with lease expiration
    job = await _get_job_by_id_for_update(db, payload.tenant_id, id)

    _assert_valid_lease_owner(job, payload, "complete")
    if job.status == "completed":
        return _to_response(job)

    attempt = await _get_attempt_for_callback(db, job, payload)
    if attempt is None:
        raise zen(
            "ZEN-JOB-4093",
            "Job attempt history is missing for this lease",
            status_code=409,
            recovery_hint="Pull a fresh job lease before reporting terminal state",
            details={"job_id": job.job_id, "node_id": payload.node_id, "attempt": payload.attempt},
        )

    now = _utcnow()
    attempt.status = "completed"
    attempt.result = payload.result
    attempt.completed_at = now
    attempt.updated_at = now

    job.status = "completed"
    job.result = payload.result
    job.completed_at = now
    job.updated_at = now
    await db.flush()

    await _append_log(
        db,
        job.job_id,
        payload.log or f"job completed on {payload.node_id}",
        level="info",
        tenant_id=job.tenant_id,
    )

    response = _to_response(job, now=now)
    await publish_control_event(
        redis,
        "job:events",
        {
            "event": "completed",
            "job_id": job.job_id,
            "tenant_id": job.tenant_id,
            "node_id": payload.node_id,
        },
    )
    await db.commit()
    return response
```

#### 4. 修复 renew_job_lease

```python
@router.post("/{id}/renew", response_model=JobResponse)
async def renew_job_lease(
    id: str,
    payload: JobRenewRequest,
    db: AsyncSession = Depends(get_machine_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
    node_token: str = Depends(get_node_machine_token),
) -> JobResponse:
    """Renew job lease with row-level lock to prevent race conditions."""
    await authenticate_node_request(
        db, payload.node_id, node_token,
        require_active=True, tenant_id=payload.tenant_id
    )

    # CRITICAL: Use row-level lock to prevent race with lease expiration
    job = await _get_job_by_id_for_update(db, payload.tenant_id, id)

    _assert_valid_lease_owner(job, payload, "renew")
    if job.status != "leased":
        raise zen(
            "ZEN-JOB-4091",
            "Job is not in leased state",
            status_code=409,
            details={"job_id": job.job_id, "status": job.status},
        )

    now = _utcnow()
    lease_seconds = int(job.lease_seconds or 30)
    job.leased_until = now + timedelta(seconds=lease_seconds)
    job.updated_at = now
    await db.flush()

    response = _to_response(job, now=now)
    await publish_control_event(
        redis,
        "job:events",
        {
            "event": "renewed",
            "job_id": job.job_id,
            "tenant_id": job.tenant_id,
            "node_id": payload.node_id,
            "leased_until": job.leased_until.isoformat(),
        },
    )
    await db.commit()
    return response
```

---

## P0-2: Goroutine 泄漏 - 添加退避和超时

### 问题
`runner-agent/internal/jobs/poller.go` 的租约续期 goroutine 无退避策略，导致内存泄漏。

### 修复方案

```go
// runner-agent/internal/jobs/poller.go

func (p *Poller) processJobs(ctx context.Context, jobs []api.Job) {
    for _, job := range jobs {
        jobCtx, cancel := context.WithCancel(ctx)
        renewDone := make(chan struct{})

        // Start lease renewal goroutine with retry limit
        go func(currentJob api.Job) {
            defer close(renewDone)

            renewEvery := time.Duration(max(5, currentJob.LeaseSeconds/2)) * time.Second
            ticker := time.NewTicker(renewEvery)
            defer ticker.Stop()

            consecutiveFailures := 0
            const maxConsecutiveFailures = 3

            for {
                select {
                case <-jobCtx.Done():
                    return
                case <-ticker.C:
                    // Add timeout for renewal request
                    renewCtx, renewCancel := context.WithTimeout(jobCtx, 10*time.Second)
                    err := p.client.RenewLease(
                        renewCtx,
                        currentJob.JobID,
                        currentJob.LeaseToken,
                        currentJob.Attempt,
                    )
                    renewCancel()

                    if err != nil {
                        consecutiveFailures++
                        log.Printf(
                            "[WARN] lease renewal failed for job %s (attempt %d/%d): %v",
                            currentJob.JobID,
                            consecutiveFailures,
                            maxConsecutiveFailures,
                            err,
                        )

                        if consecutiveFailures >= maxConsecutiveFailures {
                            log.Printf(
                                "[ERROR] lease renewal failed %d times for job %s, giving up",
                                maxConsecutiveFailures,
                                currentJob.JobID,
                            )
                            return // Exit goroutine to prevent leak
                        }
                    } else {
                        consecutiveFailures = 0 // Reset on success
                    }
                }
            }
        }(job)

        // Execute job with timeout
        executionTimeout := time.Duration(job.TimeoutSeconds) * time.Second
        execCtx, execCancel := context.WithTimeout(jobCtx, executionTimeout)

        err := p.executor.Run(execCtx, job)
        execCancel()

        // Cancel renewal goroutine
        cancel()

        // Wait for renewal goroutine to exit (with timeout)
        select {
        case <-renewDone:
            // Goroutine exited cleanly
        case <-time.After(5 * time.Second):
            log.Printf("[WARN] renewal goroutine did not exit within 5s for job %s", job.JobID)
        }

        // Report result or failure
        if err != nil {
            p.reportFailure(ctx, job, err)
        } else {
            p.reportSuccess(ctx, job)
        }
    }
}
```

---

## P0-3: 失败分类 - 重写推断逻辑

### 问题
失败分类推断规则过度匹配，缺少上下文。

### 修复方案

```python
# backend/core/failure_taxonomy.py

from enum import Enum
from typing import Any

class FailureCategory(str, Enum):
    """Job failure classification for retry strategy."""

    # Transient failures - should retry
    TRANSIENT = "transient"
    TIMEOUT = "timeout"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    NODE_UNHEALTHY = "node_unhealthy"
    NETWORK_ERROR = "network_error"

    # Permanent failures - should not retry
    PERMANENT = "permanent"
    EXECUTION_ERROR = "execution_error"
    INVALID_PAYLOAD = "invalid_payload"
    MISSING_DEPENDENCY = "missing_dependency"
    PERMISSION_DENIED = "permission_denied"

    # System failures
    LEASE_EXPIRED = "lease_expired"
    NODE_DRAINED = "node_drained"
    CANCELED = "canceled"

    # Unknown
    UNKNOWN = "unknown"


def _infer_failure_category(
    error_message: str,
    exit_code: int | None = None,
    error_details: dict[str, Any] | None = None,
) -> FailureCategory:
    """Infer failure category from error message, exit code, and context.

    Args:
        error_message: Error message from job execution
        exit_code: Process exit code (if available)
        error_details: Additional error context

    Returns:
        FailureCategory enum value
    """
    msg_lower = error_message.lower()

    # Priority 1: Use error_details if available
    if error_details:
        if error_details.get("oom_killed"):
            return FailureCategory.RESOURCE_EXHAUSTED

        signal = error_details.get("signal")
        if signal in ("SIGTERM", "SIGKILL"):
            reason = error_details.get("reason", "")
            if reason == "node_drain":
                return FailureCategory.NODE_DRAINED
            if reason == "oom":
                return FailureCategory.RESOURCE_EXHAUSTED
            # Conservative: treat as transient
            return FailureCategory.TRANSIENT

    # Priority 2: Timeout patterns (high confidence)
    if any(p in msg_lower for p in [
        "timeout", "timed out", "deadline exceeded", "context deadline"
    ]):
        return FailureCategory.TIMEOUT

    # Priority 3: Resource exhaustion (high confidence)
    if any(p in msg_lower for p in [
        "out of memory", "oom", "memory limit", "cannot allocate memory"
    ]):
        return FailureCategory.RESOURCE_EXHAUSTED

    if any(p in msg_lower for p in [
        "disk full", "no space left", "enospc", "quota exceeded"
    ]):
        return FailureCategory.RESOURCE_EXHAUSTED

    if any(p in msg_lower for p in [
        "too many open files", "emfile", "enfile", "file descriptor"
    ]):
        return FailureCategory.RESOURCE_EXHAUSTED

    # Priority 4: Network errors (medium confidence)
    # Be careful not to match "connection not found" as network error
    network_patterns = [
        "connection refused", "connection reset", "connection timeout",
        "network unreachable", "host unreachable", "no route to host",
        "econnrefused", "econnreset", "etimedout", "ehostunreach"
    ]
    if any(p in msg_lower for p in network_patterns):
        # Exclude false positives
        if "not found" not in msg_lower:
            return FailureCategory.NETWORK_ERROR

    if any(p in msg_lower for p in [
        "dns", "name resolution", "getaddrinfo", "resolve"
    ]):
        # Exclude "resolve" in other contexts
        if any(ctx in msg_lower for ctx in ["dns", "hostname", "address"]):
            return FailureCategory.NETWORK_ERROR

    # Priority 5: Permission errors (high confidence)
    if any(p in msg_lower for p in [
        "permission denied", "access denied", "forbidden", "unauthorized",
        "eacces", "eperm", "401", "403"
    ]):
        return FailureCategory.PERMISSION_DENIED

    # Priority 6: Missing dependency (medium confidence)
    # Be very careful with "not found" - it's overloaded
    if any(p in msg_lower for p in [
        "no such file", "no such directory", "command not found", "enoent"
    ]):
        # Exclude network-related "not found"
        if not any(net in msg_lower for net in ["connection", "host", "network"]):
            return FailureCategory.MISSING_DEPENDENCY

    if any(p in msg_lower for p in [
        "module not found", "package not found", "import error", "cannot import"
    ]):
        return FailureCategory.MISSING_DEPENDENCY

    # Priority 7: Invalid payload (medium confidence)
    if any(p in msg_lower for p in [
        "invalid", "malformed", "parse error", "syntax error",
        "bad request", "400", "json", "yaml"
    ]):
        # Exclude execution errors
        if "panic" not in msg_lower and "fatal" not in msg_lower:
            return FailureCategory.INVALID_PAYLOAD

    # Priority 8: Execution errors (low confidence)
    if any(p in msg_lower for p in [
        "panic", "fatal", "segmentation fault", "core dumped", "sigsegv"
    ]):
        return FailureCategory.EXECUTION_ERROR

    # Priority 9: Exit code analysis
    if exit_code is not None:
        if exit_code == 137:  # SIGKILL
            # Could be OOM or manual kill - default to resource exhausted
            return FailureCategory.RESOURCE_EXHAUSTED
        elif exit_code in (1, 2, 127):  # Common execution errors
            return FailureCategory.EXECUTION_ERROR
        elif exit_code != 0:
            # Non-zero exit code, but unknown reason
            return FailureCategory.UNKNOWN

    # Default: unknown (conservative)
    return FailureCategory.UNKNOWN


def _should_retry_job(job: "Job", failure_category: FailureCategory) -> bool:
    """Decide if job should be retried based on failure category.

    Args:
        job: Job instance
        failure_category: Failure category

    Returns:
        True if job should be retried, False otherwise
    """
    # Never retry these categories
    if failure_category in {
        FailureCategory.PERMANENT,
        FailureCategory.EXECUTION_ERROR,
        FailureCategory.INVALID_PAYLOAD,
        FailureCategory.MISSING_DEPENDENCY,
        FailureCategory.PERMISSION_DENIED,
        FailureCategory.CANCELED,
    }:
        return False

    # Check retry count
    retry_count = int(job.retry_count or 0)
    max_retries = int(job.max_retries or 0)

    if retry_count >= max_retries:
        return False

    # Check attempt count (global limit)
    attempt_count = int(getattr(job, "attempt_count", 0) or 0)
    if attempt_count >= max_retries + 1:
        return False

    return True
```

---

## 验证清单

### 数据库事务
- [ ] 所有写操作使用事务
- [ ] 行级锁正确使用
- [ ] 隔离级别正确（READ COMMITTED）
- [ ] 无死锁风险

### 并发控制
- [ ] 无竞态条件
- [ ] 无幻读/脏读
- [ ] 乐观锁/悲观锁正确使用

### 错误处理
- [ ] 所有异常被捕获
- [ ] 错误信息清晰
- [ ] 恢复提示完整

### 性能
- [ ] 无 N+1 查询
- [ ] 索引覆盖查询
- [ ] 查询缓存合理

### 可观测性
- [ ] 日志完整
- [ ] 指标完整
- [ ] 追踪完整

### IaC 唯一事实源
- [ ] 所有配置在 system.yaml
- [ ] 无硬编码配置
- [ ] 环境变量正确使用

### 前后端契约
- [ ] API 契约严格
- [ ] 类型安全
- [ ] 错误码统一
