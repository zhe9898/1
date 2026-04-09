"""Kernel scheduling policy validation and diff utilities."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .types import SchedulingPolicy


def validate_policy(policy: SchedulingPolicy) -> list[str]:  # noqa: C901
    """Return a list of validation errors. Empty means valid."""
    errors: list[str] = []

    sw = policy.scoring
    if sw.priority_max < 0 or sw.priority_max > 500:
        errors.append(f"scoring.priority_max out of range [0,500]: {sw.priority_max}")
    if sw.age_max < 0 or sw.age_max > 200:
        errors.append(f"scoring.age_max out of range [0,200]: {sw.age_max}")
    if sw.age_half_life_seconds < 60:
        errors.append(f"scoring.age_half_life_seconds too small (<60): {sw.age_half_life_seconds}")
    if sw.load_penalty_max < 0 or sw.load_penalty_max > 200:
        errors.append(f"scoring.load_penalty_max out of range [0,200]: {sw.load_penalty_max}")
    if sw.anti_affinity_penalty < 0 or sw.anti_affinity_penalty > 200:
        errors.append(f"scoring.anti_affinity_penalty out of range [0,200]: {sw.anti_affinity_penalty}")

    rp = policy.retry
    if rp.base_delay_seconds < 1:
        errors.append(f"retry.base_delay_seconds too small (<1): {rp.base_delay_seconds}")
    if rp.max_delay_seconds < rp.base_delay_seconds:
        errors.append("retry.max_delay_seconds < base_delay_seconds")

    fp = policy.freshness
    if fp.stale_after_seconds < fp.grace_period_seconds:
        errors.append("freshness.stale_after_seconds < grace_period_seconds")

    ap = policy.admission
    if ap.max_pending_per_tenant < 1:
        errors.append(f"admission.max_pending_per_tenant < 1: {ap.max_pending_per_tenant}")

    pp = policy.preemption
    if pp.max_per_window < 0:
        errors.append(f"preemption.max_per_window < 0: {pp.max_per_window}")

    bp = policy.backoff
    if bp.base_delay_seconds <= 0:
        errors.append(f"backoff.base_delay_seconds <= 0: {bp.base_delay_seconds}")

    rr = policy.resource_reservation
    if not 0.0 <= rr.reserve_pct <= 1.0:
        errors.append(f"resource_reservation.reserve_pct out of [0,1]: {rr.reserve_pct}")

    for sc_name, sc_def in policy.service_classes.items():
        if sc_def.weight <= 0:
            errors.append(f"service_classes.{sc_name}.weight <= 0: {sc_def.weight}")
        if sc_def.max_jobs_per_round < 1:
            errors.append(f"service_classes.{sc_name}.max_jobs_per_round < 1")

    sc = policy.strategy
    if not 0.0 < sc.binpack.peak_utilization < 1.0:
        errors.append(f"strategy.binpack.peak_utilization out of (0,1): {sc.binpack.peak_utilization}")
    if sc.binpack.sigma <= 0:
        errors.append(f"strategy.binpack.sigma <= 0: {sc.binpack.sigma}")
    if sc.locality.data_locality_points < 0:
        errors.append(f"strategy.locality.data_locality_points < 0: {sc.locality.data_locality_points}")
    if sc.performance.ref_cpu < 1:
        errors.append(f"strategy.performance.ref_cpu < 1: {sc.performance.ref_cpu}")
    for w_name in ("default", "locality_gpu", "locality_only", "compute_heavy"):
        w = getattr(sc.balanced, w_name)
        total = sum(w)
        if abs(total - 1.0) > 0.01:
            errors.append(f"strategy.balanced.{w_name} weights sum to {total}, expected ~1.0")

    qc = policy.queue
    if qc.aging.interval_seconds < 1:
        errors.append(f"queue.aging.interval_seconds < 1: {qc.aging.interval_seconds}")
    if qc.aging.max_bonus < 0:
        errors.append(f"queue.aging.max_bonus < 0: {qc.aging.max_bonus}")
    if qc.default_tenant_quota < 1:
        errors.append(f"queue.default_tenant_quota < 1: {qc.default_tenant_quota}")
    if qc.tenant_cache_ttl_seconds < 0:
        errors.append(f"queue.tenant_cache_ttl_seconds < 0: {qc.tenant_cache_ttl_seconds}")

    pp2 = policy.preemption
    if pp2.min_priority_diff < 0:
        errors.append(f"preemption.min_priority_diff < 0: {pp2.min_priority_diff}")
    if pp2.max_victim_progress < 0 or pp2.max_victim_progress > 1.0:
        errors.append(f"preemption.max_victim_progress out of [0,1]: {pp2.max_victim_progress}")

    bp2 = policy.backoff
    if bp2.max_exponent < 1:
        errors.append(f"backoff.max_exponent < 1: {bp2.max_exponent}")

    rp2 = policy.retry
    if rp2.max_exponent < 1:
        errors.append(f"retry.max_exponent < 1: {rp2.max_exponent}")

    sol = policy.solver
    if sol.dispatch_time_budget_ms < 0:
        errors.append(f"solver.dispatch_time_budget_ms < 0: {sol.dispatch_time_budget_ms}")
    if sol.max_jobs_per_dispatch < 1:
        errors.append(f"solver.max_jobs_per_dispatch < 1: {sol.max_jobs_per_dispatch}")
    if sol.max_nodes_per_dispatch < 1:
        errors.append(f"solver.max_nodes_per_dispatch < 1: {sol.max_nodes_per_dispatch}")
    if sol.max_candidate_pairs_per_dispatch < 1:
        errors.append(f"solver.max_candidate_pairs_per_dispatch < 1: {sol.max_candidate_pairs_per_dispatch}")
    if sol.plan_affinity_bonus < 0:
        errors.append(f"solver.plan_affinity_bonus < 0: {sol.plan_affinity_bonus}")
    if sol.spread_bonus < 0:
        errors.append(f"solver.spread_bonus < 0: {sol.spread_bonus}")

    pb = policy.priority_boost
    if pb.default_priority < 0 or pb.default_priority > 100:
        errors.append(f"priority_boost.default_priority out of [0,100]: {pb.default_priority}")
    if pb.deadline_half_life_seconds < 1:
        errors.append(f"priority_boost.deadline_half_life_seconds < 1: {pb.deadline_half_life_seconds}")

    sr = policy.sla_risk
    if not 0.0 <= sr.critical_threshold <= 1.0:
        errors.append(f"sla_risk.critical_threshold out of [0,1]: {sr.critical_threshold}")

    at = policy.auto_tune
    if at.min_multiplier <= 0:
        errors.append(f"auto_tune.min_multiplier <= 0: {at.min_multiplier}")
    if at.max_multiplier <= at.min_multiplier:
        errors.append("auto_tune.max_multiplier <= min_multiplier")
    if at.learning_rate <= 0 or at.learning_rate > 1.0:
        errors.append(f"auto_tune.learning_rate out of (0,1]: {at.learning_rate}")
    if at.history_window < 1:
        errors.append(f"auto_tune.history_window < 1: {at.history_window}")

    dc = policy.dispatch
    if dc.candidate_max < dc.candidate_min:
        errors.append("dispatch.candidate_max < candidate_min")
    if dc.starvation_rescue_multiplier < 0:
        errors.append(f"dispatch.starvation_rescue_multiplier < 0: {dc.starvation_rescue_multiplier}")
    if dc.starvation_rescue_min < 0:
        errors.append(f"dispatch.starvation_rescue_min < 0: {dc.starvation_rescue_min}")
    if dc.starvation_rescue_max < dc.starvation_rescue_min:
        errors.append("dispatch.starvation_rescue_max < starvation_rescue_min")
    if not 0.0 <= dc.default_reliability_score <= 1.0:
        errors.append(f"dispatch.default_reliability_score out of [0,1]: {dc.default_reliability_score}")
    if dc.dlq_scan_limit < 1:
        errors.append(f"dispatch.dlq_scan_limit < 1: {dc.dlq_scan_limit}")

    ts = policy.topology_spread
    if ts.max_skew < 1:
        errors.append(f"topology_spread.max_skew < 1: {ts.max_skew}")
    if ts.penalty_per_skew < 0:
        errors.append(f"topology_spread.penalty_per_skew < 0: {ts.penalty_per_skew}")

    fs = policy.fair_share
    if fs.max_score_adjustment < 0 or fs.max_score_adjustment > 200:
        errors.append(f"fair_share.max_score_adjustment out of [0,200]: {fs.max_score_adjustment}")
    if not 0.0 <= fs.deadband <= 1.0:
        errors.append(f"fair_share.deadband out of [0,1]: {fs.deadband}")
    if fs.priority_cap < 1:
        errors.append(f"fair_share.priority_cap < 1: {fs.priority_cap}")

    bf = policy.backfill
    if bf.max_reservations < 0:
        errors.append(f"backfill.max_reservations < 0: {bf.max_reservations}")
    if bf.default_estimated_duration_s < 1:
        errors.append(f"backfill.default_estimated_duration_s < 1: {bf.default_estimated_duration_s}")
    if bf.max_backfill_duration_s < 0:
        errors.append(f"backfill.max_backfill_duration_s < 0: {bf.max_backfill_duration_s}")
    if bf.planning_horizon_s < 1:
        errors.append(f"backfill.planning_horizon_s < 1: {bf.planning_horizon_s}")
    if bf.min_gap_s < 0:
        errors.append(f"backfill.min_gap_s < 0: {bf.min_gap_s}")
    if bf.reservation_min_priority < 0 or bf.reservation_min_priority > 100:
        errors.append(f"backfill.reservation_min_priority out of [0,100]: {bf.reservation_min_priority}")

    return errors


def diff_policies(old: SchedulingPolicy, new: SchedulingPolicy) -> dict[str, Any]:
    """Compute a flat diff of changed fields between two policies."""
    old_d = _flatten_dict(asdict(old))
    new_d = _flatten_dict(asdict(new))
    diff: dict[str, Any] = {}
    all_keys = set(old_d) | set(new_d)
    for key in sorted(all_keys):
        ov = old_d.get(key)
        nv = new_d.get(key)
        if ov != nv:
            diff[key] = {"old": ov, "new": nv}
    return diff


def _flatten_dict(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Flatten nested dicts with dot-separated keys."""
    flat: dict[str, Any] = {}
    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(_flatten_dict(value, full_key))
        else:
            flat[full_key] = value
    return flat
