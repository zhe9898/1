# ADR 0036: Kernel IaC Explicit Runtime Guards and Windows-Safe Compiler Writeback

- Status: Accepted
- Date: 2026-03-27
- Updated: 2026-04-10
- Scope: Kernel IaC explicit runtime guards, Windows-safe compiler writeback

> Source of truth: code and tests override ADR text. See ADR 0052 when documentation and implementation diverge.

## Context

The default `gateway-kernel` runtime must be explicit in IaC instead of depending on compiler-side patching for core safety fields.

The current host-first runtime has two baseline classes:

- infrastructure containers: `caddy`, `postgres`, `redis`, `nats`
- host processes: `gateway`, `topology-sentinel`, `control-worker`, `routing-operator`, `runner-agent`

The problems this ADR addresses are:

1. core container runtime guards must be declared or deterministically injected from one compiler path
2. rendered artifacts must stay deterministic on Windows even when atomic replace is denied
3. `render-manifest.json` must describe the real host-first deployment model instead of legacy sidecar naming

## Decision

We keep the compiler as the only write path for generated runtime artifacts and require the runtime guard contract to be explicit and testable.

The compiler must:

- render `docker-compose.yml`, `.env`, `config/Caddyfile`, systemd units, and `render-manifest.json`
- keep Windows-safe text replacement fallback for validated artifacts
- emit a host-first manifest contract with explicit host-process and container copy classes

The remaining container-side default injections apply only to current container services, for example:

- healthcheck fallbacks for `caddy`, `postgres`, `redis`, `docker-proxy`, `watchdog`, and observability services
- OOM protection defaults for `gateway`, `redis`, `watchdog`, and `docker-proxy`

Legacy `sentinel` sidecar defaults are not part of the current runtime guard baseline.

## Consequences

### Positive

- Kernel runtime truth now matches the rendered artifacts and offline bundle validation path.
- Windows writeback stays reliable without creating a second compiler path.
- Default guardrails no longer describe containers that are not part of the adopted host-first runtime.

### Tradeoffs

- Optional pack containers still keep selective compiler defaults where justified.
- Documentation and tests must keep preventing the old sidecar narrative from reappearing as the default runtime story.
