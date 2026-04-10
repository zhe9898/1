from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

try:
    import nats
except ImportError:
    nats = None  # type: ignore[assignment]

from backend.platform.events.channels import is_registered_control_plane_subject
from backend.platform.events.types import ControlEvent, ControlEventSubscription


class NATSEventSubscription(ControlEventSubscription):
    def __init__(self) -> None:
        self._queue: asyncio.Queue[ControlEvent] = asyncio.Queue()
        self._subscriptions: list[Any] = []

    @classmethod
    async def create(cls, client: Any, subjects: Sequence[str]) -> NATSEventSubscription:
        subscription = cls()

        async def _on_message(message: Any) -> None:
            subject = str(getattr(message, "subject", ""))
            raw_data = getattr(message, "data", b"")
            data = raw_data.decode("utf-8", errors="replace") if isinstance(raw_data, bytes) else str(raw_data)
            await subscription._queue.put(ControlEvent(subject=subject, data=data))

        for subject in subjects:
            sub = await client.subscribe(subject, cb=_on_message)
            subscription._subscriptions.append(sub)
        return subscription

    async def get_message(self, timeout: float | None = None) -> ControlEvent | None:
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout or 1.0)
        except TimeoutError:
            return None

    async def close(self) -> None:
        for subscription in self._subscriptions:
            unsubscribe = getattr(subscription, "unsubscribe", None)
            if unsubscribe is None:
                continue
            await unsubscribe()
        self._subscriptions.clear()


class NATSEventBus:
    backend_name = "nats"

    def __init__(self, client: Any) -> None:
        self._client = client

    @classmethod
    async def connect(cls, url: str, *, name: str, connect_timeout: float = 5.0) -> NATSEventBus:
        if nats is None:
            raise RuntimeError("nats-py is not installed")
        client = await nats.connect(url, name=name, connect_timeout=connect_timeout)
        return cls(client)

    async def publish(self, subject: str, payload: str) -> None:
        if not is_registered_control_plane_subject(subject):
            raise ValueError(f"subject is not a registered control-plane event subject: {subject}")
        await self._client.publish(subject, payload.encode("utf-8"))

    async def subscribe(self, subjects: Sequence[str]) -> NATSEventSubscription:
        subject_tuple = tuple(subjects)
        invalid = [subject for subject in subject_tuple if not is_registered_control_plane_subject(subject)]
        if invalid:
            raise ValueError(f"subjects are not registered control-plane event subjects: {invalid}")
        return await NATSEventSubscription.create(self._client, subject_tuple)

    async def close(self) -> None:
        is_closed = getattr(self._client, "is_closed", False)
        if is_closed:
            return
        try:
            drain = getattr(self._client, "drain", None)
            if drain is not None:
                await drain()
        finally:
            close = getattr(self._client, "close", None)
            if close is not None:
                await close()
