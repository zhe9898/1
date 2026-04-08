"""Scheduling Policy Store 鈥?versioned, auditable, rollback-safe governance.

Data types live in ``scheduling_policy_types``, validation/diff in
``scheduling_policy_validation``.  This module owns the runtime singleton,
YAML bootstrap, and re-exports all public symbols so existing
``from backend.kernel.policy.policy_store import X`` continues to work.
"""

from __future__ import annotations

import datetime
import logging
from collections import deque
from dataclasses import asdict
from typing import Any

# Re-export all public types so downstream imports remain unchanged.
from backend.core.scheduling_policy_types import (  # noqa: F401
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
from backend.core.scheduling_policy_validation import (  # noqa: F401
    diff_policies,
    validate_policy,
)

logger = logging.getLogger(__name__)

# Backward-compat alias 鈥?old code references ``_diff_policies``
_diff_policies = diff_policies


# =====================================================================
# PolicyStore 鈥?the runtime governance entry point
# =====================================================================


class PolicyStore:
    """Singleton versioned policy store with audit trail and rollback.

    Lifecycle:
    1. Boot 鈫?``load_from_yaml()`` or starts with defaults (version 0).
    2. Admin 鈫?``apply(new_policy, operator, reason)`` 鈫?validates,
       diffs, bumps version, records audit entry, activates.
    3. Problem 鈫?``rollback(target_version, operator, reason)`` 鈫?       restores a previous policy from history.
    4. Governance sealed 鈫?``freeze()`` 鈫?all mutations blocked.
    """

    def __init__(self) -> None:
        self._active = SchedulingPolicy()
        self._version: int = 0
        self._history: deque[PolicyVersion] = deque(maxlen=MAX_HISTORY)
        self._frozen: bool = False
        self._freeze_reason: str = ""
        self._audit_log: deque[dict[str, Any]] = deque(maxlen=200)
        # Scheduling-level config that lives outside ``scheduling.policy``
        self._tenant_quotas_raw: dict[str, Any] = {}
        self._placement_policies_raw: list[dict[str, Any]] = []
        self._default_service_class_yaml: str = "standard"
        self._resource_quotas_raw: dict[str, Any] = {}
        self._executor_contracts_raw: dict[str, Any] = {}

        # Record initial version
        self._history.append(
            PolicyVersion(
                version=0,
                policy=self._active,
                applied_at=datetime.datetime.now(datetime.UTC),
                applied_by="system",
                reason="initial defaults",
            )
        )

    # 鈹€鈹€ Read 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    @property
    def active(self) -> SchedulingPolicy:
        """Current live policy."""
        return self._active

    @property
    def version(self) -> int:
        return self._version

    @property
    def frozen(self) -> bool:
        return self._frozen

    @property
    def freeze_reason(self) -> str:
        return self._freeze_reason

    @property
    def tenant_quotas_config(self) -> dict[str, Any]:
        """Raw ``scheduling.tenant_quotas`` from system.yaml."""
        return dict(self._tenant_quotas_raw)

    @property
    def placement_policies_config(self) -> list[dict[str, Any]]:
        """Raw ``scheduling.placement_policies`` from system.yaml."""
        return list(self._placement_policies_raw)

    @property
    def default_service_class_override(self) -> str:
        """Top-level ``scheduling.default_service_class`` from system.yaml."""
        return self._default_service_class_yaml

    @property
    def resource_quotas_config(self) -> dict[str, Any]:
        """Raw ``scheduling.resource_quotas`` from system.yaml."""
        return dict(self._resource_quotas_raw)

    @property
    def executor_contracts_config(self) -> dict[str, Any]:
        """Raw ``scheduling.executor_contracts`` from system.yaml."""
        return dict(self._executor_contracts_raw)

    # 鈹€鈹€ Write (guarded) 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    def apply(
        self,
        new_policy: SchedulingPolicy,
        *,
        operator: str,
        reason: str,
    ) -> PolicyVersion:
        """Validate, diff, version, activate a new policy.

        Raises ValueError on validation failure or frozen store.
        """
        if self._frozen:
            raise ValueError(f"policy store is frozen ({self._freeze_reason}); " f"call unfreeze() first")

        errors = validate_policy(new_policy)
        if errors:
            raise ValueError(f"policy validation failed: {'; '.join(errors)}")

        diff = _diff_policies(self._active, new_policy)
        self._version += 1
        now = datetime.datetime.now(datetime.UTC)

        pv = PolicyVersion(
            version=self._version,
            policy=new_policy,
            applied_at=now,
            applied_by=operator,
            reason=reason,
            diff_summary=diff,
        )
        self._history.append(pv)
        self._active = new_policy

        self._audit_log.append(
            {
                "action": "apply",
                "version": self._version,
                "operator": operator,
                "reason": reason,
                "diff_keys": list(diff.keys()),
                "timestamp": now.isoformat(),
            }
        )

        logger.info(
            "policy v%d applied by %s: %s (changed %d fields)",
            self._version,
            operator,
            reason,
            len(diff),
        )
        return pv

    def rollback(
        self,
        target_version: int,
        *,
        operator: str,
        reason: str = "",
    ) -> PolicyVersion:
        """Restore a previous policy version.

        Raises ValueError if version not found or store is frozen.
        """
        if self._frozen:
            raise ValueError(f"policy store is frozen ({self._freeze_reason}); " f"call unfreeze() first")

        target = None
        for pv in self._history:
            if pv.version == target_version:
                target = pv
                break
        if target is None:
            available = [pv.version for pv in self._history]
            raise ValueError(f"version {target_version} not in history; " f"available: {available}")

        rollback_reason = reason or f"rollback to v{target_version}"
        return self.apply(
            target.policy,
            operator=operator,
            reason=rollback_reason,
        )

    # 鈹€鈹€ Freeze / unfreeze 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    def freeze(self, reason: str = "governance lock") -> None:
        """Prevent all policy mutations."""
        self._frozen = True
        self._freeze_reason = reason
        self._audit_log.append(
            {
                "action": "freeze",
                "reason": reason,
                "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            }
        )
        logger.info("policy store frozen: %s", reason)

    def unfreeze(self, *, operator: str) -> None:
        """Allow policy mutations again."""
        prev = self._freeze_reason
        self._frozen = False
        self._freeze_reason = ""
        self._audit_log.append(
            {
                "action": "unfreeze",
                "operator": operator,
                "previous_reason": prev,
                "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            }
        )
        logger.warning("policy store unfrozen by %s (was: %s)", operator, prev)

    # 鈹€鈹€ Load from system.yaml 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    def load_from_yaml(self, path: str = "system.yaml") -> None:
        """Bootstrap policy from system.yaml scheduling section.

        Also caches ``scheduling.tenant_quotas``, ``scheduling.placement_policies``,
        and ``scheduling.default_service_class`` so that downstream modules
        (queue_stratification, placement_policy) read from the store instead
        of parsing system.yaml themselves.

        Safe: falls back to defaults on any parse/IO error.
        """
        try:
            from pathlib import Path

            import yaml  # type: ignore[import-untyped, unused-ignore]

            raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
            sched = raw.get("scheduling", {}) or {}

            # Cache scheduling-level config outside ``policy`` subsection
            self._tenant_quotas_raw = sched.get("tenant_quotas", {}) or {}
            self._placement_policies_raw = sched.get("placement_policies", []) or []
            self._default_service_class_yaml = str(sched.get("default_service_class", "standard"))
            self._resource_quotas_raw = sched.get("resource_quotas", {}) or {}
            self._executor_contracts_raw = sched.get("executor_contracts", {}) or {}

            policy_raw = sched.get("policy", {}) or {}
            if not policy_raw:
                return  # no policy section 鈥?keep defaults

            new_policy = self._parse_yaml_policy(policy_raw)
            errors = validate_policy(new_policy)
            if errors:
                logger.warning("system.yaml policy invalid, keeping defaults: %s", errors)
                return

            self._active = new_policy
            self._version += 1
            now = datetime.datetime.now(datetime.UTC)
            self._history.append(
                PolicyVersion(
                    version=self._version,
                    policy=new_policy,
                    applied_at=now,
                    applied_by="system.yaml",
                    reason="loaded from system.yaml",
                )
            )
            logger.info("policy v%d loaded from %s", self._version, path)
        except Exception:
            logger.debug("system.yaml policy load skipped (file missing or parse error)")

    @staticmethod
    def _parse_yaml_policy(raw: dict) -> SchedulingPolicy:
        """Parse a raw YAML dict into a SchedulingPolicy."""
        # Default instances for safe fallback values (avoids slot descriptors)
        _sc_def = StrategyConfig()
        _qc_def = QueueConfig()

        scoring_raw = raw.get("scoring", {}) or {}
        retry_raw = raw.get("retry", {}) or {}
        freshness_raw = raw.get("freshness", {}) or {}
        admission_raw = raw.get("admission", {}) or {}
        preemption_raw = raw.get("preemption", {}) or {}
        backoff_raw = raw.get("backoff", {}) or {}
        reservation_raw = raw.get("resource_reservation", {}) or {}
        sc_raw = raw.get("service_classes", {}) or {}
        kind_raw = raw.get("kind_defaults", {}) or {}

        # Build sub-configs with safe defaults
        scoring = ScoringWeights(**{k: int(v) for k, v in scoring_raw.items() if k in ScoringWeights.__dataclass_fields__}) if scoring_raw else ScoringWeights()

        retry = RetryPolicy(**{k: v for k, v in retry_raw.items() if k in RetryPolicy.__dataclass_fields__}) if retry_raw else RetryPolicy()

        freshness = (
            NodeFreshnessPolicy(**{k: int(v) for k, v in freshness_raw.items() if k in NodeFreshnessPolicy.__dataclass_fields__})
            if freshness_raw
            else NodeFreshnessPolicy()
        )

        admission = (
            AdmissionPolicy(**{k: int(v) for k, v in admission_raw.items() if k in AdmissionPolicy.__dataclass_fields__})
            if admission_raw
            else AdmissionPolicy()
        )

        preemption = (
            PreemptionPolicy(**{k: int(v) for k, v in preemption_raw.items() if k in PreemptionPolicy.__dataclass_fields__})
            if preemption_raw
            else PreemptionPolicy()
        )

        backoff = BackoffPolicy(**{k: v for k, v in backoff_raw.items() if k in BackoffPolicy.__dataclass_fields__}) if backoff_raw else BackoffPolicy()

        reservation = (
            ResourceReservationConfig(**{k: v for k, v in reservation_raw.items() if k in ResourceReservationConfig.__dataclass_fields__})
            if reservation_raw
            else ResourceReservationConfig()
        )

        # Strategy config (nested sub-configs)
        strat_raw = raw.get("strategy", {}) or {}
        binpack = (
            BinpackConfig(**{k: v for k, v in (strat_raw.get("binpack", {}) or {}).items() if k in BinpackConfig.__dataclass_fields__})
            if strat_raw.get("binpack")
            else BinpackConfig()
        )
        locality = (
            LocalityConfig(**{k: v for k, v in (strat_raw.get("locality", {}) or {}).items() if k in LocalityConfig.__dataclass_fields__})
            if strat_raw.get("locality")
            else LocalityConfig()
        )
        perf_raw = strat_raw.get("performance", {}) or {}
        performance = (
            PerformanceConfig(**{k: v for k, v in perf_raw.items() if k in PerformanceConfig.__dataclass_fields__}) if perf_raw else PerformanceConfig()
        )
        bal_raw = strat_raw.get("balanced", {}) or {}
        balanced = (
            BalancedWeights(**{k: tuple(v) if isinstance(v, list) else v for k, v in bal_raw.items() if k in BalancedWeights.__dataclass_fields__})
            if bal_raw
            else BalancedWeights()
        )
        anti_aff = int(
            strat_raw.get(
                "anti_affinity_penalty",
                _sc_def.anti_affinity_penalty,
            )
        )
        strategy_cfg = StrategyConfig(
            binpack=binpack,
            locality=locality,
            performance=performance,
            balanced=balanced,
            anti_affinity_penalty=anti_aff,
        )

        # Queue config (aging + tenant + starvation)
        queue_raw = raw.get("queue", {}) or {}
        aging_raw = queue_raw.get("aging", {}) or {}
        aging = AgingConfig(**{k: int(v) for k, v in aging_raw.items() if k in AgingConfig.__dataclass_fields__}) if aging_raw else AgingConfig()
        queue_cfg = QueueConfig(
            aging=aging,
            default_tenant_quota=int(
                queue_raw.get(
                    "default_tenant_quota",
                    _qc_def.default_tenant_quota,
                )
            ),
            starvation_threshold_seconds=int(
                queue_raw.get(
                    "starvation_threshold_seconds",
                    _qc_def.starvation_threshold_seconds,
                )
            ),
            priority_layers={k: tuple(v) if isinstance(v, list) else v for k, v in (queue_raw.get("priority_layers", {}) or {}).items()}
            or dict(_qc_def.priority_layers),
            layer_aging_multipliers={k: float(v) for k, v in (queue_raw.get("layer_aging_multipliers", {}) or {}).items()}
            or dict(_qc_def.layer_aging_multipliers),
            tenant_cache_ttl_seconds=float(
                queue_raw.get(
                    "tenant_cache_ttl_seconds",
                    _qc_def.tenant_cache_ttl_seconds,
                )
            ),
            default_service_class=str(
                queue_raw.get(
                    "default_service_class",
                    _qc_def.default_service_class,
                )
            ),
        )

        # Service class definitions
        service_classes: dict[str, ServiceClassDef] = {}
        for name, sc_cfg in sc_raw.items():
            if isinstance(sc_cfg, dict):
                service_classes[name] = ServiceClassDef(**{k: v for k, v in sc_cfg.items() if k in ServiceClassDef.__dataclass_fields__})
        if not service_classes:
            service_classes = dict(SchedulingPolicy().service_classes)

        # Kind defaults
        kind_defaults: dict[str, KindDefault] = {}
        for kind_name, kd_cfg in kind_raw.items():
            if isinstance(kd_cfg, dict):
                kind_defaults[kind_name] = KindDefault(**{k: v for k, v in kd_cfg.items() if k in KindDefault.__dataclass_fields__})

        # Simple sub-configs 鈥?parsed generically from their YAML section
        def _parse_simple(cls: type, section_name: str) -> Any:
            sec = raw.get(section_name, {}) or {}
            if not sec:
                return cls()
            return cls(**{k: v for k, v in sec.items() if k in getattr(cls, "__dataclass_fields__", {})})

        solver = _parse_simple(SolverConfig, "solver")
        priority_boost = _parse_simple(PriorityBoostConfig, "priority_boost")
        sla_risk = _parse_simple(SLARiskConfig, "sla_risk")
        batch_scoring = _parse_simple(BatchScoringConfig, "batch_scoring")
        auto_tune = _parse_simple(AutoTuneConfig, "auto_tune")
        dispatch = _parse_simple(DispatchConfig, "dispatch")
        topology_spread = _parse_simple(TopologySpreadConfig, "topology_spread")
        fair_share = _parse_simple(FairShareConfig, "fair_share")
        backfill = _parse_simple(BackfillPolicyConfig, "backfill")

        return SchedulingPolicy(
            scoring=scoring,
            retry=retry,
            freshness=freshness,
            admission=admission,
            preemption=preemption,
            backoff=backoff,
            resource_reservation=reservation,
            strategy=strategy_cfg,
            queue=queue_cfg,
            service_classes=service_classes,
            kind_defaults=kind_defaults,
            default_strategy=str(raw.get("default_strategy", "spread")),
            solver=solver,
            priority_boost=priority_boost,
            sla_risk=sla_risk,
            batch_scoring=batch_scoring,
            auto_tune=auto_tune,
            dispatch=dispatch,
            topology_spread=topology_spread,
            fair_share=fair_share,
            backfill=backfill,
        )

    # 鈹€鈹€ Diagnostics 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    def snapshot(self) -> dict[str, Any]:
        """Full diagnostic snapshot for admin/explain endpoints."""
        return {
            "version": self._version,
            "frozen": self._frozen,
            "freeze_reason": self._freeze_reason,
            "active_policy": asdict(self._active),
            "history": [
                {
                    "version": pv.version,
                    "applied_at": pv.applied_at.isoformat(),
                    "applied_by": pv.applied_by,
                    "reason": pv.reason,
                    "changed_fields": list(pv.diff_summary.keys()),
                }
                for pv in self._history
            ],
            "recent_audit": list(self._audit_log),
        }

    def get_version_detail(self, version: int) -> PolicyVersion | None:
        """Return full PolicyVersion for a specific version."""
        for pv in self._history:
            if pv.version == version:
                return pv
        return None


# 鈹€鈹€ Module-level singleton 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

_store: PolicyStore | None = None


def get_policy_store() -> PolicyStore:
    """Return the process-wide PolicyStore singleton."""
    global _store
    if _store is None:
        _store = PolicyStore()
        _store.load_from_yaml()
    return _store

