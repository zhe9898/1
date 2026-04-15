# ADR 0051: IaC Three-Layer Field Resolution

- Status: Accepted
- Date: 2026-04-04
- Updated: 2026-04-10
- Scope: `system.yaml` precedence, compiler defaults, global fallback rules

> Source of truth: code and tests override ADR text. See ADR 0052 when documentation and implementation diverge.

## Context

The IaC compiler translates `system.yaml` into rendered artifacts. Some fields still need deterministic defaults, but those defaults must not silently override the source-of-truth config.

The repository uses a three-layer rule:

1. `system.yaml` service declaration wins
2. service-specific compiler defaults apply only when the field is omitted
3. global fallback applies only when neither of the above provides a value

## Current Field Rules

### Networks

- Layer 1: `services.<name>.networks`
- Layer 2: `cloudflared -> [frontend_net, backend_net]`
- Layer 3: all other containers -> `[backend_net]`

### Ulimits

- Layer 1: `services.<name>.ulimits`
- Layer 2: `gateway` and `redis` get `nofile >= 65536`
- Layer 3: no extra fallback for other services

### OOM Score Adjustment

- Layer 1: `services.<name>.oom_score_adj`
- Layer 2: `gateway`, `redis`, `watchdog`, `docker-proxy` get `-999`
- Layer 3: no extra fallback for other services

The removed legacy `sentinel` sidecar is not part of this default matrix.

## Why This Model

- It preserves `system.yaml` as the only authored configuration root.
- It keeps common safety defaults centralized in one compiler module.
- It makes the remaining implicit behavior explicit enough to test and document.

## Consequences

- Teams can override service-specific runtime knobs without editing compiler code.
- The compiler still provides a safe baseline for omitted fields.
- When a service stops being part of the current runtime model, its defaults must be removed from this ADR and from the compiler together.
