from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


SYNC_PLUGIN_BUDGET_MS = 100
ASYNC_PLUGIN_BUDGET_MS = 500
MAX_PLUGIN_PAYLOAD_BYTES = 64 * 1024
MAX_AUDIT_DETAILS_BYTES = 16 * 1024
MAX_SYNC_EXTERNAL_CALLS = 0
MAX_ASYNC_EXTERNAL_CALLS = 2
MAX_PLUGINS_PER_PHASE = 4
MAX_PLUGINS_TOTAL = 16


@dataclass(frozen=True, slots=True)
class ExtensionBudget:
    phase: str
    execution_budget_ms: int
    payload_limit_bytes: int = MAX_PLUGIN_PAYLOAD_BYTES
    audit_details_limit_bytes: int = MAX_AUDIT_DETAILS_BYTES
    external_call_limit: int = MAX_SYNC_EXTERNAL_CALLS


def _phase_default_budget(phase: str) -> ExtensionBudget:
    if phase == "post_bind":
        return ExtensionBudget(
            phase=phase,
            execution_budget_ms=ASYNC_PLUGIN_BUDGET_MS,
            external_call_limit=MAX_ASYNC_EXTERNAL_CALLS,
        )
    return ExtensionBudget(phase=phase, execution_budget_ms=SYNC_PLUGIN_BUDGET_MS)


def plugin_budget_for(phase: str, plugin: object) -> ExtensionBudget:
    defaults = _phase_default_budget(phase)
    return ExtensionBudget(
        phase=phase,
        execution_budget_ms=int(getattr(plugin, "execution_budget_ms", defaults.execution_budget_ms)),
        payload_limit_bytes=int(getattr(plugin, "payload_limit_bytes", defaults.payload_limit_bytes)),
        audit_details_limit_bytes=int(getattr(plugin, "audit_details_limit_bytes", defaults.audit_details_limit_bytes)),
        external_call_limit=int(getattr(plugin, "external_call_limit", defaults.external_call_limit)),
    )


def validate_extension_manifest_contract(manifest: object) -> None:
    source_manifest_path = getattr(manifest, "source_manifest_path", None)
    if source_manifest_path is None and getattr(manifest, "extension_id", "") != "zen70.core":
        raise ValueError("External extensions must be traceable to a manifest path")


def validate_scheduling_profile_budget(profile: object) -> None:
    phase_plugins: dict[str, list[object]] = {
        "queue_sort": list(getattr(profile, "queue_sort", [])),
        "pre_filter": list(getattr(profile, "pre_filters", [])),
        "filter": list(getattr(profile, "filters", [])),
        "post_filter": list(getattr(profile, "post_filters", [])),
        "score": list(getattr(profile, "scorers", [])),
        "reserve": list(getattr(profile, "reservers", [])),
        "permit": list(getattr(profile, "permits", [])),
        "pre_bind": list(getattr(profile, "pre_binders", [])),
        "bind": list(getattr(profile, "binders", [])),
        "post_bind": list(getattr(profile, "post_binders", [])),
    }
    total = sum(len(items) for items in phase_plugins.values())
    if total > MAX_PLUGINS_TOTAL:
        raise ValueError(f"Scheduling profile exceeds total extension budget ({total}>{MAX_PLUGINS_TOTAL})")
    for phase, plugins in phase_plugins.items():
        if len(plugins) > MAX_PLUGINS_PER_PHASE:
            raise ValueError(f"Scheduling phase '{phase}' exceeds plugin budget ({len(plugins)}>{MAX_PLUGINS_PER_PHASE})")
        for plugin in plugins:
            budget = plugin_budget_for(phase, plugin)
            if budget.execution_budget_ms > _phase_default_budget(phase).execution_budget_ms:
                raise ValueError(f"Plugin '{getattr(plugin, 'name', phase)}' exceeds execution budget for phase '{phase}'")
            if budget.payload_limit_bytes > MAX_PLUGIN_PAYLOAD_BYTES:
                raise ValueError(f"Plugin '{getattr(plugin, 'name', phase)}' exceeds payload budget")
            if budget.audit_details_limit_bytes > MAX_AUDIT_DETAILS_BYTES:
                raise ValueError(f"Plugin '{getattr(plugin, 'name', phase)}' exceeds audit-details budget")
            allowed_calls = MAX_ASYNC_EXTERNAL_CALLS if phase == "post_bind" else MAX_SYNC_EXTERNAL_CALLS
            if budget.external_call_limit > allowed_calls:
                raise ValueError(f"Plugin '{getattr(plugin, 'name', phase)}' exceeds external-call budget for phase '{phase}'")


def measured_payload_size(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def assert_budgeted_payload(payload: dict[str, Any], *, budget_bytes: int = MAX_PLUGIN_PAYLOAD_BYTES) -> None:
    size = measured_payload_size(payload)
    if size > budget_bytes:
        raise ValueError(f"Payload exceeds extension budget ({size}>{budget_bytes} bytes)")
