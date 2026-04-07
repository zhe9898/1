# ADR 0049: Scheduling PolicyStore as the Runtime Policy Source of Truth

- Status: Accepted
- Date: 2026-04-07
- Scope: Scheduling runtime policy reads, versioned policy mutation, and `system.yaml` bootstrap boundaries

## 1. Context

The scheduling stack previously relied on scattered `system.yaml` parsing. The repository has since converged on a runtime policy boundary built around `PolicyStore` and `RuntimePolicyResolver`.

Current repository evidence:

- `PolicyStore` owns the runtime singleton, policy history, freeze/unfreeze, rollback, and YAML bootstrap.
  - `backend/core/scheduling_policy_store.py`
- `RuntimePolicyResolver` is the backend-facing entrypoint for runtime policy queries.
  - `backend/core/runtime_policy_resolver.py`
- Architecture gates block direct runtime `system.yaml` reads outside the allowlist.
  - `backend/tests/unit/test_architecture_governance_gates.py`
- The code-backed architecture registry already treats runtime policy as an enforced rule.
  - `backend/core/architecture_governance.py`
  - `docs/adr/0052-code-backed-architecture-governance-registry.md`

This ADR exists to capture that runtime boundary cleanly and in readable form.

## 2. Decision

ZEN70 treats `PolicyStore` as the runtime policy source of truth for scheduling-related behavior.

### 2.1 Runtime reads

Runtime scheduling code must read policy through:

- `backend.core.scheduling_policy_store.get_policy_store`
- `backend.core.runtime_policy_resolver.RuntimePolicyResolver`

It must not directly parse and cache `system.yaml` for runtime policy decisions outside approved bootstrap/import paths.

### 2.2 Bootstrap boundary

`system.yaml` remains the declarative bootstrap source.

Allowed roles for direct `system.yaml` reads include:

- compiler and render tooling
- policy bootstrap/loading
- explicit test and validation tooling

This means:

- `system.yaml` is still important
- but runtime consumers should read the resolved store, not the raw file

### 2.3 Governance features

`PolicyStore` owns versioned policy mutation features including:

- active policy snapshot
- version tracking
- history
- apply
- rollback
- freeze / unfreeze
- audit-style mutation log

## 3. Code Evidence

Primary implementation:

- `backend/core/scheduling_policy_store.py`
- `backend/core/runtime_policy_resolver.py`
- `backend/core/architecture_governance.py`

Primary enforcement:

- `backend/tests/unit/test_scheduling_policy_store.py`
- `backend/tests/unit/test_architecture_governance_gates.py`
- `backend/tests/unit/test_tenant_fair_share.py`

Supporting runtime consumers and integrations:

- `backend/api/jobs/dispatch.py`
- `backend/core/gateway_profile.py`
- `backend/core/executor_registry.py`
- `backend/core/placement_policy.py`
- `backend/core/queue_stratification.py`
- `backend/core/quota_aware_scheduling.py`

## 4. Consequences

### Positive

- Runtime policy behavior is now queryable through one backend contract instead of scattered file parsing.
- Freeze/rollback/version history give policy changes an operational boundary.
- Architecture gates can enforce the difference between bootstrap config and runtime policy consumption.

### Tradeoffs

- `PolicyStore` is now a critical runtime abstraction and must stay small, explicit, and well-tested.
- Some bootstrap/test paths still read raw `system.yaml`, which is acceptable but must not spread back into runtime code.
- Old prose that described "all policy comes directly from system.yaml" is no longer accurate unless it refers specifically to bootstrap.

## 5. Follow-up

- If more policy domains are added, they should extend the store/resolver boundary rather than add new ad hoc readers.
- If a future external policy service replaces in-process storage, this ADR must be superseded with a migration plan and matching code evidence.
- Docs outside `docs/adr` should continue to distinguish bootstrap config from runtime policy reads.

## 6. Source-of-Truth Rule

For scheduling runtime policy questions, repository truth is:

1. `scheduling_policy_store` and `runtime_policy_resolver`
2. architecture gates and policy-store tests
3. this ADR
