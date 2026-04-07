from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StatusCompatibilityRule:
    domain: str
    canonical_values: tuple[str, ...]
    aliases: dict[str, str]

    def canonicalize(self, value: str) -> str:
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


_RULES: dict[str, StatusCompatibilityRule] = {
    # Current repository canonical values remain the domain truth.
    # Legacy aliases are confined to transport / adapter paths.
    "nodes.enrollment_status": StatusCompatibilityRule(
        domain="nodes.enrollment_status",
        canonical_values=("pending", "active", "revoked"),
        aliases={"approved": "active", "rejected": "revoked"},
    ),
    "triggers.status": StatusCompatibilityRule(
        domain="triggers.status",
        canonical_values=("active", "paused", "error"),
        aliases={"inactive": "paused"},
    ),
    "workflows.status": StatusCompatibilityRule(
        domain="workflows.status",
        canonical_values=("pending", "running", "completed", "failed", "canceled"),
        aliases={"cancelled": "canceled"},
    ),
}


def get_status_rule(domain: str) -> StatusCompatibilityRule:
    try:
        return _RULES[domain]
    except KeyError as exc:
        raise KeyError(f"No compatibility rule registered for '{domain}'") from exc


def canonicalize_status(domain: str, value: str) -> str:
    return get_status_rule(domain).canonicalize(value)


def export_status_compatibility_rules() -> dict[str, dict[str, object]]:
    return {
        domain: {
            "canonical_values": list(rule.canonical_values),
            "aliases": dict(rule.aliases),
            "compatibility_window_releases": 2,
        }
        for domain, rule in sorted(_RULES.items())
    }
