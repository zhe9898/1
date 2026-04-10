# ADR 0023: V4 Architecture Evolution Roadmap

- Status: Archived
- Date: 2026-04-07
- Scope: Historical debt register for transactions, authentication rotation, and realtime transport

## 1. Context

This file originally described a future V4 roadmap. It no longer matches the repository as an active implementation plan.

Current repository evidence:

- Explicit transaction boundaries already exist across route handlers and services. The codebase is not relying on a pure "commit only at HTTP teardown" model anymore.
  - `backend/control_plane/adapters/jobs/lifecycle.py`
  - `backend/control_plane/adapters/jobs/dispatch.py`
  - `backend/control_plane/adapters/triggers.py`
  - `backend/control_plane/adapters/auth_bootstrap.py`
- Authentication still uses dual-track JWT compatibility and response header token rotation.
  - `backend/core/jwt.py`
  - `backend/control_plane/adapters/deps.py`
  - `frontend/src/utils/http.ts`
  - `frontend/src/utils/httpAuth.ts`
- Cookie-based auth is now primary for browser requests, but the login flow still accepts access-token responses and derives in-memory claims from them.
  - `backend/control_plane/adapters/auth_cookies.py`
  - `frontend/src/stores/auth.ts`
  - `frontend/src/composables/useAuthFlow.ts`
  - `frontend/src/views/InviteView.vue`
- Realtime transport is still SSE plus HTTP callbacks. There is no adopted `/ws/iot` runtime surface in the backend.
  - `backend/control_plane/adapters/routes.py`
  - `frontend/src/utils/sse.ts`
- The actual active architecture has moved into later accepted ADRs and code-backed governance.
  - `docs/adr/0024-gateway-kernel-default-and-backend-driven-control-plane.md`
  - `docs/adr/0025-control-plane-node-job-contract-hardening.md`
  - `docs/adr/0027-node-machine-token-authentication.md`
  - `docs/adr/0028-scheduler-attempt-history-and-operational-overview.md`
  - `docs/adr/0029-scheduler-capacity-drain-and-lease-governance.md`
  - `docs/adr/0046-kernel-only-runtime-surface-and-compatibility-retirement.md`
  - `docs/adr/0049-scheduling-policy-store-single-source-of-truth.md`
  - `docs/adr/0052-code-backed-architecture-governance-registry.md`

## 2. Decision

ADR 0023 is retained only as a historical debt register.

- It is not an accepted implementation baseline.
- It must not be used as authority for current runtime behavior.
- Any future work on transaction boundaries, auth refresh/OIDC, or websocket transport must land through a new ADR with matching code evidence.

## 3. Code-Aligned Reading of the Original Three Topics

### 3.1 Transactions and Unit of Work

The original problem statement is stale.

- The repository already uses explicit `await db.commit()` in multiple route paths.
- High-risk mutations are increasingly pushed into service modules such as `LeaseService`, `JobLifecycleService`, connector services, and trigger/workflow services.
- A formal `SQLAlchemyUnitOfWork` abstraction does not exist in the current codebase.

Therefore:

- ADR 0023 does not authorize claiming that a Unit of Work pattern is already adopted.
- If a real UoW abstraction is introduced later, it requires a separate ADR and migration plan.

### 3.2 Authentication Rotation and Refresh

This debt is still real, but the repository state is different from the old roadmap wording.

- Current backend auth accepts either bearer credentials or the HTTP-only auth cookie.
- Current backend may still emit `X-New-Token` and refresh the auth cookie when a token rotates.
- The frontend keeps claims in memory and uses cookie credentials by default for HTTP and SSE, but it still knows how to absorb rotated access tokens from response headers.

Therefore:

- The repository is not yet on a standard refresh-token or OIDC flow.
- The debt remains open.
- A future migration to `/api/v1/auth/refresh`, refresh tokens, or external OIDC must supersede this ADR with new code-backed documentation.

### 3.3 WebSockets and Realtime Transport

The original "lift the websocket ban" proposal has not been adopted.

- The current browser realtime channel is SSE.
- The current runner/control-plane coordination path is HTTP callback and lease renewal, not websocket multiplexing.
- No `/ws/iot` backend route is part of the current runtime surface.

Therefore:

- ADR 0023 does not authorize claiming websocket transport as part of the current architecture.
- If websocket transport is introduced later, the boundary, allowed payloads, and isolation rules must be captured in a separate ADR.

## 4. Consequences

### Positive

- Prevents an old roadmap from being mistaken for the current architecture baseline.
- Makes later accepted ADRs and code-backed exports the real source of truth.
- Keeps the original technical debt visible without overstating implementation progress.

### Tradeoffs

- This ADR no longer provides a direct implementation plan.
- Open debts remain split across future work items rather than being solved by a single roadmap document.

## 5. Follow-up

- If transaction handling is refactored into a formal Unit of Work abstraction, create a new ADR with code evidence and migration rules.
- If auth moves to refresh tokens or external OIDC, create a new ADR that supersedes the auth portion of this file.
- If websocket transport is introduced, create a new ADR defining the runtime boundary, security model, and operational limits.

## 6. Source-of-Truth Rule

For the topics mentioned here, repository truth is:

1. implementation and exported contracts in code
2. enforcement tests and gates
3. this archived ADR

If this file drifts again, fix or retire the file rather than treating it as a live architecture contract.
