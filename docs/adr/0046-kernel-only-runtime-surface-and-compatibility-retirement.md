# ADR 0046: Kernel-Only Runtime Surface and Compatibility Retirement

- Status: Accepted
- Date: 2026-04-07
- Scope: Runtime profile exposure, legacy profile compatibility, compiler entrypoint, and configuration entrypoint cleanup

## 1. Context

The repository has already converged on `gateway-kernel` as the primary runtime surface, but compatibility inputs still exist for migration and pack selection.

Current repository evidence:

- Runtime profile normalization collapses legacy aliases back to `gateway-kernel`.
  - `backend/core/gateway_profile.py`
  - `backend/core/pack_registry.py`
  - `backend/tests/unit/test_gateway_profiles.py`
- The default public/control-plane surface is kernel-first, while optional surfaces are enabled by explicit pack selection.
  - `backend/core/gateway_profile.py`
  - `backend/api/main.py`
- `deploy/config-compiler.py` is now a wrapper that forwards to the canonical compiler.
  - `deploy/config-compiler.py`
  - `scripts/compiler.py`
  - `tests/test_repo_hardening.py`
- The repository has one formal configuration root: `system.yaml`.
  - `E:/1.0/1/system.yaml`
- `config/system.yaml` is not part of the current repository surface and is explicitly blocked from official bundle/repo paths.
  - `tests/test_repo_hardening.py`
  - `scripts/validate_offline_bundle.py`
  - `.github/workflows/build_offline_v2_9.yml`

The old version of this ADR correctly identified the direction, but its text had become unreadable and no longer served as a reliable contract artifact.

## 2. Decision

ZEN70 exposes one primary runtime profile surface:

- primary runtime profile: `gateway-kernel`

The following values remain compatibility-only inputs:

- `gateway`
- `gateway-core`
- `safe-kernel`
- `gateway-iot`
- `gateway-ops`
- pack aliases such as `iot-pack`

Compatibility inputs may still expand into pack selections, router selections, or image targets, but they are not separate product identities.

Additional rules:

- official runtime/profile documentation must speak in terms of `gateway-kernel` plus explicit packs
- `deploy/config-compiler.py` is only a compatibility wrapper around `scripts/compiler.py`
- root `system.yaml` is the only formal configuration entrypoint for the repository surface
- `config/system.yaml` must remain excluded from official repo and offline-bundle surfaces

## 3. Code Evidence

Primary implementation:

- `backend/core/gateway_profile.py`
- `backend/core/pack_registry.py`
- `backend/api/main.py`
- `scripts/compiler.py`
- `deploy/config-compiler.py`

Primary enforcement:

- `backend/tests/unit/test_gateway_profiles.py`
- `backend/tests/unit/test_architecture_governance_gates.py`
- `tests/test_profile_surface_compaction.py`
- `tests/test_repo_hardening.py`

Supporting evidence:

- `system.yaml`
- `scripts/validate_offline_bundle.py`
- `.github/workflows/build_offline_v2_9.yml`

## 4. Consequences

### Positive

- Runtime documentation, compiler invocation, and release shape all converge on one kernel-first story.
- Legacy profile names remain usable for migration without being mistaken for first-class runtime identities.
- The configuration entrypoint is explicit and testable.

### Tradeoffs

- Legacy aliases still exist in normalization and pack-resolution code, so they must be documented as compatibility-only rather than silently assumed gone.
- Some tooling and docs still mention historical profile names because they describe migration behavior or image targets.
- Compatibility retirement is a process, not a single delete.

## 5. Follow-up

- Any future removal of `gateway-iot` or `gateway-ops` compatibility inputs must be reflected in `pack_registry`, tests, and this ADR.
- If a second official config root is ever introduced, this ADR must be superseded rather than informally bypassed.
- Compiler/operator docs outside `docs/adr` should continue to be cleaned so they do not imply `config/system.yaml` is still an official repo entrypoint.

## 6. Source-of-Truth Rule

For runtime surface and config-entrypoint questions, repository truth is:

1. `gateway_profile` / `pack_registry` / compiler implementation
2. repo hardening and profile tests
3. this ADR
