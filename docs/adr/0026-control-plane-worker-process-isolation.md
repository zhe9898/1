# ADR 0026: Control-Plane Worker Process Isolation

- **Status**: Accepted
- **Date**: 2026-03-27
- **Scope**: API ingress boundary, control-plane worker deployment, sentinel sidecar supervision

## 1. Context

The Gateway Kernel already moved `Nodes / Jobs / Connectors` into the default control plane, but two background control workers still had a code path inside the API process:

- `bitrot_worker`
- `health_probe_worker`

Even when disabled by default, keeping the startup path in `backend/api/main.py` left the ingress process coupled to control-plane concerns. The repository also already has a dedicated `sentinel` sidecar service for topology control.

## 2. Decision

### 2.1 API process is ingress-only

`backend/api/main.py` must not start background control workers under any environment toggle.

The API process is limited to:

- ingress routing
- auth
- profile/capability/menu exposure
- control-plane API state transitions
- shared dependency setup/teardown

### 2.2 Control workers run out of process

A dedicated worker entrypoint is added:

- `python -m backend.workers.control_plane_worker --worker all`

This entrypoint is responsible for long-running control workers such as:

- `bitrot_worker`
- `health_probe_worker`

### 2.3 Sentinel sidecar supervises control workers and routing

The existing `sentinel` service becomes the default supervision point for control-plane daemons and now launches:

- topology sentinel
- control-plane worker runtime
- routing operator

This keeps the default kernel service set unchanged while removing ingress/control-plane process coupling.

## 3. Consequences

### Positive

- Gateway ingress no longer has any hidden worker startup path.
- Control-plane worker failures no longer share fate with API worker processes.
- Deployment remains aligned with the existing kernel service set because supervision stays inside the `sentinel` sidecar boundary.
- Runtime routing reconciliation is limited to `runtime/control-plane/routes.json` and `config/Caddyfile`; it no longer requires runtime rewrites of `.env`, `docker-compose.yml`, `render-manifest.json`, `system.yaml`, or Redis ACL material.

### Tradeoffs

- `sentinel` still remains a strong control-plane sidecar and is not yet split into finer-grained controllers.
- `routing_operator` still depends on compiler/Caddy orchestration assets, so sentinel must carry the minimal read/write mounts required for `runtime/control-plane/routes.json` and `config/Caddyfile`.

## 4. Follow-up Constraints

Any future change that does one of the following must update this ADR or create a superseding ADR:

- restarts control workers from the API lifespan path
- makes ingress availability depend on probe/bitrot worker startup
- expands sentinel supervision back into API workers instead of separate control-plane processes
