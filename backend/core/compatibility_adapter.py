from __future__ import annotations

from backend.core.status_contracts import StatusContract as StatusCompatibilityRule
from backend.core.status_contracts import (
    canonicalize_transport_status,
    export_status_compatibility_rules,
    get_status_contract,
    normalize_persisted_status,
)


def get_status_rule(domain: str) -> StatusCompatibilityRule:
    return get_status_contract(domain)


def canonicalize_status(domain: str, value: str) -> str:
    return canonicalize_transport_status(domain, value)


__all__ = [
    "StatusCompatibilityRule",
    "canonicalize_status",
    "export_status_compatibility_rules",
    "get_status_rule",
    "normalize_persisted_status",
]
