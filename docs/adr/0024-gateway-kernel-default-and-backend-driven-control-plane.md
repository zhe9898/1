# ADR 0024: Gateway Kernel Default and Backend-Driven Control Plane

- Status: Accepted
- Date: 2026-04-08
- Scope: Default runtime shape, control-plane contracts, frontend driving model

## Context

The repository has converged on a kernel-first architecture:

- the only formal runtime surface is `gateway-kernel`
- the control plane is backend-driven
- optional business capability domains are activated through explicit pack contracts
- runtime/build/bootstrap entrypoints are owned by `system.yaml`, `scripts/compiler.py`, and `scripts/bootstrap.py`

## Decision

### 1. Default release shape

ZEN70 is defined first as a Gateway Kernel.

- Default runtime profile: `gateway-kernel`
- Default runtime goal: light control plane, not heavy business workloads
- Default runtime service set: `caddy`, `gateway`, `redis`, `postgres`, `sentinel`, `docker-proxy`, `runner-agent`
- Optional business domains must re-enter through explicit pack selection, not by growing the kernel default

### 2. Default control-plane contract

The kernel control-plane spine consists of:

- `GET /api/v1/profile`
- `GET /api/v1/capabilities`
- `GET /api/v1/console/menu`
- `GET /api/v1/console/overview`
- `GET /api/v1/console/diagnostics`
- `/api/v1/nodes/*`
- `/api/v1/jobs/*`
- `/api/v1/connectors/*`
- `GET /api/v1/events`

### 3. Frontend driving rule

The frontend is a protocol consumer, not a product source of truth.

- navigation comes from backend capability and surface exposure
- runtime state changes prefer backend events and backend-owned status views
- the frontend must not recreate domain state machines locally

### 4. Entrypoint rule

Development mode keeps no compatibility wrappers.

- `system.yaml` is the only formal config root
- `scripts/compiler.py` is the only compiler entrypoint
- `scripts/bootstrap.py` is the only bootstrap entrypoint

## Consequences

### Positive

- Product identity, runtime behavior, and documentation all converge on one kernel-first story.
- The control plane has a clear backend-owned contract spine.
- Pack and runtime evolution can continue without growing a second product surface.

### Tradeoffs

- Historical names remain relevant only as migration history, not as supported runtime identities.
- Documentation and tests must actively prevent reintroduction of wrapper paths or alternate profiles.
