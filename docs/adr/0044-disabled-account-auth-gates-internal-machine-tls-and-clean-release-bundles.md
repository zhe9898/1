# ADR 0044: Disabled-Account Auth Gates, Internal Machine TLS, and Clean Release Bundles

- Status: Accepted
- Date: 2026-04-08
- Scope: auth hardening, internal machine TLS, release bundle cleanliness

## Context

The control plane already had tenant scope, node tokens, and backend-driven orchestration, but several operational paths still allowed unsafe defaults or noisy release inputs.

## Decision

1. Disabled accounts must be blocked consistently across password, PIN, WebAuthn, and invite downgrade paths.
2. Default machine traffic runs through internal TLS.
3. Release bundles exclude audit leftovers and non-canonical config roots.
4. Canonical release entrypoints remain `system.yaml`, `scripts/compiler.py`, and `scripts/bootstrap.py`.

## Consequences

### Positive

- Disabled-account governance becomes real rather than cosmetic.
- Runner deployments become secure-by-default.
- Offline bundles no longer imply multiple config roots or legacy entrypoints.

### Tradeoffs

- Recovery and invite flows are stricter.
- Operators must preserve the internal CA mount for runner TLS validation.
