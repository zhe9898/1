# Reservation Contracts

This directory is the control-plane source of truth for reservation and backfill planning.

Scope:
- Control-plane and kernel only
- No business-entity state, reporting products, or BFF semantics
- Reservation lifecycle, backfill planning windows, and time-dimension scheduling diagnostics

Hard invariants:
1. Reservations are tenant-scoped even when node IDs collide across tenants
2. Reservations are keyed by `job_id` and bind a future window on a specific node
3. Low-priority jobs may only backfill when they complete before the earliest conflicting reservation on that node
4. Leasing, completion, failure, cancel, and manual retry must cancel any active reservation for that job
5. SSE and Redis event payloads publish on `reservation:events`

Control-plane endpoints:
- `GET /api/v1/reservations`: list active reservations for the current tenant
- `GET /api/v1/reservations/{job_id}`: inspect a single reservation
- `POST /api/v1/reservations`: create an operator-managed reservation for an existing job
- `POST /api/v1/reservations/{job_id}/cancel`: cancel a reservation
- `GET /api/v1/reservations/stats`: inspect runtime counts, backend, and config
- `GET /api/v1/reservations/nodes/{node_id}/backfill-window`: compute the earliest feasible backfill window for a required duration

Operational semantics:
- Reservations are a time-planning primitive, not a separate execution path
- The dispatch loop may auto-create reservations for high-priority jobs that cannot be placed immediately
- Manual reservation creation is admin-only and only valid for schedulable jobs
- Expired reservations are cleaned up opportunistically during dispatch and surfaced as `expired` events
