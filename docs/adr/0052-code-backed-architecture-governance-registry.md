# ADR 0052: Code-Backed Architecture Governance Registry

- Status: Accepted
- Date: 2026-04-07
- Scope: Architecture governance exports, enforcement gates, and documentation truth order

## 1. Context

ZEN70 now carries a large set of architecture constraints across the kernel, control plane, execution plane, and extension boundary.

The problem was not missing prose. The problem was drift between:

1. reference writeups
2. actual implementation modules
3. test-enforced gates

Without a code-backed registry, architecture discussions could easily become "document says X, code does Y".

## 2. Decision

The repository adopts a code-backed architecture governance registry.

- `backend/kernel/governance/architecture_rules.py` is the aggregation entrypoint.
- It does not create a second architecture mechanism.
- It re-exports already-existing implementation truth from runtime modules.
- ADR text remains explanatory only.

The registry exports two repository-facing views:

### 2.1 Governance rules

`export_architecture_governance_rules()` returns the active rule set, currently `A1..A11`, including:

- rule id
- title
- priority
- maturity
- summary
- enforcement layers
- source modules
- gate tests

### 2.2 Governance snapshot

`export_architecture_governance_snapshot()` returns the current exported contracts and registries used by tests and future tooling.

## 3. Code Evidence

Primary entrypoints:

- `backend/kernel/governance/architecture_rules.py`
- `backend/kernel/surfaces/registry.py`
- `backend/kernel/policy/runtime_policy_resolver.py`
- `backend/kernel/execution/lease_service.py`
- `backend/kernel/execution/fault_isolation.py`
- `backend/kernel/governance/aggregate_owner_registry.py`
- `backend/kernel/contracts/status.py`
- `backend/kernel/extensions/extension_guard.py`

Primary enforcement:

- `backend/tests/unit/test_architecture_governance_gates.py`
- `backend/tests/unit/test_control_plane_runtime_closure.py`
- `backend/tests/unit/test_control_plane_protocol_contracts.py`
- `backend/tests/unit/test_control_plane_worker_runtime.py`

## 4. Rule of Truth

Repository truth is ordered as follows:

1. implementation and exported code contracts
2. tests and enforcement gates
3. ADR text and design notes

This ADR makes that ordering explicit.

If a reference writeup conflicts with a code-backed export, the writeup must be corrected or downgraded. It must not override the implementation by prose alone.

## 5. Consequences

### Positive

- Architecture rules are now enumerable and testable.
- Surface, runtime policy, lease, fault isolation, compatibility, aggregate ownership, and extension budget rules all have one export path.
- Future CI or diagnostics can consume a single governance snapshot instead of scraping multiple documents.

### Tradeoffs

- New architecture rules now require both implementation and gate coverage before they can honestly be marked enforced.
- Documentation updates must follow code changes rather than inventing future-state claims.

## 6. Follow-up

- Any new system-level governance rule should be added first in code, then in tests, then in ADR text.
- Any rule that is only partially implemented must remain marked partial in code until enforcement is real.
- If governance exports are later exposed through an operational API, that API must remain a read-only projection of the backend-owned registry.
