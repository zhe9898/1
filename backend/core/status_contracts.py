from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StatusContract:
    domain: str
    canonical_values: tuple[str, ...]
    aliases: dict[str, str]

    def normalize(self, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{self.domain} status is required")
        if normalized in self.canonical_values:
            return normalized
        aliased = self.aliases.get(normalized)
        if aliased is None:
            expected = ", ".join(self.canonical_values)
            raise ValueError(f"Unknown {self.domain} status '{value}'. Expected one of: {expected}")
        return aliased

    def legacy_values(self) -> tuple[str, ...]:
        return tuple(sorted(self.aliases.keys()))


_RULES: dict[str, StatusContract] = {
    "nodes.enrollment_status": StatusContract(
        domain="nodes.enrollment_status",
        canonical_values=("pending", "approved", "rejected"),
        aliases={},
    ),
    "triggers.status": StatusContract(
        domain="triggers.status",
        canonical_values=("active", "inactive", "error"),
        aliases={},
    ),
    "trigger_deliveries.status": StatusContract(
        domain="trigger_deliveries.status",
        canonical_values=("dispatching", "delivered", "failed", "retrying"),
        aliases={},
    ),
    "workflows.status": StatusContract(
        domain="workflows.status",
        canonical_values=("pending", "running", "completed", "failed", "cancelled"),
        aliases={},
    ),
    "jobs.status": StatusContract(
        domain="jobs.status",
        canonical_values=("pending", "leased", "completed", "failed", "cancelled"),
        aliases={},
    ),
    "job_attempts.status": StatusContract(
        domain="job_attempts.status",
        canonical_values=("leased", "running", "completed", "failed", "timeout", "cancelled"),
        aliases={},
    ),
    "workflow_steps.status": StatusContract(
        domain="workflow_steps.status",
        canonical_values=("waiting", "running", "completed", "failed", "skipped"),
        aliases={},
    ),
}


def get_status_contract(domain: str) -> StatusContract:
    try:
        return _RULES[domain]
    except KeyError as exc:
        raise KeyError(f"No status contract registered for '{domain}'") from exc


def canonicalize_transport_status(domain: str, value: str) -> str:
    return get_status_contract(domain).normalize(value)


def normalize_persisted_status(domain: str, value: str | None) -> str | None:
    if value is None:
        return None
    return get_status_contract(domain).normalize(value)


def export_status_compatibility_rules() -> dict[str, dict[str, object]]:
    return {
        domain: {
            "canonical_values": list(rule.canonical_values),
            "aliases": dict(rule.aliases),
            "compatibility_window_releases": 0,
        }
        for domain, rule in sorted(_RULES.items())
    }
