# ADR 0043: Runner HTTPS Default, Immutable Offline Release, and Locked CI Dependencies

- Status: Accepted
- Date: 2026-04-08
- Scope: Runner transport defaults, offline release immutability, locked CI dependencies

## Context

The runner channel carries node tokens and lease callbacks, so plaintext defaults and mutable release inputs create avoidable risk.

## Decision

1. `runner-agent` defaults to HTTPS transport.
2. Non-loopback runner traffic requires explicit secure transport.
3. Offline releases must be immutable and checksum-verified.
4. Python CI must install from locked dependencies with hashes.
5. Local bootstrap must prefer deterministic package managers such as `npm ci`.

## Code Evidence

- `runner-agent/internal/config/config.go`
- `runner-agent/internal/api/client.go`
- `runner-agent/internal/service/service.go`
- `.github/workflows/build_offline_v2_9.yml`
- `.github/workflows/ci.yml`
- `.github/workflows/compliance.yml`
- `backend/requirements-ci.lock`
- `scripts/bootstrap.py`
- `tests/test_repo_hardening.py`

## Consequences

- The default machine channel is secure by default.
- Offline release assets are tied to deterministic inputs and checksums.
- CI no longer drifts with floating Python dependency resolution.
