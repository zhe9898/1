from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from backend.sentinel.sentinel_helpers import HWState


class MountTransitionAction(str, Enum):
    NOOP = "noop"
    MARK_PENDING_OFFLINE = "mark_pending_offline"
    VERIFY_PENDING_ONLINE = "verify_pending_online"
    MARK_ONLINE = "mark_online"


@dataclass(frozen=True, slots=True)
class DebouncedMountState:
    exists: bool
    target_state: str


@dataclass(frozen=True, slots=True)
class MountStateTransitionPlan:
    action: MountTransitionAction
    target_state: str
    current_state: str | None


def resolve_debounced_mount_state(
    observations: Sequence[bool],
    *,
    window_size: int,
) -> DebouncedMountState | None:
    if window_size <= 0:
        raise ValueError("window_size must be positive")
    if len(observations) < window_size:
        return None

    candidate = observations[0]
    if any(observed != candidate for observed in observations):
        return None
    return DebouncedMountState(
        exists=candidate,
        target_state=HWState.ONLINE if candidate else HWState.OFFLINE,
    )


def plan_mount_state_transition(
    *,
    current_state: str | None,
    target_state: str,
) -> MountStateTransitionPlan:
    if target_state == HWState.OFFLINE:
        if current_state == HWState.PENDING:
            return MountStateTransitionPlan(
                action=MountTransitionAction.NOOP,
                target_state=target_state,
                current_state=current_state,
            )
        return MountStateTransitionPlan(
            action=MountTransitionAction.MARK_PENDING_OFFLINE,
            target_state=target_state,
            current_state=current_state,
        )

    if current_state == HWState.PENDING:
        return MountStateTransitionPlan(
            action=MountTransitionAction.VERIFY_PENDING_ONLINE,
            target_state=target_state,
            current_state=current_state,
        )
    if current_state == HWState.ONLINE:
        return MountStateTransitionPlan(
            action=MountTransitionAction.NOOP,
            target_state=target_state,
            current_state=current_state,
        )
    return MountStateTransitionPlan(
        action=MountTransitionAction.MARK_ONLINE,
        target_state=target_state,
        current_state=current_state,
    )


__all__ = (
    "DebouncedMountState",
    "MountStateTransitionPlan",
    "MountTransitionAction",
    "plan_mount_state_transition",
    "resolve_debounced_mount_state",
)
