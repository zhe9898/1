# ADR 0024: Gateway Kernel Default and Backend-Driven Control Plane

- **Status**: Accepted
- **Date**: 2026-03-26
- **Scope**: Default release shape, control-plane contracts, frontend driving model

## 1. Context

The repository has moved away from a "full platform by default" direction and now has enough concrete code to treat the product as a **Gateway Kernel** first.

Repository evidence:

- `system.yaml` sets `deployment.profile: gateway-kernel` and keeps `gateway` as a compatibility alias alongside `full`.
- `backend/api/main.py` already distinguishes `gateway-core` from the expanded profile and keeps the default router set to `routes`, `auth`, `settings`, `nodes`, `jobs`, and `connectors`.
- `backend/api/cluster.py` remains in the codebase, but `backend/api/main.py` only mounts it outside `gateway-core`, which means cluster is not the default control-plane spine.
- `backend/capabilities.py` now exposes gateway control-plane surfaces through capability items such as `Gateway Dashboard`, `Gateway Nodes`, `Gateway Jobs`, `Gateway Connectors`, and `Gateway Settings`.
- `frontend/src/router/index.ts` keeps the default route set narrowed to `dashboard`, `nodes`, `jobs`, `connectors`, and `settings`.
- `frontend/src/constants/controlPlane.ts`, `frontend/src/App.vue`, and `frontend/src/views/ControlDashboard.vue` drive menu/cards from backend capability exposure rather than the old family-platform mainline.
- `backend/api/nodes.py`, `backend/api/jobs.py`, and `backend/api/connectors.py` provide the minimal REST control-plane contracts.
- `backend/api/routes.py` forwards `node:events`, `job:events`, and `connector:events` through SSE, while `frontend/src/utils/sse.ts` and the related Pinia stores consume those events to update UI state.

This is enough to formalize a new default architectural decision without assuming that cluster orchestration is complete.

## 2. Decision

### 2.1 Default release shape

ZEN70 is defined first as a **Gateway Kernel**.

- Default release profile: `gateway-kernel` (runtime alias: `gateway-core`; compatibility alias: `gateway`)
- Non-default expansion profile: `full`
- Default deployment target: bypass router, mini PC, or other light edge host
- Default runtime goal: light control plane, not heavy business workloads
- Default runtime service set: `caddy`, `gateway`, `redis`, `postgres`, `sentinel`, `docker-proxy`
- Optional-by-default ingress service: `mosquitto`

### 2.2 Default control-plane contract

The default kernel contract consists of:

- `GET /api/v1/capabilities`
- `GET /api/v1/events` plus heartbeat continuation
- Node protocol: `/api/v1/nodes/*`
- Job protocol: `/api/v1/jobs/*`
- Connector protocol: `/api/v1/connectors/*`
- Required gateway support routes: auth, settings, base routes

`cluster` remains an overview/status surface only. It is explicitly **not** the future primary control-plane domain unless a later ADR changes that.

### 2.3 Frontend driving rule

The default frontend must be treated as a **protocol consumer**, not as the product source of truth.

- Navigation, dashboard cards, and feature entry exposure should be driven by backend capability exposure first.
- Runtime state changes should prefer SSE control-plane events first.
- Static route definitions may remain for bootstrapping, but the default visible menu must not revert to the old family-platform domain list.

### 2.4 Extensibility rule

Heavy domains remain in the repository, but outside the default kernel path:

- media
- observability
- cloud tunnel
- watchdog
- optional MQTT ingress
- future business or full-platform modules

They can re-enter through explicit profile expansion, not by growing the default kernel release.

## 3. Consequences

### Positive

- Aligns the shipped default with the current control-plane code, not with the historical full-platform narrative.
- Preserves future `full` expansion without forcing heavy runtime requirements on edge deployment.
- Establishes a clear protocol spine: capabilities + events + nodes + jobs + connectors.
- Makes backend-driven frontend behavior an explicit architecture rule rather than an implementation detail.

### Tradeoffs

- Checked-in deployment artifacts and docs must continue to track `system.yaml` and the compiler output; otherwise "default profile" claims will drift again.
- Legacy platform pages can stay in the repository, but they are no longer the default information architecture.
- Future work still needs schema hardening, migrations, and broader OpenAPI export alignment for the new control-plane surfaces.

## 4. Follow-up constraints

Any future change that does one of the following must update this ADR or create a superseding ADR:

- changes the default release away from `gateway`
- promotes heavy services back into the default runtime
- makes `cluster` the primary control-plane API
- reintroduces frontend-first menu ownership instead of backend capability ownership
