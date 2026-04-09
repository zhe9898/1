"""Placement Policy Framework 鈥?pluggable system-level placement solver.

Addresses the gap between per-job scheduling strategies (SPREAD/BINPACK/鈥?
and a true system-level placement optimiser.  The ``PlacementPolicy``
protocol defines three extension points:

1. ``adjust_score``   鈥?modify per-(job, node) scores before ranking.
2. ``rerank``         鈥?global reranking after scoring (e.g. bin-pack
                        consolidation or topology-aware grouping).
3. ``accept``         鈥?final veto gate (resource reservation, anti-
                        starvation cap, etc.).

The ``CompositePlacementPolicy`` chains multiple policies in priority
order so new policies can be added without touching the scoring engine.

Usage in system.yaml::

    scheduling:
      placement_policies:
        - name: resource_reservation
          enabled: true
        - name: thermal_cap
          enabled: true
          config:
            max_thermal: warm
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from backend.kernel.scheduling.job_scheduler import SchedulerNodeSnapshot, ScoredJob
    from backend.models.job import Job

logger = logging.getLogger(__name__)


# 鈹€鈹€ Protocol 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


@runtime_checkable
class PlacementPolicy(Protocol):
    """System-level placement policy extension point."""

    name: str
    order: int  # lower = earlier in pipeline

    def adjust_score(
        self,
        job: Job,
        node: SchedulerNodeSnapshot,
        current_score: int,
        breakdown: dict[str, int],
    ) -> tuple[int, dict[str, int]]:
        """Optionally adjust score after per-job strategy scoring.

        Return (new_total, updated_breakdown).  Default: pass through.
        """
        ...

    def rerank(
        self,
        scored: list[ScoredJob],
        node: SchedulerNodeSnapshot,
    ) -> list[ScoredJob]:
        """Optionally reorder scored jobs for a target node.

        Default: no change.
        """
        ...

    def accept(
        self,
        job: Job,
        node: SchedulerNodeSnapshot,
        score: int,
    ) -> tuple[bool, str]:
        """Final veto: return (accepted, reason).

        Default: accept all.
        """
        ...


# 鈹€鈹€ Built-in policies 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


class ResourceReservationPolicy:
    """Reserve a portion of node capacity for high-priority surge.

    Nodes above ``reserve_pct`` utilisation only accept priority >= ``min_priority``.
    This prevents low-priority batch work from fully saturating the cluster,
    leaving headroom for urgent jobs without requiring preemption.
    """

    name = "resource_reservation"
    order = 10

    def __init__(self, *, reserve_pct: float | None = None, min_priority: int | None = None) -> None:
        if reserve_pct is None or min_priority is None:
            from backend.kernel.policy.policy_store import get_policy_store

            rr = get_policy_store().active.resource_reservation
            if reserve_pct is None:
                reserve_pct = rr.reserve_pct
            if min_priority is None:
                min_priority = rr.min_priority
        self.reserve_pct = reserve_pct
        self.min_priority = min_priority

    def adjust_score(
        self,
        job: Job,
        node: SchedulerNodeSnapshot,
        current_score: int,
        breakdown: dict[str, int],
    ) -> tuple[int, dict[str, int]]:
        return current_score, breakdown

    def rerank(
        self,
        scored: list[ScoredJob],
        node: SchedulerNodeSnapshot,
    ) -> list[ScoredJob]:
        return scored

    def accept(
        self,
        job: Job,
        node: SchedulerNodeSnapshot,
        score: int,
    ) -> tuple[bool, str]:
        if node.max_concurrency <= 0:
            return True, ""
        utilisation = node.active_lease_count / node.max_concurrency
        priority = int(getattr(job, "priority", 0) or 0)
        if utilisation >= self.reserve_pct and priority < self.min_priority:
            return False, f"resource_reservation: util={utilisation:.0%}>={self.reserve_pct:.0%}, pri={priority}<{self.min_priority}"
        return True, ""


class ThermalCapPolicy:
    """Reject placements on thermally-stressed nodes.

    Any node whose ``thermal_state`` is in the blocked set is vetoed
    unless the job explicitly opts out via ``thermal_sensitivity == "none"``.
    """

    name = "thermal_cap"
    order = 20

    def __init__(self, *, blocked_states: frozenset[str] | None = None) -> None:
        self.blocked_states = blocked_states or frozenset({"throttling"})

    def adjust_score(
        self,
        job: Job,
        node: SchedulerNodeSnapshot,
        current_score: int,
        breakdown: dict[str, int],
    ) -> tuple[int, dict[str, int]]:
        return current_score, breakdown

    def rerank(self, scored: list[ScoredJob], node: SchedulerNodeSnapshot) -> list[ScoredJob]:
        return scored

    def accept(self, job: Job, node: SchedulerNodeSnapshot, score: int) -> tuple[bool, str]:
        sensitivity = getattr(job, "thermal_sensitivity", None)
        if sensitivity == "none":
            return True, ""
        if node.thermal_state in self.blocked_states:
            return False, f"thermal_cap: node_state={node.thermal_state}"
        return True, ""


class BinPackConsolidationPolicy:
    """Rerank scored jobs to favour nodes with higher utilisation.

    Applied *after* per-job strategy scoring so that jobs still use their
    declared strategy for the primary score, but the system-level preference
    for consolidation nudges placement toward already-busy nodes.

    This is a global optimization layer on top of per-job strategy.
    """

    name = "binpack_consolidation"
    order = 50

    def __init__(self, *, bonus_weight: float = 0.15) -> None:
        self.bonus_weight = bonus_weight

    def adjust_score(
        self,
        job: Job,
        node: SchedulerNodeSnapshot,
        current_score: int,
        breakdown: dict[str, int],
    ) -> tuple[int, dict[str, int]]:
        if node.max_concurrency <= 0:
            return current_score, breakdown
        util = node.active_lease_count / node.max_concurrency
        bonus = int(current_score * self.bonus_weight * util)
        breakdown["binpack_consolidation"] = bonus
        return current_score + bonus, breakdown

    def rerank(self, scored: list[ScoredJob], node: SchedulerNodeSnapshot) -> list[ScoredJob]:
        return scored

    def accept(self, job: Job, node: SchedulerNodeSnapshot, score: int) -> tuple[bool, str]:
        return True, ""


class PowerAwarePolicy:
    """Penalise nodes with low power headroom.

    Nodes below ``min_headroom_pct`` power headroom receive a score
    penalty, steering placement toward nodes with more power budget.
    """

    name = "power_aware"
    order = 30

    def __init__(self, *, min_headroom_pct: float = 0.15, penalty: int = 25) -> None:
        self.min_headroom_pct = min_headroom_pct
        self.penalty = penalty

    def adjust_score(
        self,
        job: Job,
        node: SchedulerNodeSnapshot,
        current_score: int,
        breakdown: dict[str, int],
    ) -> tuple[int, dict[str, int]]:
        if node.power_capacity_watts <= 0:
            return current_score, breakdown
        headroom = (node.power_capacity_watts - node.current_power_watts) / node.power_capacity_watts
        if headroom < self.min_headroom_pct:
            breakdown["power_aware_penalty"] = -self.penalty
            return current_score - self.penalty, breakdown
        return current_score, breakdown

    def rerank(self, scored: list[ScoredJob], node: SchedulerNodeSnapshot) -> list[ScoredJob]:
        return scored

    def accept(self, job: Job, node: SchedulerNodeSnapshot, score: int) -> tuple[bool, str]:
        return True, ""


class CloudOverflowPolicy:
    """Prefer edge nodes over cloud nodes; cloud nodes act as overflow capacity.

    Applies a configurable score penalty to nodes tagged with ``cloud: true``
    in their ``metadata_json``.  This ensures the scheduler exhausts on-premises
    edge capacity first and spills onto cloud nodes only when edge nodes are
    saturated or unavailable.

    Tag a node as a cloud node by including ``{"cloud": true}`` in its
    ``metadata_json`` (set automatically when ``CLOUD_AUTO_APPROVE_TOKEN``
    matches at registration time).

    Configure in system.yaml::

        scheduling:
          placement_policies:
            - name: cloud_overflow
              enabled: true
              config:
                penalty: 50
    """

    name = "cloud_overflow"
    order = 45

    def __init__(self, *, penalty: int = 50) -> None:
        self.penalty = penalty

    def adjust_score(
        self,
        job: Job,
        node: SchedulerNodeSnapshot,
        current_score: int,
        breakdown: dict[str, int],
    ) -> tuple[int, dict[str, int]]:
        if node.metadata_json.get("cloud") is True:
            breakdown["cloud_overflow_penalty"] = -self.penalty
            return current_score - self.penalty, breakdown
        return current_score, breakdown

    def rerank(self, scored: list[ScoredJob], node: SchedulerNodeSnapshot) -> list[ScoredJob]:
        return scored

    def accept(self, job: Job, node: SchedulerNodeSnapshot, score: int) -> tuple[bool, str]:
        return True, ""


# 鈹€鈹€ Composite policy runner 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


@dataclass
class CompositePlacementPolicy:
    """Chains multiple PlacementPolicy instances in order.

    The composite ensures all policies run in ``order``-sorted sequence
    and short-circuits on the first ``accept()`` rejection.
    """

    policies: list[PlacementPolicy] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.policies.sort(key=lambda p: p.order)

    def adjust_score(
        self,
        job: Job,
        node: SchedulerNodeSnapshot,
        current_score: int,
        breakdown: dict[str, int],
    ) -> tuple[int, dict[str, int]]:
        score = current_score
        for policy in self.policies:
            score, breakdown = policy.adjust_score(job, node, score, breakdown)
        return score, breakdown

    def rerank(
        self,
        scored: list[ScoredJob],
        node: SchedulerNodeSnapshot,
    ) -> list[ScoredJob]:
        for policy in self.policies:
            scored = policy.rerank(scored, node)
        return scored

    def accept(
        self,
        job: Job,
        node: SchedulerNodeSnapshot,
        score: int,
    ) -> tuple[bool, str]:
        for policy in self.policies:
            ok, reason = policy.accept(job, node, score)
            if not ok:
                return False, reason
        return True, ""


# 鈹€鈹€ Registry + loader 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


def _lazy_topology_spread(**kwargs):  # type: ignore[no-untyped-def]
    from backend.kernel.scheduling.scheduling_resilience import TopologySpreadPolicy

    return TopologySpreadPolicy(**kwargs)


_BUILTIN_POLICIES: dict[str, type] = {
    "resource_reservation": ResourceReservationPolicy,
    "thermal_cap": ThermalCapPolicy,
    "binpack_consolidation": BinPackConsolidationPolicy,
    "power_aware": PowerAwarePolicy,
    "cloud_overflow": CloudOverflowPolicy,
    "topology_spread": _lazy_topology_spread,  # type: ignore[dict-item]
}


def load_placement_policies() -> CompositePlacementPolicy:
    """Load placement policies from policy store (sourced from system.yaml at boot).

    Falls back to a sensible default set if config is absent.
    """
    policies_config: list[dict] = []
    try:
        from backend.kernel.policy.policy_store import get_policy_store

        policies_config = get_policy_store().placement_policies_config
    except Exception as exc:
        raise RuntimeError("ZEN-SCHED-PLACEMENT-POLICY-LOAD-FAILED: unable to load placement policies from the policy store") from exc

    policies: list[PlacementPolicy] = []
    if policies_config:
        for entry in policies_config:
            name = entry.get("name", "")
            enabled = entry.get("enabled", True)
            if not enabled or name not in _BUILTIN_POLICIES:
                continue
            cfg = entry.get("config", {}) or {}
            try:
                policies.append(_BUILTIN_POLICIES[name](**cfg))
            except Exception as exc:
                raise RuntimeError(f"ZEN-SCHED-PLACEMENT-POLICY-INIT-FAILED: failed to instantiate placement policy '{name}'") from exc
    else:
        # Default policy set: resource reservation + cloud overflow preference
        policies.append(ResourceReservationPolicy())
        policies.append(CloudOverflowPolicy())

    return CompositePlacementPolicy(policies=policies)


# 鈹€鈹€ Module-level singleton 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

_placement_policy: CompositePlacementPolicy | None = None
_placement_enabled: bool = True

_NOOP_POLICY = CompositePlacementPolicy(policies=[])


def get_placement_policy() -> CompositePlacementPolicy:
    """Return the process-wide composite placement policy singleton.

    If placement policies are disabled via ``set_placement_enabled(False)``,
    returns a no-op composite that passes through all scores unchanged.
    """
    if not _placement_enabled:
        return _NOOP_POLICY
    global _placement_policy
    if _placement_policy is None:
        _placement_policy = load_placement_policies()
    return _placement_policy


def set_placement_enabled(enabled: bool) -> None:
    """Toggle placement-policy evaluation (called from dispatch per feature flag)."""
    global _placement_enabled
    _placement_enabled = enabled


def reload_placement_policies() -> CompositePlacementPolicy:
    """Force re-read from policy store and rebuild the placement policy chain."""
    global _placement_policy
    _placement_policy = load_placement_policies()
    return _placement_policy
