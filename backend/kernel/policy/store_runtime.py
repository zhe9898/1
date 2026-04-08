from __future__ import annotations

import datetime
import logging
from collections import deque
from dataclasses import asdict
from typing import Any

from backend.kernel.policy.types import MAX_HISTORY, PolicyVersion, SchedulingPolicy
from backend.kernel.policy.validation import diff_policies, validate_policy

from .yaml_loader import SchedulingPolicyBootstrap, load_policy_bootstrap

logger = logging.getLogger(__name__)


class PolicyStore:
    """Versioned runtime policy store for the kernel policy subdomain."""

    def __init__(self) -> None:
        self._active = SchedulingPolicy()
        self._version: int = 0
        self._history: deque[PolicyVersion] = deque(maxlen=MAX_HISTORY)
        self._frozen: bool = False
        self._freeze_reason: str = ""
        self._audit_log: deque[dict[str, Any]] = deque(maxlen=200)
        self._bootstrap = SchedulingPolicyBootstrap()
        self._history.append(
            PolicyVersion(
                version=0,
                policy=self._active,
                applied_at=datetime.datetime.now(datetime.UTC),
                applied_by="system",
                reason="initial defaults",
            )
        )

    @property
    def active(self) -> SchedulingPolicy:
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
        return dict(self._bootstrap.tenant_quotas_raw)

    @property
    def placement_policies_config(self) -> list[dict[str, Any]]:
        return list(self._bootstrap.placement_policies_raw)

    @property
    def default_service_class_override(self) -> str:
        return self._bootstrap.default_service_class_yaml

    @property
    def resource_quotas_config(self) -> dict[str, Any]:
        return dict(self._bootstrap.resource_quotas_raw)

    @property
    def executor_contracts_config(self) -> dict[str, Any]:
        return dict(self._bootstrap.executor_contracts_raw)

    def apply(
        self,
        new_policy: SchedulingPolicy,
        *,
        operator: str,
        reason: str,
    ) -> PolicyVersion:
        if self._frozen:
            raise ValueError(f"policy store is frozen ({self._freeze_reason}); call unfreeze() first")

        errors = validate_policy(new_policy)
        if errors:
            raise ValueError(f"policy validation failed: {'; '.join(errors)}")

        diff = diff_policies(self._active, new_policy)
        self._version += 1
        now = datetime.datetime.now(datetime.UTC)
        version_record = PolicyVersion(
            version=self._version,
            policy=new_policy,
            applied_at=now,
            applied_by=operator,
            reason=reason,
            diff_summary=diff,
        )
        self._history.append(version_record)
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
        return version_record

    def rollback(
        self,
        target_version: int,
        *,
        operator: str,
        reason: str = "",
    ) -> PolicyVersion:
        if self._frozen:
            raise ValueError(f"policy store is frozen ({self._freeze_reason}); call unfreeze() first")

        target = next((version for version in self._history if version.version == target_version), None)
        if target is None:
            available = [version.version for version in self._history]
            raise ValueError(f"version {target_version} not in history; available: {available}")

        rollback_reason = reason or f"rollback to v{target_version}"
        return self.apply(target.policy, operator=operator, reason=rollback_reason)

    def freeze(self, reason: str = "governance lock") -> None:
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
        previous_reason = self._freeze_reason
        self._frozen = False
        self._freeze_reason = ""
        self._audit_log.append(
            {
                "action": "unfreeze",
                "operator": operator,
                "previous_reason": previous_reason,
                "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            }
        )
        logger.warning("policy store unfrozen by %s (was: %s)", operator, previous_reason)

    def load_from_yaml(self, path: str = "system.yaml") -> None:
        try:
            bootstrap = load_policy_bootstrap(path)
        except Exception:
            logger.debug("system.yaml policy load skipped (file missing or parse error)")
            return

        self._bootstrap = bootstrap
        if bootstrap.policy is None:
            return

        errors = validate_policy(bootstrap.policy)
        if errors:
            logger.warning("system.yaml policy invalid, keeping defaults: %s", errors)
            return

        self._active = bootstrap.policy
        self._version += 1
        now = datetime.datetime.now(datetime.UTC)
        self._history.append(
            PolicyVersion(
                version=self._version,
                policy=bootstrap.policy,
                applied_at=now,
                applied_by="system.yaml",
                reason="loaded from system.yaml",
            )
        )
        logger.info("policy v%d loaded from %s", self._version, path)

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": self._version,
            "frozen": self._frozen,
            "freeze_reason": self._freeze_reason,
            "active_policy": asdict(self._active),
            "history": [
                {
                    "version": version.version,
                    "applied_at": version.applied_at.isoformat(),
                    "applied_by": version.applied_by,
                    "reason": version.reason,
                    "changed_fields": list(version.diff_summary.keys()),
                }
                for version in self._history
            ],
            "recent_audit": list(self._audit_log),
        }

    def get_version_detail(self, version: int) -> PolicyVersion | None:
        return next((entry for entry in self._history if entry.version == version), None)
