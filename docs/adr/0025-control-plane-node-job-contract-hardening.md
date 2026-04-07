# ADR 0025: Control-Plane Node and Job Contract Hardening

- Status: Accepted
- Date: 2026-03-26
- Scope: Gateway control-plane protocol, runner contract, schema hardening

> Source of truth: code and tests override ADR text. See ADR 0052 when documentation and implementation diverge.

## 1. Context

The repository already treats `Nodes / Jobs / Connectors` as the default Gateway Kernel control plane, but the concrete `node` and `job` contracts were still too weak for heterogeneous Win/Mac/Linux runners and lease-safe execution.

Repository evidence before this change:

- `backend/api/nodes.py` exposed mostly `profile + capabilities + metadata`, which left scheduler-relevant dimensions in loose metadata.
- `backend/api/jobs.py` used `leased_until + skip_locked`, but terminal callbacks did not verify lease ownership, attempt, or idempotency.
- `runner-agent` only reported lightweight metadata and posted results by `job_id` alone.
- Existing databases would not pick up new columns because `Base.metadata.create_all()` does not alter already-created tables.

This left the control plane vulnerable to contract drift, weak heterogeneous scheduling, and stale-result overwrites.

## 2. Decision

### 2.1 Strong node contract

The control plane now treats the following fields as first-class node protocol, not optional metadata:

- `executor`
- `os`
- `arch`
- `zone`
- `protocol_version`
- `lease_version`

`/api/v1/nodes/register` and `/api/v1/nodes/heartbeat` both carry this full contract, and the Go runner reports the same shape on every registration and heartbeat.

### 2.2 Lease-safe job contract

Jobs now use an explicit execution-ownership contract:

- optional `idempotency_key` on create
- server-assigned `attempt`
- server-assigned `lease_token` on pull
- required `node_id + attempt + lease_token` on `result` and `fail`

Terminal callbacks are accepted only from the active lease owner. Replayed callbacks for the same terminal attempt are treated as idempotent.

### 2.3 Schema hardening at initialization boundary

Schema evolution for these protocol fields is applied only in the explicit database initialization path:

- `backend/db.py:init_db()` keeps `create_all()` for bootstrap
- it then runs idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS ...`
- it also creates a unique partial index for `jobs.idempotency_key`

Request paths remain forbidden from triggering DDL or bootstrap side effects.

## 3. Consequences

### Positive

- Heterogeneous runners can be scheduled against explicit platform/executor facts rather than free-form metadata.
- Stale or duplicated result callbacks no longer overwrite terminal state from another lease owner.
- `idempotency_key` becomes a real contract for upstream callers instead of a convention.
- Existing databases can adopt the stronger protocol without destructive resets.

### Tradeoffs

- Older runners that do not send the stronger node/job contract must be upgraded before they are considered first-class participants.
- The control-plane API surface becomes stricter, which is intentional but requires OpenAPI/docs/runner updates to stay aligned.
- This ADR hardens protocol ownership, but does not yet finish the separate controller-process architecture for sentinel/routing/operator.

## 4. Follow-up constraints

Any future change that does one of the following must update this ADR or create a superseding ADR:

- weakens node scheduling facts back into free-form metadata
- allows terminal job callbacks without lease ownership verification
- reintroduces request-path schema self-heal or DDL
- changes `attempt / lease_token / idempotency_key` semantics without updating runner and OpenAPI contracts together
