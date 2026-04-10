# ADR 0046: Kernel-Only Runtime Surface and Compatibility Retirement

- Status: Accepted
- Date: 2026-04-08
- Scope: Runtime profile exposure, wrapper removal, and configuration entrypoint cleanup

## Context

The repository had already converged on `gateway-kernel` as the formal runtime surface, but historical profile names and deploy wrappers still created drift in docs and tooling.

## Decision

ZEN70 now enforces a stricter development-time rule set:

- the only supported runtime profile is `gateway-kernel`
- historical profile names are not supported runtime identities
- `system.yaml` is the only formal configuration entrypoint
- `scripts/compiler.py` is the only compiler entrypoint
- `scripts/bootstrap.py` is the only bootstrap entrypoint
- development mode keeps no compatibility wrapper files

## Code Evidence

- `backend/kernel/profiles/public_profile.py`
- `backend/kernel/packs/presets.py`
- `backend/runtime/topology/profile_selection.py`
- `backend/runtime/topology/pack_selection.py`
- `scripts/compiler.py`
- `scripts/bootstrap.py`
- `tests/test_repo_hardening.py`

## Consequences

### Positive

- Runtime surface, build flow, and operator guidance now point to one canonical path.
- Pack selection remains explicit without pretending to be a second profile system.
- The repository no longer carries wrapper code that can diverge from the canonical path.

### Tradeoffs

- Historical names may still appear in negative tests or migration commentary, but not as supported entrypoints.
- Any future attempt to reintroduce a wrapper or alternate runtime profile must be treated as an architecture change, not a convenience patch.
