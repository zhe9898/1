"""Kernel policy entrypoint.

`backend.kernel.policy.policy_store` is the canonical runtime entrypoint for
versioned scheduling policy state. The store implementation and YAML parsing
live in dedicated submodules so the kernel policy boundary stays modular.
"""

from __future__ import annotations

from backend.kernel.policy.types import (  # noqa: F401
    MAX_HISTORY,
    AdmissionPolicy,
    AgingConfig,
    AutoTuneConfig,
    BackfillPolicyConfig,
    BackoffPolicy,
    BalancedWeights,
    BatchScoringConfig,
    BinpackConfig,
    DispatchConfig,
    FairShareConfig,
    KindDefault,
    LocalityConfig,
    NodeFreshnessPolicy,
    PerformanceConfig,
    PolicyVersion,
    PreemptionPolicy,
    PriorityBoostConfig,
    QueueConfig,
    ResourceReservationConfig,
    RetryPolicy,
    SchedulingPolicy,
    ScoringWeights,
    ServiceClassDef,
    SLARiskConfig,
    SolverConfig,
    StrategyConfig,
    TopologySpreadConfig,
)
from backend.kernel.policy.validation import (  # noqa: F401
    diff_policies,
    validate_policy,
)

from .store_runtime import PolicyStore

_diff_policies = diff_policies

_store: PolicyStore | None = None


def get_policy_store() -> PolicyStore:
    global _store
    if _store is None:
        _store = PolicyStore()
        _store.load_from_yaml()
    return _store
