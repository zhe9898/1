# ADR 0028: Scheduler Attempt History and Operational Overview

- Status: Accepted
- Date: 2026-03-27
- Scope: Gateway job scheduling, retry policy, dashboard observability

> Source of truth: code and tests override ADR text. See ADR 0052 when documentation and implementation diverge.

## 1. Context

Gateway Kernel already had secure node enrollment and lease-safe callbacks, but the scheduler and console still behaved like an early queue:

- jobs mostly behaved as FIFO records without explicit selector-driven scheduling
- retry budget fields existed, but failure handling did not requeue work through a control-plane policy
- the control console exposed menu entries, but not an operational view of node health, backlog, stale leases, and connector pressure
- execution history existed only on the current `jobs` row, which made retry/debug/audit flows too weak

That is not sufficient for a distributed Win/Mac runner fleet.

## 2. Decision

### 2.1 Scheduler decisions are selector-driven and scored

The control plane schedules from job constraints, not from runner self-selection.

Jobs now carry selector and priority fields:

- `priority`
- `target_os`
- `target_arch`
- `required_capabilities`
- `target_zone`
- `timeout_seconds`
- `max_retries`
- `estimated_duration_s`
- `source`

`jobs/pull` filters candidates by node eligibility and then scores them using priority, age, scarcity, reliability, locality, active leases, and recent same-node failures.

### 2.2 Every lease produces a durable attempt record

Each job lease writes a `job_attempts` row with:

- `attempt_id`
- `job_id`
- `node_id`
- `lease_token`
- `attempt_no`
- `status`
- `score`
- result/error/timestamps

This separates current job state from execution history and makes retries, stale leases, and node placement auditable.

### 2.3 Retry budget is enforced by the control plane

`jobs/{id}/fail` no longer only marks terminal failure.

If `retry_count < max_retries`, the control plane:

1. records the failed attempt
2. increments `retry_count`
3. clears current lease ownership
4. puts the job back into `pending`

Terminal failure is only reached after the retry budget is exhausted.

### 2.4 Dashboard is backend-driven operational overview

`GET /api/v1/console/overview` is now the dashboard contract.

The overview aggregates:

- node health and enrollment pressure
- queue backlog, high-priority backlog, stale leases, completed and failed jobs
- connector health and attention
- severity-sorted attention items with route destinations

The frontend dashboard renders this API instead of inventing operational state locally.

## 3. Consequences

### Positive

- Win/Mac scheduling becomes selector-aware instead of plain FIFO.
- Retry behavior is now a real control-plane policy, not just a stored integer.
- Operators can inspect attempt history and attention queues directly from the console.
- Dashboard semantics are now tied to backend contracts and easier to test.

### Tradeoffs

- The `jobs` contract is broader and must stay synchronized across backend, frontend, runner, OpenAPI, and docs.
- Pull-path scheduling does more work per request and must stay bounded by candidate limits.
- Attempt history introduces more database writes, which is acceptable because it is required for auditability.

## 4. Follow-up constraints

Any future change that does one of the following must update or supersede this ADR:

- reverts scheduler selection to simple FIFO without selector-aware scoring
- removes durable per-attempt execution history
- bypasses retry budget enforcement in the control plane
- moves dashboard attention logic back into hardcoded frontend assumptions
