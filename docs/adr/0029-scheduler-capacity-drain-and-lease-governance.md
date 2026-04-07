# ADR 0029: Scheduler Capacity, Drain, and Lease Governance

- Status: Accepted
- Date: 2026-03-27
- Scope: Node fleet governance, lease lifecycle, control-plane operator actions

> Source of truth: code and tests override ADR text. See ADR 0052 when documentation and implementation diverge.

## 1. Context

ADR 0028 made scheduling selector-driven and durable through per-lease attempt history, but the control plane still lacked the next production-grade layer:

- nodes exposed identity and capabilities, but not scheduler-relevant capacity or drain state
- operators could inspect jobs, but could not explicitly drain a runner or cancel/retry work through first-class APIs
- runners could only finish or fail a lease, which made long-running execution opaque and made lease expiry harder to reason about
- the console could show outcomes, but not why a node was blocked or why a job was eligible for a specific runner

That gap is acceptable for a queue demo, not for a distributed Win/Mac fleet.

## 2. Decision

### 2.1 Nodes report scheduler-relevant capacity and governance state

The default node contract now includes:

- `agent_version`
- `max_concurrency`
- computed `active_lease_count`
- `drain_status`
- `health_reason`

The scheduler must reject nodes that are:

- not actively enrolled
- offline or stale
- draining
- already at their declared concurrency limit

### 2.2 Lease lifecycle supports progress and renewal

Machine-authenticated runners may now call:

- `POST /api/v1/jobs/{id}/progress`
- `POST /api/v1/jobs/{id}/renew`

These endpoints remain bound to the active lease owner through `node_id + attempt + lease_token`.

This keeps long-running work observable without weakening lease ownership rules.

### 2.3 Fleet governance and job control are explicit control-plane actions

The control plane now owns these operator actions:

- `POST /api/v1/nodes/{id}/drain`
- `POST /api/v1/nodes/{id}/undrain`
- `POST /api/v1/jobs/{id}/cancel`
- `POST /api/v1/jobs/{id}/retry`
- `GET /api/v1/jobs/{id}/explain`

These actions are backend contracts, not frontend-only behaviors.

### 2.4 Placement decisions must remain explainable

The scheduler explain contract must expose, per node:

- whether the job is eligible
- blocker reasons when it is not
- placement score when it is
- current lease saturation
- drain state
- recent reliability summary

This is required for operator trust and regression review.

## 3. Consequences

### Positive

- Draining a node no longer depends on an informal convention; the scheduler and UI share the same fact.
- Capacity-aware placement prevents saturated runners from over-acquiring leases.
- Long-running work can keep its lease fresh without faking terminal callbacks.
- The UI can render node/job actions and scheduler explain output directly from backend state.

### Tradeoffs

- Node and job contracts are larger and must stay synchronized across backend, frontend, runner, docs, and OpenAPI.
- Lease lifecycle now creates more control-plane events.
- Explain output must stay bounded so placement introspection does not become an unbounded hot path.

## 4. Follow-up constraints

Any future change that does one of the following must update or supersede this ADR:

- removes node capacity or drain state from scheduler eligibility
- reintroduces frontend-only job or node control actions without backend ownership
- weakens lease renewal/progress ownership checks
- hides scheduler blockers or placement reasoning from the explain contract
