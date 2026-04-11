"""Normalized trigger fire input contract."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TriggerFireCommand:
    input_payload: dict[str, object]
    context: dict[str, object]
    reason: str | None
    idempotency_key: str | None


def normalize_trigger_fire_command(
    *,
    input_payload: dict[str, object] | None = None,
    context: dict[str, object] | None = None,
    reason: str | None = None,
    idempotency_key: str | None = None,
) -> TriggerFireCommand:
    return TriggerFireCommand(
        input_payload=dict(input_payload or {}),
        context=dict(context or {}),
        reason=(reason or "").strip() or None,
        idempotency_key=(idempotency_key or "").strip() or None,
    )
