from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ControlEvent:
    subject: str
    data: str


class ControlEventSubscription(Protocol):
    async def get_message(self, timeout: float | None = None) -> ControlEvent | None: ...

    async def close(self) -> None: ...


class ControlEventBus(Protocol):
    backend_name: str

    async def publish(self, subject: str, payload: str) -> None: ...

    async def subscribe(self, subjects: Sequence[str]) -> ControlEventSubscription: ...

    async def close(self) -> None: ...
