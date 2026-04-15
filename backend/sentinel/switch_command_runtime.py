from __future__ import annotations

from dataclasses import dataclass

from backend.kernel.contracts.events_schema import SwitchCommandSignalPayload


@dataclass(frozen=True, slots=True)
class SwitchRuntimeCommand:
    switch_name: str
    state: str


@dataclass(frozen=True, slots=True)
class SwitchRuntimePlan:
    switch_name: str
    state: str
    container_name: str
    clear_runtime_override: bool = False
    runtime_override_state: str | None = None
    container_actions: tuple[str, ...] = ()
    publish_route_meltdown: bool = False


def parse_switch_runtime_command(data: str | bytes | dict[str, object]) -> SwitchRuntimeCommand | None:
    payload = SwitchCommandSignalPayload.from_message(data)
    if payload is None:
        return None
    switch_name = payload.effective_switch_name()
    if not switch_name:
        return None
    return SwitchRuntimeCommand(switch_name=switch_name, state=payload.state)


def plan_switch_runtime_effects(
    command: SwitchRuntimeCommand,
    *,
    container_name: str,
) -> SwitchRuntimePlan:
    state = command.state
    if state in {"ON", "OFF"}:
        return SwitchRuntimePlan(
            switch_name=command.switch_name,
            state=state,
            container_name=container_name,
            clear_runtime_override=True,
        )
    if state == "PAUSE":
        return SwitchRuntimePlan(
            switch_name=command.switch_name,
            state=state,
            container_name=container_name,
            runtime_override_state="OFF",
            container_actions=("stop",),
            publish_route_meltdown=True,
        )
    if state == "RESTART":
        return SwitchRuntimePlan(
            switch_name=command.switch_name,
            state=state,
            container_name=container_name,
            container_actions=("stop", "start"),
        )
    raise ValueError(f"unsupported switch runtime state: {state}")


__all__ = (
    "SwitchRuntimeCommand",
    "SwitchRuntimePlan",
    "parse_switch_runtime_command",
    "plan_switch_runtime_effects",
)
